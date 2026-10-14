from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from judge.models import Language, Problem, Submission, SubmissionQuality, SubmissionSource, SubmissionTestCase
from judge.models.tests.util import create_problem, create_user
from judge.tasks.quality import score_problem_quality, score_submission_quality
from judge.utils.code_quality import CodeQualityError, QUESTIONS, RUBRIC_VERSION, normalize_source, score_source, \
    source_hash

SOURCE = 'n = int(input())\nprint(n * 2)\n'


def jev_response(score=2.5, confidence=0.7, cost=0.00004):
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'model': 'typesafe/jev-1.13-20260917',
        'answers': {name: {'type': 'score', 'score': score, 'confidence': confidence} for name in QUESTIONS},
        'usage': {'input_tokens': 900, 'output_tokens': 18, 'cost': cost},
    }
    return response


@override_settings(CODE_QUALITY_JEV_API_KEY='test-key', CODE_QUALITY_JEV_URL='https://jev.test',
                   CODE_QUALITY_MONTHLY_BUDGET_USD=None)
class ScoreSourceTestCase(TestCase):
    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response(score=2.5))
    def test_maps_levels_to_one_to_five(self, post):
        result = score_source(SOURCE, 'Python 3')
        self.assertEqual(result['scores'], {name: 3.5 for name in QUESTIONS})
        self.assertEqual(result['confidence'], {name: 0.7 for name in QUESTIONS})
        self.assertEqual(result['cost'], 0.00004)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_sends_code_as_state_and_one_question_per_dimension(self, post):
        score_source(SOURCE, 'Python 3')
        url, = post.call_args.args
        body = post.call_args.kwargs['json']
        self.assertEqual(url, 'https://jev.test/v1/systemone')
        self.assertEqual(body['state']['code'], SOURCE)
        self.assertEqual(set(body['questions']), set(QUESTIONS))
        self.assertTrue(all(q['type'] == 'score' for q in body['questions'].values()))

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response(score=7.0, confidence=1.5))
    def test_out_of_range_values_are_clamped(self, post):
        result = score_source(SOURCE, 'Python 3')
        self.assertEqual(set(result['scores'].values()), {5.0})
        self.assertEqual(set(result['confidence'].values()), {1.0})

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response(score=float('nan')))
    def test_non_finite_score_is_an_api_error(self, post):
        with self.assertRaises(CodeQualityError):
            score_source(SOURCE, 'Python 3')

    @mock.patch('judge.utils.code_quality.requests.post')
    def test_null_usage_is_tolerated(self, post):
        post.return_value = jev_response()
        post.return_value.json.return_value['usage'] = None
        self.assertEqual(score_source(SOURCE, 'Python 3')['cost'], 0.0)

    def test_normalize_source_keeps_meaningful_whitespace(self):
        self.assertEqual(normalize_source('a  \r\n\r\n    b\t\r\n\n'), 'a\n\n    b')
        self.assertNotEqual(normalize_source('if x:\n    y'), normalize_source('if x:\ny'))

    @mock.patch('judge.utils.code_quality.requests.post', side_effect=requests.ConnectionError('secret source'))
    def test_error_message_does_not_leak_details(self, post):
        with self.assertRaises(CodeQualityError) as context:
            score_source(SOURCE, 'Python 3')
        self.assertNotIn('secret source', str(context.exception))


@override_settings(CODE_QUALITY_JEV_API_KEY='test-key', CODE_QUALITY_JEV_URL='https://jev.test',
                   CODE_QUALITY_MONTHLY_BUDGET_USD=None, CODE_QUALITY_MAX_SOURCE_BYTES=20000)
class ScoreSubmissionQualityTaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='quality_user').profile
        cls.problem = create_problem(code='quality', enable_code_quality=True)

    def setUp(self):
        cache.clear()

    def make_submission(self, source=SOURCE, result='AC'):
        submission = Submission.objects.create(user=self.user, problem=self.problem,
                                               language=Language.get_python3(), result=result, status='D')
        SubmissionSource.objects.create(submission=submission, source=source)
        return submission

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response(score=3.0))
    def test_scores_accepted_submission(self, post):
        submission = self.make_submission()
        self.assertEqual(score_submission_quality(submission.id), 'scored')
        quality = SubmissionQuality.objects.get(submission=submission)
        self.assertEqual(quality.naming, 4.0)
        self.assertEqual(quality.total, 16.0)
        self.assertEqual(quality.source_hash, source_hash(SOURCE))
        self.assertEqual(quality.rubric_version, RUBRIC_VERSION)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_skips_non_accepted_and_unchanged(self, post):
        self.assertEqual(score_submission_quality(self.make_submission(result='WA').id), 'not-accepted')
        submission = self.make_submission()
        score_submission_quality(submission.id)
        self.assertEqual(score_submission_quality(submission.id), 'unchanged')
        self.assertEqual(post.call_count, 1)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_identical_source_reuses_scores_without_api_call(self, post):
        score_submission_quality(self.make_submission().id)
        second = self.make_submission()
        self.assertEqual(score_submission_quality(second.id), 'reused')
        self.assertEqual(post.call_count, 1)
        self.assertTrue(SubmissionQuality.objects.filter(submission=second).exists())

    @mock.patch('judge.utils.code_quality.requests.post')
    def test_disabled_without_key_and_skips_large_source(self, post):
        with self.settings(CODE_QUALITY_JEV_API_KEY=None):
            self.assertEqual(score_submission_quality(self.make_submission().id), 'disabled')
        with self.settings(CODE_QUALITY_MAX_SOURCE_BYTES=10):
            self.assertEqual(score_submission_quality(self.make_submission(source='x' * 11).id), 'too-large')
        post.assert_not_called()

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response(cost=1.0))
    def test_monthly_budget_stops_new_calls(self, post):
        with self.settings(CODE_QUALITY_MONTHLY_BUDGET_USD=1.0):
            self.assertEqual(score_submission_quality(self.make_submission().id), 'scored')
            self.assertEqual(score_submission_quality(self.make_submission(source='print(1)').id), 'over-budget')
        self.assertEqual(post.call_count, 1)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_skips_problem_with_flag_off(self, post):
        submission = self.make_submission()
        Problem.objects.filter(id=self.problem.id).update(enable_code_quality=False)
        self.assertEqual(score_submission_quality(submission.id), 'not-enabled')
        post.assert_not_called()

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_same_code_in_flight_waits_then_reuses(self, post):
        first, second = self.make_submission(), self.make_submission()
        cache.add('code_quality_lock:%s' % source_hash(SOURCE), 1, 60)
        with mock.patch.object(score_submission_quality, 'retry', side_effect=RuntimeError('retry')) as retry:
            with self.assertRaises(RuntimeError):
                score_submission_quality(second.id)
        retry.assert_called_once()
        post.assert_not_called()

        # The first task finishes and releases the lock; the retried task then reuses its result.
        cache.delete('code_quality_lock:%s' % source_hash(SOURCE))
        self.assertEqual(score_submission_quality(first.id), 'scored')
        self.assertEqual(score_submission_quality(second.id), 'reused')
        self.assertEqual(post.call_count, 1)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_whitespace_only_resubmission_reuses_scores(self, post):
        score_submission_quality(self.make_submission().id)
        for variant in (SOURCE.replace('\n', '\r\n'), SOURCE.replace('\n', '  \n'), '\n' + SOURCE + '\n\n'):
            self.assertEqual(score_submission_quality(self.make_submission(source=variant).id), 'reused')
        self.assertEqual(post.call_count, 1)

    @mock.patch('judge.utils.code_quality.requests.post', return_value=jev_response())
    def test_indentation_change_is_scored_again(self, post):
        score_submission_quality(self.make_submission().id)
        reindented = SOURCE.replace('print', '    print')
        self.assertEqual(score_submission_quality(self.make_submission(source=reindented).id), 'scored')
        self.assertEqual(post.call_count, 2)

    @mock.patch('judge.utils.code_quality.requests.post', side_effect=requests.Timeout())
    def test_api_failure_retries_and_leaves_submission_untouched(self, post):
        submission = self.make_submission()
        with mock.patch.object(score_submission_quality, 'retry', side_effect=RuntimeError('retry')) as retry:
            with self.assertRaises(RuntimeError):
                score_submission_quality(submission.id)
        retry.assert_called_once()
        submission.refresh_from_db()
        self.assertEqual(submission.result, 'AC')
        self.assertFalse(SubmissionQuality.objects.filter(submission=submission).exists())


@override_settings(CODE_QUALITY_JEV_API_KEY='test-key')
class QueueCodeQualityTestCase(TestCase):
    """The bridge hook: queue only accepted submissions on opted-in problems, and never raise."""

    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='quality_bridge_user').profile
        cls.enabled = create_problem(code='quality_on', enable_code_quality=True)
        cls.disabled = create_problem(code='quality_off')

    def queue(self, problem, result):
        from judge.bridge.judge_handler import JudgeHandler
        submission = Submission.objects.create(user=self.user, problem=problem, language=Language.get_python3(),
                                               result=result, status='D')
        JudgeHandler._queue_code_quality(mock.Mock(spec=JudgeHandler), submission)
        return submission

    @mock.patch('judge.bridge.judge_handler.score_submission_quality')
    def test_queues_only_accepted_on_enabled_problem(self, task):
        submission = self.queue(self.enabled, 'AC')
        self.queue(self.enabled, 'WA')
        self.queue(self.disabled, 'AC')
        task.apply_async.assert_called_once()
        self.assertEqual(task.apply_async.call_args.args, ((submission.id,),))
        self.assertFalse(task.apply_async.call_args.kwargs['retry'])

    @mock.patch('judge.bridge.judge_handler.score_submission_quality')
    def test_does_nothing_without_api_key(self, task):
        with self.settings(CODE_QUALITY_JEV_API_KEY=None):
            self.queue(self.enabled, 'AC')
        task.apply_async.assert_not_called()

    @mock.patch('judge.bridge.judge_handler.score_submission_quality')
    def test_queue_failure_does_not_raise(self, task):
        task.app.connection_for_write.side_effect = ConnectionError('broker down')
        self.queue(self.enabled, 'AC')


class ScoreProblemQualityTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user(username='quality_backfill_user').profile
        cls.problem = create_problem(code='quality_backfill', enable_code_quality=True)

    @mock.patch('judge.tasks.quality.score_submission_quality')
    def test_queues_every_accepted_submission(self, task):
        accepted = [Submission.objects.create(user=self.user, problem=self.problem, language=Language.get_python3(),
                                              result=result, status='D').id for result in ('AC', 'WA', 'AC')]
        self.assertEqual(score_problem_quality.apply(args=(self.problem.id,)).get(), 2)
        queued = sorted(call.args[0] for call in task.delay.call_args_list)
        self.assertEqual(queued, [accepted[0], accepted[2]])

    @mock.patch('judge.tasks.quality.score_submission_quality')
    def test_skips_already_scored_submissions(self, task):
        scored, unscored = (Submission.objects.create(user=self.user, problem=self.problem,
                                                      language=Language.get_python3(), result='AC', status='D')
                            for _ in range(2))
        SubmissionQuality.objects.create(submission=scored, naming=3, structure=3, clarity=3, hygiene=3,
                                         confidence={}, source_hash='0' * 64, scorer_model='m',
                                         rubric_version=RUBRIC_VERSION)
        self.assertEqual(score_problem_quality.apply(args=(self.problem.id,)).get(), 1)
        task.delay.assert_called_once_with(unscored.id)


@override_settings(CODE_QUALITY_JEV_API_KEY='test-key')
class GradingEndOrderTestCase(TestCase):
    """The verdict and the live grading-end update must not wait on the Celery broker."""

    def test_queue_runs_after_live_update(self):
        from judge.bridge.judge_handler import JudgeHandler
        user = create_user(username='quality_order_user').profile
        problem = create_problem(code='quality_order', enable_code_quality=True)
        submission = Submission.objects.create(user=user, problem=problem, language=Language.get_python3(),
                                               status='G')
        SubmissionTestCase.objects.create(submission=submission, case=1, status='AC', time=0.1, memory=10,
                                          points=1, total=1)
        calls = []
        handler = mock.Mock(spec=JudgeHandler)
        handler.name = 'judge-test'
        handler._post_update_submission.side_effect = lambda *a, **k: calls.append('live-update')
        handler._queue_code_quality.side_effect = lambda s: calls.append('queue:%s' % s.result)
        with mock.patch('judge.bridge.judge_handler.event'):
            JudgeHandler.on_grading_end(handler, {'submission-id': submission.id})
        self.assertEqual(calls, ['live-update', 'queue:AC'])
