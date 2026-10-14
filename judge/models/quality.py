from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.submission import Submission

__all__ = ['SubmissionQuality']


class SubmissionQuality(models.Model):
    """Code-quality scores of an accepted submission. Informational only: never affects points or verdict."""

    DIMENSIONS = ('naming', 'structure', 'clarity', 'hygiene')

    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name='quality',
                                      verbose_name=_('submission'))
    # Each score is the model's expected rubric level on a 1-5 scale.
    naming = models.FloatField(verbose_name=_('naming'))
    structure = models.FloatField(verbose_name=_('structure'))
    clarity = models.FloatField(verbose_name=_('clarity'))
    hygiene = models.FloatField(verbose_name=_('hygiene'))
    confidence = models.JSONField(verbose_name=_('confidence per dimension'))
    source_hash = models.CharField(max_length=64, db_index=True, verbose_name=_('source SHA-256'))
    scorer_model = models.CharField(max_length=64, verbose_name=_('scoring model'))
    rubric_version = models.PositiveSmallIntegerField(verbose_name=_('rubric version'))
    created = models.DateTimeField(auto_now_add=True, verbose_name=_('created'))

    class Meta:
        verbose_name = _('submission code quality')
        verbose_name_plural = _('submission code qualities')

    @staticmethod
    def is_enabled_for(submission):
        """Contest submissions follow the contest's switch; practice submissions follow the problem's."""
        if submission.contest_object_id is not None:
            return submission.contest_object.enable_code_quality
        return submission.problem.enable_code_quality

    @staticmethod
    def is_visible_to(submission, user):
        """Whether `user` may see this submission's score (assumes they can already see the submission).

        Scores belong to accepted code: a rejudge to another verdict hides them until it is AC again.
        Like checker feedback, contest scores stay hidden while the contest runs, except to its editors.
        """
        if submission.result != 'AC':
            return False
        contest = submission.contest_object
        return contest is None or contest.ended or contest.is_editable_by(user)

    @property
    def total(self):
        return sum(getattr(self, dim) for dim in self.DIMENSIONS)

    def __str__(self):
        return _('Code quality of %(submission)s') % {'submission': self.submission}
