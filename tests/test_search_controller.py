"""Search validity, novelty, and failure-evidence gates without paid calls."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from generator import generate
from phase2 import controller, diversity, llm, prompts
from runner import store


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fixture-key')
    monkeypatch.setenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
    monkeypatch.setenv('TASKLAB_PROMPTS_DIR', str(tmp_path / 'prompts'))
    monkeypatch.setenv('TASKLAB_AUTHOR_TIMEOUT_SEC', '180')
    monkeypatch.setattr(controller, 'DATA', tmp_path / 'phase2')
    monkeypatch.setattr(store, 'DATA', tmp_path / 'runner')


def batch(**kwargs):
    return controller.start_batch(profile='search', briefs=[{
        'id': 'graph-repair', 'family': 'dependency-planning', 'topic': 'Repair dependency invalidation.'}],
        metadata={'search_id': 'fixture-search'}, defer_update=True, **kwargs)


def author(topic, output_dir, **kwargs):
    path = Path(output_dir)
    path.mkdir(parents=True)
    (path / 'instruction.md').write_text('Repair the graph and invalidate all transitive consumers after dependency changes.')
    (path / 'README.md').write_text('# Artifact Dependency Repair\n\nRepair incremental build invalidation.')
    return {'status': 'structurally_validated', 'task_dir': str(path), 'cost_usd': .04,
            'name': 'artifact-dependency-repair', 'display_name': 'Artifact Dependency Repair',
            'description': 'Repair incremental build invalidation.', 'prompt_version': kwargs['prompt_version']}


def review(verdict='pass'):
    return {'verdict': verdict, 'issues': [], 'summary': 'Original and valid.',
            'solution_pattern': 'Dependency closure and content-based invalidation.',
            'novelty': {'verdict': 'duplicate' if verdict == 'duplicate' else 'pass', 'reason': 'Different state invariant.'},
            'call': {'status': 'completed', 'cost_usd': .01}}


def setup_task(monkeypatch, statuses=None, audit=None, review_result=None):
    statuses = statuses or ['passed', 'passed', 'failed', 'failed', 'failed']
    generator = Mock(side_effect=author)
    monkeypatch.setattr(generate, 'generate_task', generator)
    reviewer = Mock(return_value=review_result or review())
    monkeypatch.setattr(controller, 'semantic_review', reviewer)
    original_create = store.create_job
    queue = Mock(side_effect=original_create)
    monkeypatch.setattr(store, 'create_job', queue)
    def finish(record, task, job_id):
        job_path = store.directory(job_id)
        for run_id, status in zip(['oracle', 'nop'] + [f'eval-{i}' for i in range(1, 6)], ['passed', 'passed'] + statuses):
            path = job_path / 'runs' / run_id / 'state.json'
            state = store.read_json(path)
            state.update(status=status, tests=[{'name': 'test_transitive', 'status': 'failed' if status == 'failed' else 'passed',
                                               'message': 'Consumer remained stale.' if status == 'failed' else ''}])
            store.write_json(path, state)
        saved = store.read_json(job_path / 'job.json')
        saved.update(status='completed' if all(s in {'passed', 'failed'} for s in statuses) else 'error',
                     controls_passed=True, validation={'passed': True})
        store.write_json(job_path / 'job.json', saved)
        return store.job(job_id)
    monkeypatch.setattr(controller, '_wait_job', finish)
    auditor = Mock(return_value=audit or {'accepted': True, 'verdict': 'valid_model_failures', 'failures': [],
                                         'task_defects': [], 'call': {'status': 'completed', 'cost_usd': .02}})
    monkeypatch.setattr(controller, 'failure_audit', auditor)
    return generator, reviewer, queue, auditor


def test_search_batch_freezes_stage_allowances_and_archive():
    archive = [{'id': 'previous', 'family': 'streaming-protocol', 'description': 'Decode frames.'}]
    record = batch(archive=archive)
    archive[0]['description'] = 'changed'
    assert record['archive'][0]['description'] == 'Decode frames.'
    assert record['profile'] == 'search' and record['defer_update']
    assert record['config']['authoring']['generation_cost_usd'] == record['config']['search']['generation_cost_usd']
    assert record['config']['authoring']['semantic_review_cost_usd'] == record['config']['search']['semantic_review_cost_usd']
    assert record['config']['authoring']['failure_audit_cost_usd'] == record['config']['search']['failure_audit_cost_usd']
    assert record['tasks'][0]['family'] == 'dependency-planning'


def test_search_uses_readable_name_diversity_context_and_audited_acceptance(monkeypatch):
    record = batch(archive=[{'id': 'previous', 'family': 'storage', 'description': 'Snapshot recovery.'}])
    generator, reviewer, queue, auditor = setup_task(monkeypatch)
    task = record['tasks'][0]
    controller.process_task(record, task)
    assert generator.call_args.kwargs['diversity_context'][0]['id'] == 'previous'
    assert reviewer.call_args.kwargs['archive'][0]['id'] == 'previous'
    assert queue.call_args.kwargs['profile'] == 'search'
    metadata = queue.call_args.kwargs['metadata']
    assert metadata['display_name'] == 'Artifact Dependency Repair'
    assert metadata['search_id'] == 'fixture-search' and metadata['family'] == 'dependency-planning'
    assert task['accepted'] is True and task['summary']['learnable'] is True
    assert store.job(task['job_id'])['metadata']['accepted'] is True
    assert task['failure_audit_cost_usd'] == .02
    assert task['accounted_cost_usd'] == pytest.approx(.07)
    assert auditor.call_count == 1


def test_exact_duplicate_is_rejected_before_semantic_review_or_solver(monkeypatch, tmp_path):
    first = author('', tmp_path / 'prior', prompt_version='fixture')
    fingerprint = diversity.fingerprint_task(first['task_dir'])
    record = batch(archive=[{'id': 'earlier', 'fingerprint': fingerprint}])
    _, reviewer, queue, auditor = setup_task(monkeypatch)
    controller.process_task(record, record['tasks'][0])
    assert record['tasks'][0]['status'] == 'duplicate'
    reviewer.assert_not_called()
    queue.assert_not_called()
    auditor.assert_not_called()


def test_semantic_duplicate_is_rejected_before_solver(monkeypatch):
    record = batch()
    _, _, queue, auditor = setup_task(monkeypatch, review_result=review('duplicate'))
    controller.process_task(record, record['tasks'][0])
    assert record['tasks'][0]['status'] == 'duplicate'
    assert not record['tasks'][0]['accepted']
    queue.assert_not_called()
    auditor.assert_not_called()


@pytest.mark.parametrize('last,expected', [('budget_exhausted', 'budget_exhausted'), ('error', 'error'), ('timeout', 'error')])
def test_search_budget_stops_are_inconclusive_but_infra_errors_stop_search(monkeypatch, last, expected):
    record = batch()
    _, _, _, auditor = setup_task(monkeypatch, statuses=['passed', 'failed', 'failed', 'budget_exhausted', last])
    controller.process_task(record, record['tasks'][0])
    task = record['tasks'][0]
    assert task['status'] == expected and task['summary']['learnable'] is None
    assert not task['accepted']
    auditor.assert_not_called()


def test_postevaluation_task_defect_does_not_trigger_regeneration(monkeypatch):
    record = batch()
    generator, _, queue, _ = setup_task(monkeypatch, audit={
        'accepted': False, 'verdict': 'task_defect', 'task_defects': ['Ambiguous transitive rule'],
        'call': {'status': 'completed', 'cost_usd': .03}})
    controller.process_task(record, record['tasks'][0])
    assert record['tasks'][0]['status'] == 'invalid' and not record['tasks'][0]['accepted']
    assert generator.call_count == queue.call_count == 1


def test_audit_provider_error_stops_search_without_claiming_acceptance(monkeypatch):
    record = batch()
    setup_task(monkeypatch, audit={'accepted': False, 'verdict': 'inconclusive', 'stop_reason': 'api_error',
                                   'call': {'status': 'api_error', 'cost_usd': .03}})
    controller.process_task(record, record['tasks'][0])
    assert record['tasks'][0]['status'] == 'error' and not record['tasks'][0]['accepted']


def test_deferred_round_does_not_revise_prompt_and_uses_600s_author_timeout(monkeypatch):
    record = batch()
    monkeypatch.setattr(controller, 'process_task', lambda record, task: task.update(status='invalid'))
    updater = Mock()
    monkeypatch.setattr(controller, 'propose_update', updater)
    controller.process_batch(record)
    assert record['status'] == 'checkpoint' and record['decision'] == 'update_deferred'
    assert llm.author_timeout_sec() == 600
    assert record['metrics']['canonical_learnable_tasks'] == 0
    updater.assert_not_called()


def test_optimizer_can_revise_incumbent_without_rewriting_generation_provenance(monkeypatch):
    record = batch()
    original = copy.deepcopy(record['source_prompt'])
    incumbent = prompts.create_version(original['text'] + '\nPreserve an established algorithmic invariant.\n', original['version'])
    record['update_source_prompt'] = incumbent
    updater = Mock(return_value={'status': 'completed', 'cost_usd': .01, 'data': {
        'action': 'revise', 'prompt': incumbent['text'] + '\nVary state transitions across fresh task families.\n', 'rationale': 'New evidence.'}})
    monkeypatch.setattr(llm, 'budgeted_json_call', updater)
    controller.propose_update(record)
    payload = json.loads(updater.call_args.args[0][1]['content'])
    assert payload['current_prompt']['version'] == incumbent['version']
    assert payload['generation_prompt']['version'] == original['version']
    assert 'search:600s' in payload['evaluation_profile']
    assert record['source_prompt'] == original and record['prompt_version'] == original['version']
    assert record['candidate_prompt']['parent_version'] == incumbent['version']


def test_failure_audit_requires_specific_evidence_for_every_failed_run(monkeypatch, tmp_path):
    (tmp_path / 'instruction.md').write_text('Invalidate all transitive consumers.')
    feedback = {'runs': [{'id': 'eval-1', 'kind': 'evaluation', 'status': 'passed'},
                         {'id': 'eval-2', 'kind': 'evaluation', 'status': 'failed'},
                         {'id': 'eval-3', 'kind': 'evaluation', 'status': 'failed'}]}
    row = {'run_id': 'eval-2', 'reason': 'Only invalidates immediate consumers.', 'contract_evidence': 'instruction.md: transitive consumers',
           'test_evidence': 'test_transitive: stale artifact', 'trajectory_evidence': 'Agent describes one-hop invalidation.'}
    call = Mock(return_value={'status': 'completed', 'cost_usd': .01, 'data': {
        'verdict': 'valid_model_failures', 'task_defects': [], 'failures': [row]}})
    monkeypatch.setattr(llm, 'budgeted_json_call', call)
    result = controller.failure_audit(tmp_path, feedback, tmp_path / 'audit', .20)
    assert result['accepted'] is False
    call.return_value['data']['failures'].append({**row, 'run_id': 'eval-3'})
    call.return_value['data']['verdict'] = 'valid_model_failures'
    result = controller.failure_audit(tmp_path, feedback, tmp_path / 'audit', .20)
    assert result['accepted'] is True


def test_interrupted_audit_is_not_replayed(monkeypatch):
    record = batch()
    _, _, _, auditor = setup_task(monkeypatch)
    task = record['tasks'][0]
    controller.process_task(record, task)
    attempt = task['attempts'][0]
    attempt.pop('failure_audit')
    attempt.pop('terminal_status')
    attempt['finished'] = False
    task['status'] = 'auditing'
    auditor.reset_mock()
    with pytest.raises(RuntimeError, match='Interrupted failure-audit'):
        controller.process_task(record, task)
    auditor.assert_not_called()


def test_search_authors_overlap_and_each_paid_start_marker_is_durable(monkeypatch):
    import threading
    record = controller.start_batch(profile='search', defer_update=True)
    barrier = threading.Barrier(3)
    observed = []
    lock = threading.Lock()
    def concurrent_author(topic, output_dir, **kwargs):
        task_id = Path(output_dir).parent.parent.name
        saved = store.read_json(controller._path(record['id']) / 'batch.json')
        task = next(t for t in saved['tasks'] if t['id'] == task_id)
        assert task['status'] == 'generating'
        assert task['attempts'][0]['finished'] is False
        with lock:
            observed.append(task_id)
        barrier.wait(timeout=3)
        return {'status': 'api_error', 'task_dir': output_dir, 'cost_usd': .01, 'errors': ['fixture failure']}
    monkeypatch.setattr(generate, 'generate_task', concurrent_author)
    controller.process_batch(record)
    saved = store.read_json(controller._path(record['id']) / 'batch.json')
    assert len(set(observed)) == 3
    assert all(t['status'] == 'error' and len(t['attempts']) == 1 for t in saved['tasks'])
    assert saved['accounted_cost_usd'] == pytest.approx(.03)
    assert saved['status'] == 'checkpoint'


def test_search_exception_waits_for_other_inflight_results_before_checkpoint(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    record = controller.start_batch(profile='search', defer_update=True)
    barrier = threading.Barrier(3)
    release = threading.Event()
    error_saved = threading.Event()
    original_save = controller._save
    def observed_save(record):
        original_save(record)
        if record.get('task_errors'):
            error_saved.set()
    monkeypatch.setattr(controller, '_save', observed_save)
    first_id = record['tasks'][0]['id']
    def task_work(record, task):
        barrier.wait(timeout=3)
        if task['id'] == first_id:
            raise RuntimeError('Interrupted author response; no paid replay.')
        assert release.wait(timeout=3)
        task.update(status='completed', generation_cost_usd=.02)
    monkeypatch.setattr(controller, 'process_task', task_work)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(controller.process_batch, record)
        try:
            assert error_saved.wait(timeout=3)
            assert not future.done()
            assert store.read_json(controller._path(record['id']) / 'batch.json')['status'] == 'running'
        finally:
            release.set()
        future.result(timeout=3)
    saved = store.read_json(controller._path(record['id']) / 'batch.json')
    assert saved['status'] == 'checkpoint'
    assert saved['tasks'][0]['status'] == 'error'
    assert all(t['status'] == 'completed' for t in saved['tasks'][1:])
    assert saved['accounted_cost_usd'] == pytest.approx(.04)


def test_parallel_restart_does_not_replay_an_interrupted_author_call(monkeypatch):
    record = controller.start_batch(profile='search', defer_update=True)
    task = record['tasks'][0]
    task.update(status='generating', attempts=[{'number': 0, 'finished': False}])
    for other in record['tasks'][1:]:
        other['status'] = 'completed'
    author_call = Mock()
    monkeypatch.setattr(generate, 'generate_task', author_call)
    controller.process_batch(record)
    assert task['status'] == 'error' and 'no automatic paid retry' in task['error']
    author_call.assert_not_called()


def test_new_sibling_duplicate_is_checked_again_after_review(monkeypatch):
    record = controller.start_batch(profile='search', defer_update=True)
    _, reviewer, queue, _ = setup_task(monkeypatch)
    task, sibling = record['tasks'][:2]
    def review_while_sibling_finishes(*args, **kwargs):
        sibling['fingerprint'] = copy.deepcopy(task['fingerprint'])
        return review()
    reviewer.side_effect = review_while_sibling_finishes
    controller.process_task(record, task)
    assert task['status'] == 'duplicate'
    assert task['attempts'][0]['pre_submission_duplicate_check']['duplicate']
    queue.assert_not_called()


def test_batch_save_serializes_snapshots_across_threads(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    record = controller.start_batch(profile='search', defer_update=True)
    original_write = store.write_json
    active = 0
    peak = 0
    counter = threading.Lock()
    barrier = threading.Barrier(3)
    def guarded_write(path, value):
        nonlocal active, peak
        with counter:
            active += 1
            peak = max(peak, active)
        try:
            assert value is not record
            original_write(path, value)
        finally:
            with counter:
                active -= 1
    monkeypatch.setattr(store, 'write_json', guarded_write)
    def save_task(task):
        barrier.wait(timeout=3)
        for n in range(10):
            task['generation_cost_usd'] = (n + 1) / 1000
            task[f'field_{n}'] = {'evidence': [n, task['id']]}
            controller._save(record)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(save_task, record['tasks']))
    saved = store.read_json(controller._path(record['id']) / 'batch.json')
    assert peak == 1
    assert saved['accounted_cost_usd'] == pytest.approx(.03)
    assert all(t['field_9']['evidence'][0] == 9 for t in saved['tasks'])


def truncated_review(cost=.01):
    return {'status': 'invalid_response', 'finish_reason': 'length', 'cost_usd': cost, 'data': None,
            'accounting': {'source': 'provider_usage', 'usage': {'completion_tokens': 32768,
                'completion_tokens_details': {'reasoning_tokens': 32760}}}}


def test_semantic_review_retries_known_reasoning_truncation_with_evidence(monkeypatch, tmp_path):
    (tmp_path / 'instruction.md').write_text('Restore dependency closure.')
    successful = {'status': 'completed', 'cost_usd': .02, 'data': {'verdict': 'pass', 'issues': [], 'summary': 'Valid.'}}
    caller = Mock(side_effect=[truncated_review(), successful])
    monkeypatch.setattr(llm, 'budgeted_json_call', caller)
    steps = []
    result = controller.semantic_review(tmp_path, tmp_path / 'review', .5, on_attempt=lambda row: steps.append(copy.deepcopy(row)))
    assert result['verdict'] == 'pass' and result['call']['cost_usd'] == pytest.approx(.03)
    first, fallback = caller.call_args_list
    assert first.kwargs['max_tokens'] == 32768
    assert first.kwargs['reasoning_enabled'] is False
    assert fallback.kwargs['reasoning_enabled'] is False
    assert fallback.kwargs['budget_usd'] == pytest.approx(.49)
    assert first.kwargs['evidence_dir'] != fallback.kwargs['evidence_dir']
    assert [row['status'] for row in steps] == ['started', 'invalid_response', 'started', 'completed']


@pytest.mark.parametrize('response', [
    {'status': 'api_error', 'finish_reason': None, 'cost_usd': .04},
    {'status': 'invalid_response', 'finish_reason': 'stop', 'cost_usd': .04},
    {'status': 'invalid_response', 'finish_reason': 'length', 'cost_usd': .04,
     'accounting': {'source': 'reserved_unknown_usage'}},
])
def test_semantic_review_never_retries_ambiguous_or_nontruncated_failure(monkeypatch, tmp_path, response):
    (tmp_path / 'instruction.md').write_text('Restore dependency closure.')
    caller = Mock(return_value=response)
    monkeypatch.setattr(llm, 'budgeted_json_call', caller)
    controller.semantic_review(tmp_path, tmp_path / 'review', .5)
    assert caller.call_count == 1


def retained_task(record, *, saved_review=None):
    task = record['tasks'][0]
    work = controller._path(record['id']) / 'tasks' / task['id'] / 'attempt-0'
    generation = author(task['topic'], work / 'task', prompt_version=record['prompt_version'])
    task.update(status='reviewing', generation_cost_usd=.04,
                attempts=[{'number': 0, 'directory': str(work), 'finished': False, 'generation': generation}])
    if saved_review:
        task['attempts'][0]['semantic_review'] = saved_review
    controller._save(record)
    return task, work


def test_resume_reuses_valid_bundle_after_legacy_review_truncation(monkeypatch):
    record = batch()
    task, work = retained_task(record, saved_review={'verdict': 'error', 'issues': [], 'stop_reason': 'invalid_response'})
    old = truncated_review()
    store.write_json(work / 'review/semantic-review-result.json', old)
    task['review_cost_usd'] = old['cost_usd']
    generator, reviewer, queue, _ = setup_task(monkeypatch)
    controller.process_task(record, task)
    generator.assert_not_called()
    assert reviewer.call_args.args[1] == work / 'review-2'
    assert queue.call_count == 1 and task['accepted']
    assert task['attempts'][0]['review_attempts'][0]['legacy'] is True
    assert task['review_cost_usd'] == pytest.approx(.02)


def test_resume_reuses_passed_review_without_another_model_call(monkeypatch):
    record = batch()
    task, _ = retained_task(record, saved_review={k: v for k, v in review().items() if k != 'call'})
    generator, reviewer, queue, _ = setup_task(monkeypatch)
    controller.process_task(record, task)
    generator.assert_not_called()
    reviewer.assert_not_called()
    assert queue.call_count == 1 and task['accepted']


def test_resume_does_not_repeat_unresolved_review_request(monkeypatch):
    record = batch()
    task, work = retained_task(record)
    store.write_json(work / 'review/semantic-review-request.json', {'request': 'admitted'})
    generator, reviewer, queue, _ = setup_task(monkeypatch)
    with pytest.raises(RuntimeError, match='Interrupted semantic-review'):
        controller.process_task(record, task)
    generator.assert_not_called()
    reviewer.assert_not_called()
    queue.assert_not_called()


def test_per_task_repair_override_allows_real_repair_after_operational_attempts(monkeypatch):
    record = batch()
    task, work = retained_task(record)
    task['max_task_repairs'] = 4
    task['attempts'] = [copy.deepcopy(task['attempts'][0]) for _ in range(4)]
    for number, attempt in enumerate(task['attempts']):
        attempt.update(number=number, finished=True)
    task['feedback'] = {'repair_instruction': 'Fix the actual failed oracle assertion.'}
    generator, _, _, _ = setup_task(monkeypatch)
    controller.process_task(record, task)
    assert generator.call_count == 1 and len(task['attempts']) == 5
    assert generator.call_args.kwargs['repair_feedback']['previous_feedback']['repair_instruction']


def test_execution_feedback_requires_exact_current_bundle_provenance(monkeypatch):
    record = batch()
    task, work = retained_task(record)
    current_hash = controller._task_sha256(work / 'task')
    task['precheck_feedback'] = {'job_id': 'old-fixture', 'task_sha256': 'old-version',
                                'runs': [{'failed_tests': ['test_old_contract']}]}
    _, reviewer, _, _ = setup_task(monkeypatch)
    controller.process_task(record, task)
    assert reviewer.call_args.kwargs['execution_feedback'] is None
    assert task['precheck_feedback']['task_sha256'] == 'old-version'
    task['precheck_feedback']['task_sha256'] = current_hash
    assert controller._current_execution_feedback(task, work / 'task')['task_sha256'] == current_hash
    (work / 'task' / 'instruction.md').write_text('A new revision with a new contract.')
    assert controller._current_execution_feedback(task, work / 'task') is None


def test_unversioned_execution_feedback_is_not_current_truth(monkeypatch):
    record = batch()
    task, work = retained_task(record)
    task['precheck_feedback'] = {'runs': [{'failed_tests': ['unattributed old result']}]}
    assert controller._current_execution_feedback(task, work / 'task') is None


def test_job_feedback_exposes_snapshot_hash_and_job_id(monkeypatch):
    record = batch()
    _, _, _, _ = setup_task(monkeypatch, statuses=['passed'] * 5)
    task = record['tasks'][0]
    controller.process_task(record, task)
    job = store.job(task['job_id'])
    feedback = controller._job_feedback(job)
    assert feedback['job_id'] == job['id']
    assert feedback['task_sha256'] == job['task_sha256']


def test_explicit_review_invalidation_preserves_evidence_and_reuses_task(monkeypatch):
    record = batch()
    old_review = {'verdict': 'reject', 'issues': [{'evidence': 'Incorrectly cited old test'}], 'summary': 'Stale oracle feedback.'}
    task, work = retained_task(record, saved_review=old_review)
    task['status'] = 'invalid'
    task['review_cost_usd'] = .03
    attempt = task['attempts'][0]
    attempt.update(finished=True, terminal_status='invalid')
    prior = {'status': 'completed', 'cost_usd': .03, 'data': old_review}
    store.write_json(work / 'review/semantic-review-result.json', prior)
    before_hash = controller._task_sha256(work / 'task')
    controller.invalidate_semantic_review(record, task['id'], 'The review used an older task snapshot as current evidence.')
    assert task['status'] == 'reviewing'
    assert attempt['review_invalidations'][0]['previous_review'] == old_review
    assert attempt['review_invalidations'][0]['task_sha256'] == before_hash
    assert attempt['review_epoch'] == 1 and not attempt['finished']
    assert store.read_json(work / 'review/semantic-review-result.json') == prior
    generator, reviewer, queue, _ = setup_task(monkeypatch)
    controller.process_task(record, task)
    generator.assert_not_called()
    assert reviewer.call_args.args[1] == work / 'review-2'
    assert queue.call_count == 1 and task['accepted']
    assert task['review_cost_usd'] == pytest.approx(.04)
    assert controller._task_sha256(work / 'task') == before_hash


def test_review_invalidation_refuses_unresolved_request(monkeypatch):
    record = batch()
    task, work = retained_task(record)
    store.write_json(work / 'review/semantic-review-request.json', {'admitted': True})
    with pytest.raises(RuntimeError, match='unresolved'):
        controller.invalidate_semantic_review(record, task['id'], 'Cannot silently replay an unknown request.')


def test_optimizer_uses_nonthinking_glm_with_room_for_full_prompt(monkeypatch):
    record = batch()
    call = Mock(return_value={'status': 'completed', 'cost_usd': .01, 'data': {
        'action': 'keep', 'prompt': record['source_prompt']['text'], 'rationale': 'Insufficient evidence.'}})
    monkeypatch.setattr(llm, 'budgeted_json_call', call)
    controller.propose_update(record)
    assert call.call_args.kwargs['reasoning_enabled'] is False
    assert call.call_args.kwargs['max_tokens'] == 16384
