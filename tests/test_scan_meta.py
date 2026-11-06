from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from scan_meta import (
    build_scan_meta,
    payload_hash,
    publish_file_date,
    verify_meta_against_list,
    write_list_and_meta,
)
from us_session import last_complete_us_session_date
import scan as strong_scan

NY = ZoneInfo("America/New_York")


class TestPublishDate(unittest.TestCase):
    def test_session_plus_one(self):
        self.assertEqual(publish_file_date(date(2026, 11, 5)), date(2026, 11, 6))


class TestMetaBinding(unittest.TestCase):
    def test_hash_stable(self):
        self.assertEqual(payload_hash(["b", "a"]), payload_hash(["A", "B"]))

    def test_ok_fresh_with_hits(self):
        meta = build_scan_meta(
            source_id="test",
            list_filename="Strong_Stocks_V3_20261106.txt",
            tickers=["AAA"],
            target_session=date(2026, 11, 5),
            universe_n=10,
            bar_dates_all=[date(2026, 11, 5)] * 10,
            missing_count=0,
            selected_bar_dates={"AAA": "2026-11-05"},
        )
        self.assertEqual(meta["status"], "OK")
        self.assertEqual(meta["target_session"], "2026-11-05")

    def test_stale_fail_closed(self):
        meta = build_scan_meta(
            source_id="test",
            list_filename="x.txt",
            tickers=["AAA"],
            target_session=date(2026, 11, 5),
            universe_n=10,
            bar_dates_all=[date(2026, 11, 4)] * 10,
            missing_count=0,
            selected_bar_dates={"AAA": "2026-11-04"},
        )
        self.assertEqual(meta["status"], "FAIL_STALE")

    def test_ok_no_new_signal(self):
        meta = build_scan_meta(
            source_id="test",
            list_filename="x.txt",
            tickers=[],
            target_session=date(2026, 11, 5),
            universe_n=10,
            bar_dates_all=[date(2026, 11, 5)] * 10,
            missing_count=0,
            selected_bar_dates={},
        )
        self.assertEqual(meta["status"], "OK_NO_NEW_SIGNAL")

    def test_write_and_verify(self):
        meta = build_scan_meta(
            source_id="test",
            list_filename="Strong_Stocks_V3_20261106.txt",
            tickers=["AAA", "BBB"],
            target_session=date(2026, 11, 5),
            universe_n=2,
            bar_dates_all=[date(2026, 11, 5), date(2026, 11, 5)],
            missing_count=0,
            selected_bar_dates={"AAA": "2026-11-05", "BBB": "2026-11-05"},
        )
        lp, mp = write_list_and_meta("out_test_strong.txt", ["AAA", "BBB"], meta)
        loaded = json.loads(mp.read_text())
        tickers = [ln.strip() for ln in lp.read_text().splitlines() if ln.strip()]
        ok, msg = verify_meta_against_list(loaded, tickers)
        self.assertTrue(ok, msg)
        # tamper
        bad_ok, _ = verify_meta_against_list(loaded, ["AAA"])
        self.assertFalse(bad_ok)
        lp.unlink()
        mp.unlink()

    def test_thanksgiving_session(self):
        self.assertEqual(
            last_complete_us_session_date(datetime(2026, 11, 26, 12, 0, tzinfo=NY)),
            date(2026, 11, 25),
        )


class TestScanRejectsStalePick(unittest.TestCase):
    def test_process_batch_skips_stale_bar(self):
        idx = pd.DatetimeIndex([pd.Timestamp("2026-11-04")] * 250)
        df = pd.DataFrame(
            {"Close": 10.0, "High": 11.0, "Low": 9.0, "Volume": 1_000_000},
            index=idx,
        )
        # MultiIndex columns for single symbol path with len>1 chunk uses levels —
        # use len==1 path
        hits, bars, all_bars = [], {}, []
        from collections import Counter

        reasons = Counter()
        with patch.object(strong_scan.yf, "download", return_value=df), patch.object(
            strong_scan, "wait_for_internet"
        ):
            strong_scan.process_batch(
                ["AAA"],
                target_session=date(2026, 11, 5),
                hits=hits,
                selected_bars=bars,
                bar_dates_all=all_bars,
                reasons=reasons,
            )
        self.assertEqual(hits, [])
        self.assertEqual(all_bars, [date(2026, 11, 4)])
        self.assertGreaterEqual(reasons["stale_selected_bar"], 1)


if __name__ == "__main__":
    unittest.main()
