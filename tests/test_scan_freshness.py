"""Golden screening + freshness gate for Strong (no network)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import marketdata as md  # noqa: E402
import scan  # noqa: E402


def make_hist(end="2026-11-11", n=260, start_px=10.0, end_px=20.0, vol=1_000_000.0, range_frac=0.05):
    idx = pd.bdate_range(end=end, periods=n)
    closes = np.linspace(start_px, end_px, n)
    highs = closes * (1 + range_frac / 2)
    lows = closes * (1 - range_frac / 2)
    return pd.DataFrame(
        {"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": np.full(n, vol)},
        index=idx,
    )


def test_golden_screen_identical_for_same_bars():
    good = make_hist()
    cheap = make_hist(end_px=3.0, start_px=2.0)
    first = {s: scan.screen_symbol(s, fr.copy()) for s, fr in (("GOOD", good), ("CHEAP", cheap))}
    second = {s: scan.screen_symbol(s, fr.copy()) for s, fr in (("GOOD", good), ("CHEAP", cheap))}
    assert first["GOOD"] is not None and first["GOOD"][0] == "GOOD"
    assert first["CHEAP"] is None
    assert first == second


def test_blocked_does_not_write_session_file(monkeypatch, tmp_path):
    expected = date(2026, 11, 11)
    stale = make_hist(end="2026-11-10")
    syms = [f"S{i}" for i in range(10)]
    frames = {s: stale.copy() for s in syms}
    stats = md.DownloadStats(requested=10, yahoo=10, fresh=0, stale_after=10)

    monkeypatch.setattr(scan, "last_complete_us_session_date", lambda: expected)
    monkeypatch.setattr(scan, "get_nasdaq_list", lambda: list(syms))
    monkeypatch.setattr(scan, "wait_for_internet", lambda: None)
    monkeypatch.setattr(scan, "download_daily", lambda *a, **k: (frames, stats))
    monkeypatch.chdir(tmp_path)
    # Plant an older session file — must not be replaced / a new-session file must not appear.
    old = tmp_path / "Strong_Stocks_V3_20261110.txt"
    old.write_text("OLD\n")
    rc = scan.main()
    assert rc == 3
    assert not (tmp_path / "Strong_Stocks_V3_20261111.txt").exists()
    assert old.read_text() == "OLD\n"


def test_fresh_pass_writes_expected_session_filename(monkeypatch, tmp_path):
    expected = date(2026, 11, 11)
    good = make_hist(end="2026-11-11")
    cheap = make_hist(end="2026-11-11", start_px=2.0, end_px=3.0)
    frames = {"GOOD": good, "CHEAP": cheap}
    stats = md.DownloadStats(requested=2, yahoo=2, fresh=2)

    monkeypatch.setattr(scan, "last_complete_us_session_date", lambda: expected)
    monkeypatch.setattr(scan, "get_nasdaq_list", lambda: ["GOOD", "CHEAP"])
    monkeypatch.setattr(scan, "wait_for_internet", lambda: None)
    monkeypatch.setattr(scan, "download_daily", lambda *a, **k: (frames, stats))
    monkeypatch.chdir(tmp_path)
    rc = scan.main()
    assert rc == 0
    out = tmp_path / "Strong_Stocks_V3_20261111.txt"
    assert out.exists()
    assert out.read_text(encoding="utf-8").strip().splitlines() == ["GOOD"]
    assert not (tmp_path / "Strong_Stocks_V3_20261112.txt").exists()
