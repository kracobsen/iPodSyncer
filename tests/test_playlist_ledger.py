"""Tests for ``ipodsync.playlist.save_ledger`` atomicity."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ipodsync import playlist as playlist_mod


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(playlist_mod, "LEDGER_ROOT", tmp_path / "playlists")
    return tmp_path


def test_save_ledger_round_trip(fake_root: Path) -> None:
    playlist_mod.save_ledger("GUID", {"workout", "chill"})
    assert playlist_mod.load_ledger("GUID") == {"workout", "chill"}


def test_save_ledger_no_tmp_left_behind(fake_root: Path) -> None:
    playlist_mod.save_ledger("GUID", {"a"})
    p = playlist_mod.ledger_path("GUID")
    leftover = list(p.parent.glob("*.tmp"))
    assert leftover == []


def test_save_ledger_failure_preserves_existing(fake_root: Path) -> None:
    playlist_mod.save_ledger("GUID", {"original"})
    p = playlist_mod.ledger_path("GUID")
    before = p.read_text()

    real_write_text = Path.write_text

    def boom(self: Path, *args: object, **kwargs: object) -> int:
        if self.suffix == ".tmp":
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(Path, "write_text", boom), pytest.raises(OSError, match="disk full"):
        playlist_mod.save_ledger("GUID", {"replacement"})

    assert p.read_text() == before
    assert json.loads(p.read_text()) == {"owned": ["original"]}
