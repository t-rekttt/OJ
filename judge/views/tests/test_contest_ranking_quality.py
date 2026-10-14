import re
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.contest_format.registry import formats
from judge.models import ContestParticipation, ContestProblem, ContestSubmission, Language, Submission, \
    SubmissionQuality, SubmissionTestCase
from judge.models.tests.util import create_contest, create_problem, create_user
from judge.tasks.quality import rate_contest_quality
from judge.utils.code_quality import RUBRIC_VERSION


def ended_contest(key, **kwargs):
    now = timezone.now()
    return create_contest(key=key, start_time=now - timezone.timedelta(days=2),
                          end_time=now - timezone.timedelta(days=1), is_visible=True, **kwargs)


class ContestRankingQualityTestCase(TestCase):
    """Code-quality scores appear in every contest format's scoreboard, without affecting points or order."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = create_user(username='rq_alice')
        cls.bob = create_user(username='rq_bob')
        cls.editor = create_user(username='rq_editor', user_permissions=('edit_own_contest',))
        cls.problem = create_problem(code='rq_problem', is_public=True)

    def build(self, key, format_name='default', enable=True, ended=True):
        if ended:
            contest = ended_contest(key, format_name=format_name, enable_code_quality=enable, authors=('rq_editor',))
        else:
            contest = create_contest(key=key, format_name=format_name, enable_code_quality=enable,
                                     authors=('rq_editor',), is_visible=True)
        contest_problem = ContestProblem.objects.create(contest=contest, problem=self.problem, points=100, order=1)
        start = contest.start_time + timezone.timedelta(minutes=1)
        for user in (self.alice, self.bob):
            ContestParticipation.objects.create(contest=contest, user=user.profile, real_start=start, virtual=0)
        return contest, contest_problem

    def submit(self, contest, contest_problem, user, minutes, quality=None, result='AC'):
        participation = ContestParticipation.objects.get(contest=contest, user=user.profile, virtual=0)
        submission = Submission.objects.create(user=user.profile, problem=self.problem,
                                               language=Language.get_python3(), result=result, status='D',
                                               points=100 if result == 'AC' else 0, case_points=1, case_total=1,
                                               time=0.1, memory=100, contest_object=contest)
        submitted_at = contest.start_time + timezone.timedelta(minutes=minutes)
        Submission.objects.filter(id=submission.id).update(date=submitted_at)
        SubmissionTestCase.objects.create(submission=submission, case=1, status=result, time=0.1, memory=100,
                                          points=1 if result == 'AC' else 0, total=1)
        ContestSubmission.objects.create(submission=submission, problem=contest_problem, participation=participation,
                                         points=100 if result == 'AC' else 0)
        if quality is not None:
            SubmissionQuality.objects.create(submission=submission, naming=quality, structure=quality,
                                             clarity=quality, hygiene=quality, confidence={}, source_hash=str(minutes),
                                             scorer_model='m', rubric_version=RUBRIC_VERSION)
        participation.recompute_results()
        return submission

    def ranking(self, contest, user=None):
        self.client.force_login(user or self.alice)
        response = self.client.get(reverse('contest_ranking', args=[contest.key]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def row(self, html, username):
        match = re.search(r'<tr id="user-%s".*?</tr>' % username, html, re.S)
        self.assertIsNotNone(match, username)
        return match.group(0)

    def test_every_format_shows_latest_accepted_score(self):
        self.assertEqual(len(formats), 7)  # guard: the loop below must cover every registered format
        for key in formats:
            with self.subTest(format=key):
                contest, cp = self.build('rq_fmt_%s' % key, format_name=key)
                self.submit(contest, cp, self.alice, 10, quality=2.0)   # earlier AC: 8.0
                self.submit(contest, cp, self.alice, 20, quality=4.0)   # latest AC: 16.0, shown
                self.submit(contest, cp, self.alice, 30, result='WA')   # later non-AC ignored
                html = self.ranking(contest, self.alice)
                alice = self.row(html, 'rq_alice')
                self.assertIn('class="quality-score"', alice)
                self.assertIn('16.0', alice)
                self.assertIn('Code quality: 16.0', alice)
                self.assertNotIn('8.0', alice)
                self.assertIn('<td class="quality-total"', alice)
                self.assertIn('th class="quality-total header"', html)
                self.assertIn('>Code quality</th>', html)

    def test_contestant_without_scores_gets_empty_quality_cells(self):
        contest, cp = self.build('rq_empty')
        self.submit(contest, cp, self.alice, 10, quality=3.0)
        self.submit(contest, cp, self.bob, 10)  # accepted but not scored
        bob = self.row(self.ranking(contest), 'rq_bob')
        self.assertNotIn('class="quality-score"', bob)
        self.assertIn('<td class="quality-total"></td>', bob)

    def test_quality_never_changes_points_or_order(self):
        contest, cp = self.build('rq_order')
        self.submit(contest, cp, self.alice, 50, quality=1.0)   # slower solve, poor quality
        self.submit(contest, cp, self.bob, 10, quality=5.0)     # faster solve, great quality
        participations = {p.user.username: p for p in ContestParticipation.objects.filter(contest=contest)}
        self.assertEqual(participations['rq_alice'].score, participations['rq_bob'].score)
        html = self.ranking(contest)
        # Order is decided by points/time only: bob (faster) first regardless of quality.
        self.assertLess(html.index('id="user-rq_bob"'), html.index('id="user-rq_alice"'))
        self.assertIn('100', self.row(html, 'rq_alice'))

    def test_hidden_when_contest_switch_off(self):
        contest, cp = self.build('rq_off', enable=False)
        self.submit(contest, cp, self.alice, 10, quality=4.0)
        html = self.ranking(contest)
        self.assertNotIn('quality-score', html.split('<tbody', 1)[-1])
        self.assertNotIn('th class="quality-total', html)

    def test_running_contest_hidden_from_contestants_shown_to_editors(self):
        contest, cp = self.build('rq_live', ended=False)
        self.submit(contest, cp, self.alice, 10, quality=4.0)
        self.assertNotIn('th class="quality-total', self.ranking(contest, self.alice))
        self.assertIn('th class="quality-total', self.ranking(contest, self.editor))


class AdminRateCodeQualityTestCase(ContestRankingQualityTestCase):
    """The admin "Rate code quality" button scores the latest accepted submission per contestant and problem."""

    @mock.patch('judge.tasks.quality.score_submission_quality')
    def test_queues_only_latest_unscored_accepted(self, task):
        contest, cp = self.build('rq_rate')
        self.submit(contest, cp, self.alice, 10)                       # superseded by the later AC
        latest_alice = self.submit(contest, cp, self.alice, 20)
        self.submit(contest, cp, self.alice, 30, result='WA')          # not accepted
        self.submit(contest, cp, self.bob, 15, quality=3.0)            # latest, already scored
        self.assertEqual(rate_contest_quality.apply(args=(contest.id,)).get(), 1)
        task.delay.assert_called_once_with(latest_alice.id)

    @mock.patch('judge.tasks.rate_contest_quality')
    def test_admin_button_permissions(self, task):
        task.delay.return_value.id = 'task-id'
        contest, _cp = self.build('rq_rate_perm')
        off, _cp = self.build('rq_rate_off', enable=False)
        url = lambda c: reverse('admin:judge_contest_rate_code_quality', args=[c.id])  # noqa: E731

        self.client.force_login(self.alice)          # not staff / cannot edit
        self.assertNotEqual(self.client.get(url(contest)).status_code, 302)
        task.delay.assert_not_called()

        admin = create_user(username='rq_admin', is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(url(off)).status_code, 404)
        self.assertEqual(self.client.get(url(contest)).status_code, 302)
        task.delay.assert_called_once_with(contest.id)

        # The button is the link to this contest's rate URL (the class name also appears in the page script).
        page = self.client.get(reverse('admin:judge_contest_change', args=[contest.id])).content.decode()
        self.assertIn('href="%s"' % url(contest), page)
        off_page = self.client.get(reverse('admin:judge_contest_change', args=[off.id])).content.decode()
        self.assertNotIn('href="%s"' % url(off), off_page)
