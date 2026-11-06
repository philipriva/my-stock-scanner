#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strong_Stocks_V3 scanner — emits ticker list + OHLC-bound sidecar metadata."""
from __future__ import annotations

import io
import os
import random
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from scan_meta import build_scan_meta, publish_file_date, write_list_and_meta
from us_session import last_complete_us_session_date, session_bar_date

# --- 2.0 策略參數 ---
MIN_PRICE = 5
ADR_MULTIPLIER = 4.5
ADR_LEN = 20
PROX_BAND_MIN_PCT = 4.0
PROX_BAND_MAX_PCT = 20.0
MIN_DOLLAR_VOLUME = 1_500_000
RELEVANT_YEARS = 5
CHUNK_SIZE = 100
SOURCE_ID = "philipriva/my-stock-scanner:Strong_Stocks_V3"
PRODUCER_VERSION = "scan.py@meta-v1"


def is_internet_up() -> bool:
    try:
        requests.get("http://www.google.com", timeout=5)
        return True
    except Exception:
        return False


def wait_for_internet() -> None:
    if not is_internet_up():
        print("\n⚠️ 偵測到網絡斷開！腳本已自動暫停...")
        while not is_internet_up():
            time.sleep(10)
        print("✅ 網絡已恢復，繼續掃描...\n")


def get_nasdaq_list() -> list[str]:
    print("📋 正在獲取市場名單...")
    try:
        url = "http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        df = pd.read_csv(io.StringIO(response.text), sep="|")
        tickers = df[df["Test Issue"] == "N"]["Symbol"].astype(str).tolist()
        clean = [t for t in tickers if t.isalpha() and len(t) <= 4]
        print(f"📊 總數: {len(clean)} | 準備進行群組化高速掃描...")
        return list(set(clean))
    except Exception as exc:
        print(f"⚠️ nasdaq list failed: {exc}")
        return ["AAPL", "MSFT", "NVDA"]


def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def proximity_band_pct(adr_pct: float) -> float:
    band = float(adr_pct) * ADR_MULTIPLIER
    if PROX_BAND_MIN_PCT is not None:
        band = max(band, float(PROX_BAND_MIN_PCT))
    if PROX_BAND_MAX_PCT is not None:
        band = min(band, float(PROX_BAND_MAX_PCT))
    return band


def process_batch(
    chunk_tickers: list[str],
    *,
    target_session: date,
    hits: list[str],
    selected_bars: dict[str, str],
    bar_dates_all: list[date],
    reasons: Counter,
) -> int:
    """Returns download_failed count for this chunk (0 or len)."""
    wait_for_internet()
    try:
        batch_data = yf.download(
            tickers=chunk_tickers,
            period=f"{RELEVANT_YEARS}y",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"❌ 批次下載失敗: {e}")
        reasons["download_failed"] += len(chunk_tickers)
        return len(chunk_tickers)

    dl_fail = 0
    for symbol in chunk_tickers:
        try:
            if len(chunk_tickers) == 1:
                hist_5y = batch_data
            else:
                if not isinstance(batch_data.columns, pd.MultiIndex):
                    reasons["download_failed"] += 1
                    dl_fail += 1
                    continue
                if symbol not in batch_data.columns.levels[0]:
                    reasons["download_failed"] += 1
                    dl_fail += 1
                    continue
                hist_5y = batch_data[symbol]

            hist_5y = hist_5y.dropna(subset=["Close", "High", "Low", "Volume"])
            if len(hist_5y) < 200:
                reasons["too_few_bars"] += 1
                continue

            last_bar = session_bar_date(hist_5y.index[-1])
            bar_dates_all.append(last_bar)

            hist_2y = hist_5y.tail(252 * 2)
            current_price = float(hist_2y["Close"].iloc[-1])
            if pd.isna(current_price) or current_price < MIN_PRICE:
                reasons["price_filter"] += 1
                continue

            ema_200 = hist_2y["Close"].ewm(span=200, adjust=False).mean().iloc[-1]
            ema_50 = hist_2y["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
            if current_price < ema_200 or ema_50 < ema_200:
                reasons["ema_filter"] += 1
                continue

            daily_range_pct = (hist_5y["High"] - hist_5y["Low"]) / hist_5y["Low"]
            adr_pct = float(daily_range_pct.tail(ADR_LEN).mean() * 100)
            if pd.isna(adr_pct) or adr_pct <= 0:
                reasons["adr_filter"] += 1
                continue

            band_pct = proximity_band_pct(adr_pct)
            relevant_ath = float(hist_5y["Close"].max())
            if relevant_ath <= 0 or pd.isna(relevant_ath):
                reasons["ath_filter"] += 1
                continue
            dist_to_ath_pct = (relevant_ath - current_price) / relevant_ath * 100.0
            if dist_to_ath_pct > band_pct:
                reasons["proximity_filter"] += 1
                continue

            avg_vol = float(hist_2y["Volume"].iloc[-20:].mean())
            if current_price * avg_vol < MIN_DOLLAR_VOLUME:
                reasons["dollar_vol_filter"] += 1
                continue

            # Only accept picks whose last bar is the target session (fail-closed).
            if last_bar != target_session:
                reasons["stale_selected_bar"] += 1
                continue

            sym_u = symbol.strip().upper()
            hits.append(sym_u)
            selected_bars[sym_u] = last_bar.isoformat()
            print(
                f"✅ [強勢領先] {sym_u} | 現價: {current_price:.2f} | "
                f"距高點: {dist_to_ath_pct:.1f}% | ADR: {adr_pct:.1f}% | "
                f"bar={last_bar.isoformat()} | 門檻: {band_pct:.1f}% ({ADR_MULTIPLIER}×)"
            )
        except Exception:
            reasons["symbol_exception"] += 1
    return dl_fail


def cleanup_old_files(prefix: str = "Strong_Stocks_V3_", keep_days: int = 5) -> None:
    print("\n🧹 開始檢查並清理過期檔案...")
    now = time.time()
    cutoff = now - (keep_days * 86400)
    for file in os.listdir("."):
        if not (file.startswith(prefix) and (file.endswith(".txt") or file.endswith(".meta.json"))):
            continue
        file_path = os.path.join(".", file)
        if os.path.getmtime(file_path) < cutoff:
            try:
                os.remove(file_path)
                print(f"🗑️ 已自動刪除 {keep_days} 天前的舊檔案: {file}")
            except Exception as e:
                print(f"❌ 刪除 {file} 失敗: {e}")


def run_scan(tickers: list[str] | None = None) -> tuple[int, Path, Path, dict]:
    target_session = last_complete_us_session_date()
    pub = publish_file_date(target_session)
    list_name = f"Strong_Stocks_V3_{pub.strftime('%Y%m%d')}.txt"
    print(
        f"target_session={target_session.isoformat()} publish_file_date={pub.isoformat()} "
        f"list={list_name}",
        flush=True,
    )

    all_tickers = tickers if tickers is not None else get_nasdaq_list()
    hits: list[str] = []
    selected_bars: dict[str, str] = {}
    bar_dates_all: list[date] = []
    reasons: Counter = Counter()
    download_failed = 0

    chunks = list(chunk_list(all_tickers, CHUNK_SIZE))
    print(f"🚀 開始批次掃描，總共分爲 {len(chunks)} 組執行...", flush=True)
    for chunk in chunks:
        download_failed += process_batch(
            chunk,
            target_session=target_session,
            hits=hits,
            selected_bars=selected_bars,
            bar_dates_all=bar_dates_all,
            reasons=reasons,
        )
        time.sleep(random.uniform(1.0, 2.0))

    # Deduplicate preserving order
    seen = set()
    uniq_hits = []
    for t in hits:
        if t not in seen:
            seen.add(t)
            uniq_hits.append(t)

    missing_count = max(0, len(all_tickers) - len(bar_dates_all))
    meta = build_scan_meta(
        source_id=SOURCE_ID,
        list_filename=list_name,
        tickers=uniq_hits,
        target_session=target_session,
        universe_n=len(all_tickers),
        bar_dates_all=bar_dates_all,
        missing_count=missing_count,
        selected_bar_dates=selected_bars,
        download_failed=download_failed,
        excluded_reasons=dict(reasons),
        producer_version=PRODUCER_VERSION,
    )
    lp, mp = write_list_and_meta(list_name, uniq_hits, meta)
    print(
        f"wrote {lp} n={len(uniq_hits)} meta={mp} status={meta['status']} "
        f"fresh_frac={meta['input_bars'].get('frac')}",
        flush=True,
    )
    return 0 if meta["status"] in ("OK", "OK_NO_NEW_SIGNAL") else 4, lp, mp, meta


if __name__ == "__main__":
    start_time = time.time()
    code, lp, mp, meta = run_scan()
    cleanup_old_files()
    end_time = time.time()
    print("-" * 50)
    print(f"🏆 掃描與清理完成！總耗時: {(end_time - start_time)/60:.1f} 分鐘 status={meta['status']}")
    raise SystemExit(code)
