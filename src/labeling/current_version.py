"""Reject stale or unverified decisions before sync and reminder side effects."""
from pathlib import Path

from .schema import read_label, validate_payload
from .eligibility import geography_eligible
from src.crawler.content import read_json, article_url_key

ROOT = Path(__file__).resolve().parents[2] / 'results/articles'


def _matches(label, row):
    if not isinstance(label, dict) or validate_payload(label) or label.get('decision') != 'KEEP' or not geography_eligible(label):
        return False
    fields = {key: label.get(key) for key in ('application_type', 'domains', 'summary')}
    deadline = label.get('deadline') or {}
    importance = label.get('importance') or {}
    fields.update(deadline_status=deadline.get('status'), deadline_at=deadline.get('timestamp'),
                  deadline_text=deadline.get('raw_text'), importance_level=importance.get('level'),
                  importance_reason=importance.get('reason'), importance_factors=importance.get('factors'))
    return all(row.get(key) == value for key, value in fields.items() if key in row)


def require_current_selection(rows, root=ROOT):
    labels = {}
    for directory in root.glob('*/*'):
        if not directory.is_dir():
            continue
        meta = {**read_json(directory/'data.json'), **read_json(directory/'metadata.json')}
        key = article_url_key(meta.get('url', ''))
        if key:
            labels.setdefault(key, []).append(directory/'label.json')
    invalid = []
    for row in rows:
        paths = labels.get(article_url_key(row.get('url', '')), [])
        # Existing archives take precedence: a new DROP/REVIEW must invalidate a stored KEEP.
        if paths:
            valid = all(not errors and _matches(label, row)
                        for label, errors in (read_label(path) for path in paths))
        else:
            valid = _matches(row.get('label'), row)
        if not valid:
            invalid.append(row.get('id'))
    if invalid:
        raise RuntimeError(f'current label verification required: {len(invalid)} records, ids={invalid}; relabel and reconcile database first')
