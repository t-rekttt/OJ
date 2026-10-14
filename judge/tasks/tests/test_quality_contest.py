from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.models import Language, Submission, SubmissionQuality, SubmissionSource
from judge.models.tests.util import create_contest, create_problem, create_user
from judge.tasks.quality import score_contest_quality, score_problem_quality, score_submission_quality
from judge.tasks.tests.test_quality import jev_response
from judge.utils.code_quality import RUBRIC_VERSION


def make_submission(user, problem, contest=None, result='AC', source='print(1)\n'):
    submission = Submission.objects.create(user=user, problem=problem, language=Language.get_python3(),
                                           result=result, status='D', contest_object=contest)
    SubmissionSource.objects.create(submission=submission, source=source)
    return submission


def add_quality(submission):
    return SubmissionQuality.objects.create(submission=submission, naming=3, structure=3, clarity=3, hygiene=3,
                                            confidence={}, source_hash='0' * 64, scorer_model='m',
                                            rubric_version=RUBRIC_VERSION)


class ContestEnableRuleTestCase(TestCase):
    """Contest submissions follow the contest's switch; practice submissions follow the problem's."""

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='cq_rule_user').profile
        cls.problem_on = create_problem(code='cq_rule_p_on', enable_code_quality=True)
        cls.problem_off = create_problem(code='cq_rule_p_off')
        cls.contest_on = create_contest(key='cq_rule_c_on', enable_code_quality=True)
        cls.contest_off = create_contest(key='cq_rule_c_off')

    def test_rule(self):
        cases = [
            (self.problem_on, None, True),
            (self.problem_off, None, False),
            (self.problem_off, self.contest_on, True),    # contest turns it on
            (self.problem_on, self.contest_off, False),   # contest turns it off
        ]
        for problem, contest, expected in cases:
            submission = make_submission(self.user, problem, contest)
            self.assertIs(SubmissionQuality.is_enabled_for(submission), expected, (problem.code, contest))

    @override_settings(CODE_QUALITY_JEV_API_KEY='test-key', CODE_QUALITY_MONTHLY_BUDGET_USD=None)
    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_task_follows_contest_switch(self, post):
        self.assertEqual(score_submission_quality(make_submission(self.user, self.problem_on, self.contest_off).id),
                         'not-enabled')
        self.assertEqual(score_submission_quality(make_submission(self.user, self.problem_off, self.contest_on).id),
                         'scored')
        self.assertEqual(post.call_count, 1)

    @override_settings(CODE_QUALITY_JEV_API_KEY='test-key')
    @mock.patch('judge.bridge.judge_handler.score_submission_quality')
    def test_bridge_follows_contest_switch(self, task):
        from judge.bridge.judge_handler import JudgeHandler
        handler = mock.Mock(spec=JudgeHandler)
        JudgeHandler._queue_code_quality(handler, make_submission(self.user, self.problem_on, self.contest_off))
        task.apply_async.assert_not_called()
        JudgeHandler._queue_code_quality(handler, make_submission(self.user, self.problem_off, self.contest_on))
        task.apply_async.assert_called_once()


class ContestBackfillTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='cq_bf_user').profile
        cls.problem = create_problem(code='cq_bf_p', enable_code_quality=True)
        cls.contest = create_contest(key='cq_bf_c', enable_code_quality=True)

    @mock.patch('judge.tasks.quality.score_submission_quality')
    def test_contest_backfill_queues_only_its_unscored_ac(self, task):
        in_contest = make_submission(self.user, self.problem, self.contest)
        add_quality(make_submission(self.user, self.problem, self.contest))
        make_submission(self.user, self.problem, self.contest, result='WA')
        make_submission(self.user, self.problem)
        self.assertEqual(score_contest_quality.apply(args=(self.contest.id,)).get(), 1)
        task.delay.assert_called_once_with(in_contest.id)

    @mock.patch('judge.tasks.quality.score_submission_quality')
    def test_problem_backfill_skips_contest_submissions(self, task):
        practice = make_submission(self.user, self.problem)
        make_submission(self.user, self.problem, self.contest)
        self.assertEqual(score_problem_quality.apply(args=(self.problem.id,)).get(), 1)
        task.delay.assert_called_once_with(practice.id)


class ContestBackfillViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Contest authors can edit only with the edit_own_contest permission.
        cls.editor = create_user(username='cq_view_editor', user_permissions=('edit_own_contest',))
        cls.other = create_user(username='cq_view_other')
        cls.on = create_contest(key='cq_view_on', enable_code_quality=True, authors=('cq_view_editor',))
        cls.off = create_contest(key='cq_view_off', authors=('cq_view_editor',))

    def post(self, user, contest):
        self.client.force_login(user)
        return self.client.post(reverse('contest_score_quality', args=[contest.key]))

    @mock.patch('judge.views.contests.score_contest_quality')
    def test_editor_starts_backfill(self, task):
        task.delay.return_value.id = 'task-id'
        response = self.post(self.editor, self.on)
        self.assertEqual(response.status_code, 302)
        task.delay.assert_called_once_with(self.on.id)

    @mock.patch('judge.views.contests.score_contest_quality')
    def test_refused_for_non_editor_and_disabled_contest(self, task):
        # ContestMixin renders access errors as a message page rather than a redirect to the task.
        self.assertNotEqual(self.post(self.other, self.on).status_code, 302)
        self.assertContains(self.post(self.editor, self.off), 'No such contest')
        task.delay.assert_not_called()


class ContestScoreVisibilityTestCase(TestCase):
    """Scores of contest submissions stay hidden from contestants until the contest ends."""

    @classmethod
    def setUpTestData(cls):
        cls.contestant = create_user(username='cq_vis_contestant')
        cls.editor = create_user(username='cq_vis_editor', user_permissions=('edit_own_contest',))
        cls.problem = create_problem(code='cq_vis_p', is_public=True)
        cls.contest = create_contest(key='cq_vis_c', enable_code_quality=True, authors=('cq_vis_editor',))
        cls.submission = make_submission(cls.contestant.profile, cls.problem, cls.contest)
        add_quality(cls.submission)

    def status_page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('submission_status', args=[self.submission.id]))

    def test_hidden_from_contestant_while_running(self):
        response = self.status_page(self.contestant)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="code-quality"')

    def test_visible_to_contest_editor_while_running(self):
        self.assertContains(self.status_page(self.editor), 'id="code-quality"')

    def test_visible_to_contestant_after_contest_ends(self):
        type(self.contest).objects.filter(id=self.contest.id).update(
            end_time=timezone.now() - timezone.timedelta(minutes=1))
        self.assertContains(self.status_page(self.contestant), 'id="code-quality"')
