#!/usr/bin/env python3
"""Regression checks for pending-batch orchestration safety gates."""
import importlib.util
from pathlib import Path
import tempfile

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
assert source.index('waited_for_crawler = wait_for_crawler()') < source.index('all_pending = pending()')
assert source.count('notify_stage("label"') == 1
assert source.count('notify_stage("select"') == 1
assert source.count('notify_stage("storage"') == 1
assert source.index('labeled, label_failures') < source.index('selected_batches, select_failures')
assert source.index('selected_batches, select_failures') < source.index('ingested, ingest_failures')
with tempfile.TemporaryDirectory() as temporary_dir:
    assert mod.wait_for_crawler(
        Path(temporary_dir) / '.crawler.lock',
        timeout_seconds=0.1,
        poll_interval=0.01,
        settle_seconds=0,
    ) is False

original_run = mod.run
label_outputs = iter([
    '{"status":"failed","candidates":2,"labeled":1,"failed":1,"skipped_valid":0}',
    '{"status":"ok","candidates":1,"labeled":1,"failed":0,"skipped_valid":1}',
])
sleep_calls = []
mod.run = lambda _cmd, allow_fail=False: next(label_outputs)
try:
    label_result, label_history = mod.label_run_with_retries(
        Path('/tmp/test-run'), attempts=2, retry_delay=7,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )
    assert label_result['failed'] == 0
    assert [item['failed'] for item in label_history] == [1, 0]
    assert label_history[1]['skipped_valid'] == 1
    assert sleep_calls == [7]
finally:
    mod.run = original_run

operation = lambda path: (
    (_ for _ in ()).throw(RuntimeError('one article failed'))
    if path.name == 'bad' else {'run_id': path.name, 'selected': 1}
)
processed, failed = mod.run_isolated(
    [Path('/tmp/bad'), Path('/tmp/good')], 'label', operation
)
assert [item['run_id'] for item in processed] == ['good']
assert [item['run_id'] for item in failed] == ['bad']
assert failed[0]['stage'] == 'label'

notifications = []
original_send_pipeline_stage = mod.send_pipeline_stage
mod.send_pipeline_stage = lambda stage, payload: (_ for _ in ()).throw(RuntimeError('webhook down'))
try:
    mod.notify_stage('label', {'batch_count': 1}, notifications)
finally:
    mod.send_pipeline_stage = original_send_pipeline_stage
assert notifications == [{'stage': 'label', 'status': 'failed', 'error': 'webhook down'}]

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
