#!/usr/bin/env python3
"""Regression checks for pending-batch orchestration safety gates."""
import importlib.util
from pathlib import Path

ROOT = Path('/root/workspace/wx-crawl')
SCRIPT = ROOT / 'skill/wechat-pipeline-orchestration/scripts/process_pending_batches.py'
spec = importlib.util.spec_from_file_location('pending', SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.RECORD == ROOT / 'results/record'
assert not hasattr(mod, 'RECORD_ROOT')
source = SCRIPT.read_text(encoding='utf-8')
assert '"--summaries"' not in source
assert 'Agent-side per-article summary' in source
run_dir = ROOT / 'results/record/2026_08_15_01_13_13'
original_status_json = mod.status_json
mod.status_json = lambda _: {'pending_label_count': 1}
try:
    try:
        mod.require_labels(run_dir)
    except RuntimeError as exc:
        assert 'still need label.json' in str(exc)
    else:
        raise AssertionError('unlabeled batch was not blocked')
finally:
    mod.status_json = original_status_json
print('pending-batch safety regression checks passed')
