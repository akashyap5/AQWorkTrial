"""Lifecycle checks use mocks; no real power assertions or sleeps are created."""
from unittest.mock import Mock

import pytest

from phase2 import runtime


@pytest.fixture
def inhibitor(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.shutil, "which", Mock(return_value="/usr/bin/caffeinate"))
    monkeypatch.setattr(runtime.os, "getpid", lambda: 12345)
    child = Mock()
    child.poll.return_value = None
    popen = Mock(return_value=child)
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    return popen, child


def test_inhibitor_is_scoped_and_watches_worker_pid(inhibitor):
    popen, child = inhibitor
    with runtime.keep_awake() as active:
        assert active is True
        child.terminate.assert_not_called()
        assert popen.call_args.args[0] == ["/usr/bin/caffeinate", "-i", "-w", "12345"]
        assert popen.call_args.kwargs["start_new_session"] is True
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)


def test_inhibitor_is_cleaned_up_when_batch_raises(inhibitor):
    _, child = inhibitor
    with pytest.raises(ValueError, match="batch failed"):
        with runtime.keep_awake():
            raise ValueError("batch failed")
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)


def test_exited_inhibitor_is_reaped_without_terminate(inhibitor):
    _, child = inhibitor
    child.poll.return_value = 0
    with runtime.keep_awake():
        pass
    child.terminate.assert_not_called()
    child.wait.assert_called_once_with(timeout=3)


def test_stuck_inhibitor_is_killed_after_bounded_wait(inhibitor):
    _, child = inhibitor
    child.wait.side_effect = [runtime.subprocess.TimeoutExpired("caffeinate", 3), 0]
    with runtime.keep_awake():
        pass
    child.kill.assert_called_once()
    assert child.wait.call_count == 2


@pytest.mark.parametrize("platform,executable", [("linux", "/usr/bin/caffeinate"), ("darwin", None)])
def test_unsupported_runtime_has_no_subprocess_dependency(monkeypatch, inhibitor, platform, executable):
    popen, _ = inhibitor
    monkeypatch.setattr(runtime.sys, "platform", platform)
    monkeypatch.setattr(runtime.shutil, "which", Mock(return_value=executable))
    with runtime.keep_awake() as active:
        assert active is False
    popen.assert_not_called()


def test_inhibitor_spawn_failure_does_not_prevent_batch_work(inhibitor):
    popen, _ = inhibitor
    popen.side_effect = OSError("not executable")
    with runtime.keep_awake() as active:
        assert active is False
