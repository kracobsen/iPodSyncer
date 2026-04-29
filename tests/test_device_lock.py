"""Tests for ``ipodsync.device.lock``."""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ipodsync.device import lock as lock_mod


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(lock_mod, "_APP_SUPPORT", tmp_path)
    return tmp_path


def test_lock_acquires_and_releases(fake_root: Path) -> None:
    with lock_mod.device_lock("GUID"):
        pass
    # Re-acquirable in the same process.
    with lock_mod.device_lock("GUID"):
        pass


def test_lock_contended_raises_lockerror(fake_root: Path) -> None:
    """A separate fd holding the flock should make the next acquire fail fast."""
    p = lock_mod.lock_path("GUID")
    p.parent.mkdir(parents=True, exist_ok=True)
    holder = p.open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with (
            pytest.raises(lock_mod.LockError, match="another ipodsync"),
            lock_mod.device_lock("GUID"),
        ):
            pass
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_lock_distinct_guids_dont_collide(fake_root: Path) -> None:
    with lock_mod.device_lock("GUID-A"), lock_mod.device_lock("GUID-B"):
        pass


def test_lock_path_is_under_app_support(fake_root: Path) -> None:
    p = lock_mod.lock_path("ABCD1234")
    assert p == fake_root / "ABCD1234" / "lock"
