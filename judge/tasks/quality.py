import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from judge.models import Submission, SubmissionQuality
from judge.utils.celery import Progress
from judge.utils.code_quality import CodeQualityError, RUBRIC_VERSION, score_source, source_hash

__all__ = ('score_problem_quality', 'score_submission_quality')

logger = logging.getLogger('judge.code_quality')


def _month_spend_key():
    return 'code_quality_spend:%s' % timezone.now().strftime('%Y-%m')


def _over_budget():
    budget = settings.CODE_QUALITY_MONTHLY_BUDGET_USD
    return budget is not None and cache.get(_month_spend_key(), 0) >= budget * 1e6


def _record_spend(cost):
    # Spend is kept in integer micro-dollars so concurrent workers can add atomically.
    # Calls already in flight can still pass the cap, so it is a soft limit.
    key = _month_spend_key()
    cache.add(key, 0, 40 * 24 * 3600)
    cache.incr(key, int(round(cost * 1e6)))


def _save(submission, digest, scores, confidence, model):
    SubmissionQuality.objects.update_or_create(submission=submission, defaults={
        **scores,
        'confidence': confidence,
        'source_hash': digest,
        'scorer_model': model,
        'rubric_version': RUBRIC_VERSION,
    })


@shared_task(bind=True, ignore_result=True, max_retries=3, default_retry_delay=60, rate_limit='60/m')
def score_submission_quality(self, submission_id):
    """Score an accepted submission's code quality. Failures never touch the submission itself."""
    if not settings.CODE_QUALITY_JEV_API_KEY:
        return 'disabled'
    try:
        submission = Submission.objects.select_related('source', 'language', 'problem').get(id=submission_id)
    except Submission.DoesNotExist:
        return 'missing'
    if not submission.problem.enable_code_quality:
        return 'not-enabled'
    if submission.result != 'AC':
        return 'not-accepted'

    source = submission.source.source
    digest = source_hash(source)
    existing = SubmissionQuality.objects.filter(submission=submission).first()
    if existing and existing.source_hash == digest and existing.rubric_version == RUBRIC_VERSION:
        return 'unchanged'

    # Identical code (resubmissions, copies) reuses an earlier result instead of calling the model again.
    same_code = SubmissionQuality.objects.filter(source_hash=digest, rubric_version=RUBRIC_VERSION) \
                                         .exclude(submission=submission).first()
    if same_code:
        _save(submission, digest, {dim: getattr(same_code, dim) for dim in SubmissionQuality.DIMENSIONS},
              same_code.confidence, same_code.scorer_model)
        return 'reused'

    if len(source.encode('utf-8')) > settings.CODE_QUALITY_MAX_SOURCE_BYTES:
        return 'too-large'
    if _over_budget():
        logger.warning('Code quality budget reached; skipping submission %d', submission_id)
        return 'over-budget'

    # Lock on the code, not the submission: the same code submitted several times in quick succession
    # (or queued by both the bridge and a backfill) calls the API once; the other tasks retry and reuse it.
    lock_key = 'code_quality_lock:%s' % digest
    if not cache.add(lock_key, 1, settings.CODE_QUALITY_JEV_TIMEOUT * 2 + 30):
        raise self.retry(countdown=settings.CODE_QUALITY_JEV_TIMEOUT + 5, max_retries=5)
    try:
        result = score_source(source, submission.language.name)
    except CodeQualityError as e:
        logger.warning('Code quality scoring failed for submission %d', submission_id)
        raise self.retry(exc=e, countdown=60 * 2 ** self.request.retries)
    finally:
        cache.delete(lock_key)

    _record_spend(result['cost'])
    _save(submission, digest, result['scores'], result['confidence'], result['model'])
    return 'scored'


@shared_task(bind=True)
def score_problem_quality(self, problem_id):
    """Queue code-quality scoring for every accepted submission of a problem (backfill)."""
    submission_ids = Submission.objects.filter(problem_id=problem_id, result='AC') \
                                       .exclude(quality__rubric_version=RUBRIC_VERSION).values_list('id', flat=True)
    queued = 0
    with Progress(self, submission_ids.count()) as p:
        for submission_id in submission_ids.iterator():
            score_submission_quality.delay(submission_id)
            queued += 1
            if queued % 10 == 0:
                p.done = queued
    return queued
