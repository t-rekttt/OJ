from unittest import mock

from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from judge.models import Language, Submission, SubmissionQuality
from judge.models.problem import SubmissionSourceAccess
from judge.models.tests.util import create_problem, create_user
from judge.utils.code_quality import RUBRIC_VERSION


class SubmissionQualityViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user(username='quality_owner')
        cls.other = create_user(username='quality_other')
        cls.problem = create_problem(code='quality_view', is_public=True, enable_code_quality=True,
                                     submission_source_visibility_mode=SubmissionSourceAccess.ONLY_OWN)
        cls.submission = Submission.objects.create(user=cls.owner.profile, problem=cls.problem,
                                                   language=Language.get_python3(), result='AC', status='D')

    def status_page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('submission_status', args=[self.submission.id]))

    def add_quality(self, confidence=0.9):
        SubmissionQuality.objects.create(
            submission=self.submission, naming=2.0, structure=3.5, clarity=4.0, hygiene=5.0,
            confidence={'naming': confidence, 'structure': 0.9, 'clarity': 0.9, 'hygiene': 0.9},
            source_hash='0' * 64, scorer_model='jev-test', rubric_version=RUBRIC_VERSION,
        )

    def test_owner_sees_scores_and_total(self):
        self.add_quality()
        response = self.status_page(self.owner)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="code-quality"')
        self.assertContains(response, '14.5 / 20')
        self.assertContains(response, 'width: 25.0%')  # naming 2.0 on a 1-5 bar
        self.assertNotContains(response, 'quality-row quality-uncertain')
        self.assertNotContains(response, 'class="quality-legend"')

    def test_low_confidence_dimension_is_marked(self):
        self.add_quality(confidence=0.2)
        response = self.status_page(self.owner)
        self.assertContains(response, 'quality-row quality-uncertain', count=1)
        self.assertContains(response, 'class="quality-legend"')

    def test_hidden_after_rejudge_to_non_accepted(self):
        self.add_quality()
        Submission.objects.filter(id=self.submission.id).update(result='WA')
        self.assertNotContains(self.status_page(self.owner), 'id="code-quality"')

    def test_no_block_without_scores(self):
        self.assertNotContains(self.status_page(self.owner), 'id="code-quality"')

    def test_scores_follow_submission_access(self):
        self.add_quality()
        response = self.status_page(self.other)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="code-quality"', status_code=response.status_code)

    def test_vietnamese_labels(self):
        self.add_quality()
        self.client.force_login(self.owner)
        response = self.client.get(reverse('submission_status', args=[self.submission.id]), HTTP_ACCEPT_LANGUAGE='vi')
        self.assertContains(response, 'Chất lượng mã')
        self.assertContains(response, 'Đặt tên')


class ScoreQualityBackfillViewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = create_user(username='quality_manager', is_staff=True, is_superuser=True)
        cls.limited = create_user(username='quality_limited', is_staff=True,
                                  user_permissions=('rejudge_submission', 'edit_all_problem'))
        cls.on = create_problem(code='quality_bf_on', enable_code_quality=True)
        cls.off = create_problem(code='quality_bf_off')

    def post(self, user, problem):
        self.client.force_login(user)
        return self.client.post(reverse('problem_submissions_quality', args=[problem.code]))

    @mock.patch('judge.views.problem_manage.score_problem_quality')
    def test_refused_when_problem_not_opted_in(self, task):
        self.assertEqual(self.post(self.manager, self.off).status_code, 404)
        task.delay.assert_not_called()

    @mock.patch('judge.views.problem_manage.score_problem_quality')
    def test_requires_bulk_rejudge_permission(self, task):
        self.assertEqual(self.post(self.limited, self.on).status_code, 403)
        task.delay.assert_not_called()

    @mock.patch('judge.views.problem_manage.redirect_to_task_status', return_value=HttpResponse('ok'))
    @mock.patch('judge.views.problem_manage.score_problem_quality')
    def test_manager_can_start_backfill(self, task, redirect):
        task.delay.return_value.id = 'task-id'
        self.assertEqual(self.post(self.manager, self.on).status_code, 200)
        task.delay.assert_called_once_with(self.on.id)
