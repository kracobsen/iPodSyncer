"""Tests for ``sysinfo.read_model_num_str`` + supported-model gate."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from ipodsync.device import sysinfo


def _write_plist(mp: Path, body: dict[str, object]) -> None:
    p = mp / "iPod_Control" / "Device" / "SysInfoExtended"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(plistlib.dumps(body))


def test_read_model_num_str_bare(tmp_path: Path) -> None:
    _write_plist(tmp_path, {"ModelNumStr": "MB562"})
    assert sysinfo.read_model_num_str(tmp_path) == "MB562"


def test_read_model_num_str_with_region_suffix(tmp_path: Path) -> None:
    _write_plist(tmp_path, {"ModelNumStr": "MB562LL/A"})
    assert sysinfo.read_model_num_str(tmp_path) == "MB562LL/A"


def test_read_model_num_str_missing_file(tmp_path: Path) -> None:
    assert sysinfo.read_model_num_str(tmp_path) is None


def test_read_model_num_str_malformed_plist(tmp_path: Path) -> None:
    p = tmp_path / "iPod_Control" / "Device" / "SysInfoExtended"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not a plist")
    assert sysinfo.read_model_num_str(tmp_path) is None


def test_read_model_num_str_missing_key(tmp_path: Path) -> None:
    _write_plist(tmp_path, {"SerialNumber": "X"})
    assert sysinfo.read_model_num_str(tmp_path) is None


@pytest.mark.parametrize("code", ["MB029", "MB147", "MB562", "MC293", "MC297"])
def test_is_supported_model_accepts_6g_codes(code: str) -> None:
    assert sysinfo.is_supported_model(code)
    assert sysinfo.is_supported_model(code + "LL/A")


@pytest.mark.parametrize(
    "code",
    [
        "MA107",  # Shuffle 1G
        "MC526",  # Nano 5G
        "MC916",  # Nano 6G
        "MD717",  # Touch 5G
        "",
    ],
)
def test_is_supported_model_rejects_other_codes(code: str) -> None:
    assert not sysinfo.is_supported_model(code)


def test_verify_classic_6g_supported(tmp_path: Path) -> None:
    _write_plist(tmp_path, {"ModelNumStr": "MB562LL/A"})
    model, err = sysinfo.verify_classic_6g(tmp_path)
    assert model == "MB562LL/A"
    assert err is None


def test_verify_classic_6g_unsupported(tmp_path: Path) -> None:
    _write_plist(tmp_path, {"ModelNumStr": "MA107"})
    model, err = sysinfo.verify_classic_6g(tmp_path)
    assert model == "MA107"
    assert err is not None
    assert "MA107" in err
    assert "Classic 6G" in err


def test_verify_classic_6g_missing(tmp_path: Path) -> None:
    model, err = sysinfo.verify_classic_6g(tmp_path)
    assert model is None
    assert err is None
