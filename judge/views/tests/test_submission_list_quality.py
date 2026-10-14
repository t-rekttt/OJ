import re

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import Language, Submission, SubmissionQuality
from judge.models.tests.util import create_contest, create_problem, create_user
from judge.utils.code_quality import RUBRIC_VERSION


class SubmissionListQualityTestCase(TestCase):
    """The submissions list shows the code-quality score under each visible accepted submission's points."""

    @classmethod
    def setUpTestData(cls):
        cls.student = create_user(username='lq_student')
        cls.editor = create_user(username='lq_editor', user_permissions=('edit_own_contest',))
        cls.problem = create_problem(code='lq_problem', is_public=True)

    def submit(self, quality=None, result='AC', contest=None):
        submission = Submission.objects.create(user=self.student.profile, problem=self.problem,
                                               language=Language.get_python3(), result=result, status='D',
                                               case_points=1, case_total=1, time=0.1, memory=100,
                                               contest_object=contest)
        if quality is not None:
            SubmissionQuality.objects.create(submission=submission, naming=quality, structure=quality,
                                             clarity=quality, hygiene=quality, confidence={},
                                             source_hash=str(submission.id), scorer_model='m',
                                             rubric_version=RUBRIC_VERSION)
        return submission

    def row(self, html, submission):
        match = re.search(r'<div class="submission-row" id="%d">.*?</div>\s*</div>\s*</div>' % submission.id,
                          html, re.S)
        self.assertIsNotNone(match, submission.id)
        return match.group(0)

    def list_page(self, user=None):
        self.client.force_login(user or self.student)
        response = self.client.get(reverse('all_submissions'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_badge_on_scored_accepted_only(self):
        scored = self.submit(quality=4.0)
        unscored = self.submit()
        rejudged = self.submit(quality=3.0, result='WA')   # stale score after rejudge to WA stays hidden
        html = self.list_page()
        self.assertIn('CQ 16.0', self.row(html, scored))
        self.assertNotIn('quality-score', self.row(html, unscored))
        self.assertNotIn('quality-score', self.row(html, rejudged))

    def test_running_contest_hidden_except_editors(self):
        contest = create_contest(key='lq_live', authors=('lq_editor',), is_visible=True)
        live = self.submit(quality=4.0, contest=contest)
        self.assertNotIn('quality-score', self.row(self.list_page(self.student), live))
        self.assertIn('CQ 16.0', self.row(self.list_page(self.editor), live))

        type(contest).objects.filter(id=contest.id).update(end_time=timezone.now() - timezone.timedelta(minutes=1))
        self.assertIn('CQ 16.0', self.row(self.list_page(self.student), live))

    def test_scores_loaded_in_one_query_per_page(self):
        for _ in range(5):
            self.submit(quality=3.0)
        self.client.force_login(self.student)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('all_submissions'))
        quality_queries = [q for q in queries.captured_queries if 'judge_submissionquality' in q['sql']]
        self.assertEqual(len(quality_queries), 1)

    def test_live_row_refresh_includes_badge(self):
        scored = self.submit(quality=2.0)
        self.client.force_login(self.student)
        response = self.client.get(reverse('submission_single_query'), {'id': scored.id})
        self.assertContains(response, 'CQ 8.0')

    def test_vietnamese_badge(self):
        scored = self.submit(quality=4.0)
        self.client.force_login(self.student)
        html = self.client.get(reverse('all_submissions'), HTTP_ACCEPT_LANGUAGE='vi').content.decode()
        self.assertIn('CLC 16.0', self.row(html, scored))
