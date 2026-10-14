"""Code-quality scores on contest scoreboards.

Informational only: these scores never feed into points, ranking order or tie-breaks.
Each contest format renders its own problem cells; the quality score is appended inside whatever cell the
format produced, so every format (current and future) gets it without format-specific code.
"""

from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, pgettext

from judge.models import SubmissionQuality


def can_see_quality_column(contest, user):
    """Scores are shown once the contest has ended, or earlier to people who can edit the contest."""
    return contest.enable_code_quality and (contest.ended or contest.is_editable_by(user))


def latest_accepted_quality(contest, participation_ids):
    """Map (participation_id, contest_problem_id) -> total score of the latest accepted, scored submission.

    One query for the whole scoreboard. "Latest" is by submission time, so a resubmission replaces earlier code.
    """
    rows = SubmissionQuality.objects.filter(
        submission__contest__participation_id__in=participation_ids,
        submission__contest_object=contest,
        submission__result='AC',
    ).order_by('submission__date').values_list(
        'submission__contest__participation_id', 'submission__contest__problem_id',
        'naming', 'structure', 'clarity', 'hygiene',
    )
    scores = {}
    for participation_id, problem_id, *dims in rows:
        # Later rows overwrite earlier ones, leaving the latest submission per cell.
        scores[(participation_id, problem_id)] = sum(dims)
    return scores


def add_quality_to_cell(cell, total):
    """Insert the quality score just before the format's closing </td>; leave the cell untouched if absent."""
    if total is None or not cell.endswith('</td>'):
        return cell
    # Short label to fit the cell; the column header and the tooltip spell out "Code quality".
    badge = format_html('<div class="quality-score" title="{title}">{label} {total}</div>',
                        title=_('Code quality (informational, does not affect points)'),
                        label=pgettext('code quality abbreviation', 'CQ'), total='%.1f' % total)
    return mark_safe(cell[:-len('</td>')] + badge + '</td>')


def quality_total_cell(totals):
    """Total column: average quality over the problems that have a score, out of 20."""
    if not totals:
        return mark_safe('<td class="quality-total"></td>')
    return format_html('<td class="quality-total" title="{title}">{average}</td>',
                       title=_('Average code quality over %(count)d problem(s), out of 20') % {'count': len(totals)},
                       average='%.1f' % (sum(totals) / len(totals)))
