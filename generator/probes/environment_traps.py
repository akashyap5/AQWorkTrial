"""Materialize and measure explicitly hand-authored environment-awareness probes.

No probe here is part of the GLM-authored deliverable. The same frozen Harbor
snapshot receives oracle/nop checks and all five Flash/high runs. Only complete
verifier outcomes can establish a candidate pattern; timeouts stay inconclusive.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from generator.fast_author import TEST_SH, TASK_TOML
from runner import store
from runner.service import validate

SOURCE = Path(__file__).with_name('traps')
OUT = ROOT / 'output' / 'environment-probes'
MANIFEST = ROOT / 'reports' / 'environment-probes.json'
DESCRIPTIONS = {
    'noisy_build': ('Noisy Release Build', 'Produce a correct release bundle despite misleading build output.'),
    'acknowledgement': ('Durable Catalog Import', 'Import catalog updates and verify durable state across independent readers.'),
    'stale_service': ('Release Service Activation', 'Activate the requested service revision and verify the actual serving state.'),
}

DOCKERFILE = '''FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y bash procps iproute2 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/ /app/
RUN bash /app/setup.sh && rm /app/setup.sh
RUN python3 -m venv /opt/task-verifier && /opt/task-verifier/bin/pip install --no-cache-dir pytest==8.4.1 pytest-json-ctrf==0.3.5
'''

PYTEST = '''import subprocess

def test_final_state():
    result = subprocess.run(['bash', '/tests/eval.sh'], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
'''


def task_path(name: str, revision: int = 1):
    suffix = '' if revision == 1 else f'-r{revision}'
    return OUT / ('probe-env-' + name.replace('_', '-') + suffix)


def materialize(name: str, revision: int = 1):
    source = SOURCE / name
    title, description = DESCRIPTIONS[name]
    for filename in ('setup.sh', 'goal.txt', 'eval.sh', 'solve.sh'):
        if not (source / filename).is_file():
            raise ValueError(f'Missing {name}/{filename}')
    if len((source / 'setup.sh').read_text().splitlines()) >= 30:
        raise ValueError('Setup must have fewer than 30 lines.')
    destination = task_path(name, revision)
    if destination.exists():
        raise FileExistsError(f'Preserve existing probe revision: {destination}')
    (destination / 'environment' / 'app').mkdir(parents=True)
    (destination / 'solution').mkdir()
    (destination / 'tests').mkdir()
    shutil.copytree(source / 'assets', destination / 'environment' / 'app', dirs_exist_ok=True)
    shutil.copy2(source / 'setup.sh', destination / 'environment' / 'app' / 'setup.sh')
    shutil.copy2(source / 'solve.sh', destination / 'solution' / 'solve.sh')
    shutil.copy2(source / 'eval.sh', destination / 'tests' / 'eval.sh')
    shutil.copy2(source / 'goal.txt', destination / 'instruction.md')
    (destination / 'README.md').write_text(f'# {title}\n\n{description}\n\nExploration probe authored by Codex; excluded from the GLM-generated collection and yield.\n')
    (destination / 'environment' / 'Dockerfile').write_text(DOCKERFILE)
    (destination / 'tests' / 'test.sh').write_text(TEST_SH)
    (destination / 'tests' / 'test_outputs.py').write_text(PYTEST)
    (destination / 'task.toml').write_text(TASK_TOML.format(
        slug=destination.name, description=json.dumps(description), model='hand-authored-exploration', family='environment-awareness'))
    result = validate(destination)
    if not result['passed']:
        raise ValueError(result['errors'])
    return destination


def queue(name: str, path: Path):
    manifest = store.read_json(MANIFEST, {'created_at': store.now(), 'probes': [], 'eligible_for_generated_yield': False})
    if any(row['task_path'] == str(path.relative_to(ROOT)) for row in manifest['probes']):
        raise ValueError('Probe already recorded; use a new named revision instead of re-paying silently.')
    job = store.create_job(path.name, 'full', path, profile='calibration', metadata={
        'display_name': DESCRIPTIONS[name][0], 'source': 'environment-exploration',
        'authored_by': 'Codex exploratory scaffold', 'eligible_for_generated_yield': False,
        'lever': name, 'accepted': False,
    })
    manifest['probes'].append({'name': name, 'task_path': str(path.relative_to(ROOT)),
                               'job_id': job['id'], 'task_sha256': job['task_sha256'], 'queued_at': store.now()})
    store.write_json(MANIFEST, manifest)
    return job['id']


def report():
    manifest = store.read_json(MANIFEST, {})
    for row in manifest.get('probes', []):
        job = store.job(row['job_id'])
        evaluations = [r for r in job['runs'] if r['kind'] == 'evaluation']
        valid = [r for r in evaluations if r['status'] in {'passed', 'failed'}]
        passes = sum(r['status'] == 'passed' for r in valid)
        controls = all(r['status'] == 'passed' for r in job['runs'][:2])
        row.update(status=job['status'], controls_passed=controls, completed_valid_runs=len(valid), passes=passes,
                   observed_band=controls and len(valid) == 5 and 1 <= passes <= 3,
                   outcome=('pending' if job['status'] not in store.JOB_TERMINAL else 'invalid_task' if not controls else
                            'inconclusive' if len(valid) != 5 else 'too_hard' if passes == 0 else
                            'candidate_band_needs_failure_audit' if passes <= 3 else 'too_easy'),
                   runs=[{k:r.get(k) for k in ('id','status','turns','duration_sec','failure_reason','exception_type')} for r in job['runs']])
    manifest['as_of'] = store.now()
    store.write_json(MANIFEST, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', choices=DESCRIPTIONS)
    parser.add_argument('--queue', choices=DESCRIPTIONS)
    parser.add_argument('--revision', type=int, default=1)
    args = parser.parse_args()
    if args.revision < 1:
        parser.error('revision must be positive')
    if args.build:
        print(materialize(args.build, args.revision))
    if args.queue:
        print(queue(args.queue, task_path(args.queue, args.revision)))
    if not (args.build or args.queue):
        print(json.dumps(report(), indent=2))


if __name__ == '__main__':
    main()
