"""Records, resume, and concurrency safety for the batch generator (generator/fast_author.py).

* Ledger: output/ledger.jsonl is an append-only record of every pipeline event (one JSON object per line).
* Verdicts: each task directory gets verdict.json explaining its decision from measured evidence.
* Batch state: output/batches/<batch-id>/state.json records each slot's stage, slug, and probe job, so a killed
  batch resumes where it stopped (`--resume <batch-id>|latest`) without re-running finished authoring or probes.
* Concurrency: slugs are reserved with an atomic mkdir, every slot is processed under a non-blocking per-slot
  lock, and shared files are written under fcntl locks with atomic replaces.
"""
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "output" / "ledger.jsonl"
BATCHES = ROOT / "output" / "batches"
DONE_STATES = {"done"}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=1, default=str))
    os.replace(tmp, path)


def ledger(event, **fields):
    """Append one event to the shared ledger (safe across threads and processes)."""
    entry = {"ts": now(), "pid": os.getpid(), "event": event, **fields}
    line = json.dumps(entry, default=str) + "\n"
    with file_lock(LEDGER.with_suffix(".lock")):
        with LEDGER.open("a") as handle:
            handle.write(line)


def reserve_dir(base, names):
    """Atomically create the first free directory among names; returns its name."""
    base.mkdir(parents=True, exist_ok=True)
    for name in names:
        try:
            (base / name).mkdir()
            return name
        except FileExistsError:
            continue
    raise RuntimeError(f"no free name under {base} among {names[:3]}...")


class Batch:
    """Durable per-batch state with per-slot claims."""

    def __init__(self, batch_id):
        self.id = batch_id
        self.dir = BATCHES / batch_id
        self.path = self.dir / "state.json"
        self.lock = self.dir / ".state.lock"
        self._claims = {}

    @classmethod
    def create(cls, args, slots):
        BATCHES.mkdir(parents=True, exist_ok=True)
        batch_id = reserve_dir(BATCHES, [f"batch-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{k}" for k in range(50)])
        batch = cls(batch_id)
        write_json_atomic(batch.path, {"id": batch_id, "created": now(), "args": args, "slots": slots})
        ledger("batch_started", batch=batch_id, args=args, slots=len(slots))
        return batch

    @classmethod
    def open(cls, batch_id):
        if batch_id == "latest":
            states = sorted(BATCHES.glob("*/state.json"), key=lambda p: p.stat().st_mtime)
            if not states:
                raise SystemExit("no batch to resume")
            batch_id = states[-1].parent.name
        batch = cls(batch_id)
        if not batch.path.is_file():
            raise SystemExit(f"unknown batch {batch_id}")
        return batch

    def read(self):
        return json.loads(self.path.read_text())

    def slot(self, index):
        return next(s for s in self.read()["slots"] if s["index"] == index)

    def update_slot(self, index, **fields):
        with file_lock(self.lock):
            state = self.read()
            for slot in state["slots"]:
                if slot["index"] == index:
                    slot.update(fields)
                    slot["updated"] = now()
            write_json_atomic(self.path, state)
        ledger("slot_update", batch=self.id, slot=index, **fields)

    def claim(self, index):
        """Non-blocking exclusive claim on a slot for this process; False if another copy holds it."""
        handle = (self.dir / f"slot-{index}.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._claims[index] = handle
        return True

    def release(self, index):
        handle = self._claims.pop(index, None)
        if handle:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def run_details(job_dir):
    """Per-run outcome summary of a Task Lab probe job."""
    runs = []
    for n in range(1, 6):
        try:
            state = json.loads((job_dir / "runs" / f"eval-{n}" / "state.json").read_text())
        except (OSError, ValueError):
            continue
        runs.append({"run": n, "status": state.get("status"), "duration_sec": round(state.get("duration_sec") or 0),
                     "turns": state.get("turns"),
                     "failed_tests": [t["name"].split("::")[-1] for t in state.get("tests", []) if t.get("status") == "failed"]})
    return runs
