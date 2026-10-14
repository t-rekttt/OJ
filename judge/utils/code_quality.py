"""Code-quality scoring of accepted submissions with a TypeSafe System One model (Jev).

One request scores all rubric dimensions. The student's code is sent as data in `state`; Jev only
returns numeric scores, so instructions hidden in the code cannot produce free text.
"""

import hashlib
import logging
import math

import requests
from django.conf import settings

logger = logging.getLogger('judge.code_quality')

# Bump when the questions below change, so stored scores can be told apart and rescored.
RUBRIC_VERSION = 1

CONTEXT = ('`code` is an accepted solution on a competitive-programming judge, written by a student. '
           'Judge code quality only; correctness and performance are already verified and must be ignored.')

# Criteria are written per level and literally, because Jev reads them at face value.
QUESTIONS = {
    'naming': {
        'instructions': 'How well do the identifiers in `code` describe their purpose? '
                        'Meaningful non-English words count as descriptive.',
        'criteria': [
            'Almost all identifiers are single letters or meaningless',
            'Mostly cryptic names; purpose rarely clear from names',
            'Mix of clear and cryptic names; typical short contest names',
            'Most names describe their purpose',
            'All names clearly describe their purpose',
        ],
    },
    'structure': {
        'instructions': 'How well is `code` decomposed for its size?',
        'criteria': [
            'Disorganized; logic tangled or duplicated across the file',
            'A non-trivial algorithm crammed into main',
            'Acceptable: a tiny program kept in main, or simple split',
            'Clean helper functions or structs for the main pieces',
            'Excellent decomposition; each function/struct has one clear job',
        ],
    },
    'clarity': {
        'instructions': 'How easy is `code` to read and follow?',
        'criteria': [
            'Very hard to follow: confusing tricks, broken indentation',
            'Hard to follow in several places',
            'Followable with some effort',
            'Easy to follow; consistent formatting',
            'Very easy to follow; consistent formatting and helpful comments where logic is non-obvious',
        ],
    },
    'hygiene': {
        'instructions': 'How clean is `code` of dead or commented-out code, unused includes/macros/variables, '
                        'magic numbers, duplication and template boilerplate? '
                        'Fast-IO lines and a few useful aliases are fine.',
        'criteria': [
            'Heavy: lots of unused macros/includes, dead code or duplication',
            'Several hygiene problems',
            'A few minor problems',
            'Nearly clean; one small issue',
            'Clean; nothing unused, no dead code, no magic numbers',
        ],
    },
}


class CodeQualityError(Exception):
    pass


def normalize_source(source):
    """Drop whitespace that cannot change a score, so trivial resubmissions share one cached result.

    Line endings, trailing spaces/tabs and blank lines at either end are ignored; indentation is kept.
    """
    lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return '\n'.join(line.rstrip() for line in lines).strip('\n')


def source_hash(source):
    return hashlib.sha256(normalize_source(source).encode('utf-8')).hexdigest()


def _bounded(value, low, high):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('non-finite value')
    return min(high, max(low, value))


def score_source(source, language_name):
    """Score `source` on every rubric dimension.

    Returns {'scores': {dim: 1.0-5.0}, 'confidence': {dim: 0-1}, 'model': str, 'cost': float}.
    Raises CodeQualityError on any API failure; the caller decides whether to retry.
    """
    body = {
        'model': settings.CODE_QUALITY_JEV_MODEL,
        'state': {'context': CONTEXT, 'language': language_name, 'code': source},
        'questions': {name: {'type': 'score', **question} for name, question in QUESTIONS.items()},
    }
    try:
        response = requests.post(
            settings.CODE_QUALITY_JEV_URL.rstrip('/') + '/v1/systemone',
            json=body,
            headers={'Authorization': 'Bearer %s' % settings.CODE_QUALITY_JEV_API_KEY},
            timeout=settings.CODE_QUALITY_JEV_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        answers = data['answers']
        return {
            # Jev scores levels 0-4 as a probability-weighted expectation; the rubric shows 1-5.
            'scores': {name: _bounded(answers[name]['score'], 0, 4) + 1 for name in QUESTIONS},
            'confidence': {name: _bounded(answers[name]['confidence'], 0, 1) for name in QUESTIONS},
            'model': str(data.get('model') or settings.CODE_QUALITY_JEV_MODEL)[:64],
            'cost': max(0.0, float((data.get('usage') or {}).get('cost') or 0.0)),
        }
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as e:
        # Never log the request body: it contains the student's source.
        raise CodeQualityError('Jev request failed: %s' % type(e).__name__) from e
