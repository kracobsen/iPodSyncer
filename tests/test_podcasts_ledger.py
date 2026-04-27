"""Unit tests for the podcast consumed-ledger module (phase 18)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ipodsync.podcasts import ledger as ld


def _entry(
    sha1: str = "aaa",
    show: str = "Show",
    title: str = "Ep 1",
    source_path: str = "/tmp/missing",
    removed_at: str = "2026-04-27T00:00:00Z",
) -> ld.Entry:
    return ld.Entry(
        sha1=sha1,
        show=show,
        title=title,
        source_path=source_path,
        removed_at=removed_at,
    )


def test_roundtrip_preserves_entries(tmp_path: Path) -> None:
    p = tmp_path / "podcasts.json"
    a = ld.Ledger(guid="0xDEAD")
    a.record(_entry(sha1="aaa", show="TWiT", title="42"))
    a.record(_entry(sha1="bbb", show="Daring Fireball", title="The Talk Show"))
    ld.save(a, path=p)

    b = ld.load(a.guid, path=p)
    assert b.guid == a.guid
    assert b.entries == a.entries


def test_save_writes_current_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "podcasts.json"
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry())
    ld.save(ledger, path=p)
    raw = json.loads(p.read_text())
    assert raw["version"] == ld.SCHEMA_VERSION


def test_save_is_atomic_no_tmp_left(tmp_path: Path) -> None:
    p = tmp_path / "podcasts.json"
    ld.save(ld.Ledger(guid="0x1"), path=p)
    assert p.is_file()
    assert not (tmp_path / "podcasts.json.tmp").exists()


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "podcasts.json"
    ld.save(ld.Ledger(guid="0x1"), path=p)
    assert p.is_file()


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    out = ld.load("0xABC", path=p)
    assert out.guid == "0xABC"
    assert out.entries == {}


def test_load_missing_version_field_succeeds(tmp_path: Path) -> None:
    """Forward-compat: a ledger written without a version key still loads."""
    p = tmp_path / "podcasts.json"
    p.write_text(json.dumps({
        "entries": {
            "aaa": {
                "show": "X", "title": "Y",
                "source_path": "/tmp/x", "removed_at": "2026-01-01T00:00:00Z",
            },
        },
    }))
    out = ld.load("0x1", path=p)
    assert "aaa" in out.entries


def test_contains(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="aaa"))
    assert ledger.contains("aaa") is True
    assert ledger.contains("bbb") is False


def test_forget_pattern_removes_matching(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a", show="TWiT", title="42"))
    ledger.record(_entry(sha1="b", show="TWiT", title="43"))
    ledger.record(_entry(sha1="c", show="ATP", title="500"))

    gone = ledger.forget_pattern("TWiT")
    assert {e.sha1 for e in gone} == {"a", "b"}
    assert set(ledger.entries) == {"c"}


def test_forget_pattern_matches_show_slash_episode(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a", show="TWiT", title="42"))
    ledger.record(_entry(sha1="b", show="TWiT", title="43"))

    gone = ledger.forget_pattern("TWiT/42")
    assert [e.sha1 for e in gone] == ["a"]
    assert set(ledger.entries) == {"b"}


def test_forget_pattern_is_case_insensitive(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a", show="TWiT", title="42"))
    gone = ledger.forget_pattern("twit")
    assert [e.sha1 for e in gone] == ["a"]


def test_forget_pattern_empty_does_nothing(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a"))
    assert ledger.forget_pattern("") == []
    assert set(ledger.entries) == {"a"}


def test_forget_all_clears_and_returns(tmp_path: Path) -> None:
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a"))
    ledger.record(_entry(sha1="b"))
    gone = ledger.forget_all()
    assert {e.sha1 for e in gone} == {"a", "b"}
    assert ledger.entries == {}


def test_prune_missing_drops_entries_whose_source_is_gone(tmp_path: Path) -> None:
    src = tmp_path / "kept.mp3"
    src.write_bytes(b"x")
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="kept", source_path=str(src)))
    ledger.record(_entry(sha1="gone", source_path=str(tmp_path / "missing.mp3")))

    dropped = ledger.prune_missing()
    assert [e.sha1 for e in dropped] == ["gone"]
    assert set(ledger.entries) == {"kept"}


def test_save_then_load_after_forget_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "podcasts.json"
    ledger = ld.Ledger(guid="0x1")
    ledger.record(_entry(sha1="a", show="TWiT", title="42"))
    ledger.record(_entry(sha1="b", show="ATP", title="500"))
    ledger.forget_pattern("TWiT")
    ld.save(ledger, path=p)

    reloaded = ld.load(ledger.guid, path=p)
    assert set(reloaded.entries) == {"b"}


def test_path_for_uses_app_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = ld.path_for("0xDEADBEEF")
    assert p.parts[-3:] == ("ipodsync", "0xDEADBEEF", "podcasts.json")
    assert "Application Support" in str(p)
