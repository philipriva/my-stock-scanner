import datetime
import io
import os
import sys
import time

import pandas as pd
import requests
import yfinance as yf

from marketdata import (
    MIN_FRESH_FRAC,
    download_daily,
    format_status,
    last_bar_date,
    last_complete_us_session_date,
)

# --- 2.0 策略參數 ---
MIN_PRICE = 5
# 離高門檻 = ADR% × ADR_MULTIPLIER（取代固定 PROXIMITY_TO_HIGH=10%）
ADR_MULTIPLIER = 4.5
ADR_LEN = 20
# 可選夾住門檻%，避免極低／極高 ADR 過嚴或過鬆（設 None 關閉）
PROX_BAND_MIN_PCT = 4.0
PROX_BAND_MAX_PCT = 20.0
MIN_DOLLAR_VOLUME = 1_500_000
RELEVANT_YEARS = 5
CHUNK_SIZE = 100


def is_internet_up():
    try:
        requests.get("http://www.google.com", timeout=5)
        return True
    except Exception:
        return False


def wait_for_internet():
    if not is_internet_up():
        print("\n⚠️ 偵測到網絡斷開！腳本已自動暫停...")
        while not is_internet_up():
            time.sleep(10)
        print("✅ 網絡已恢復，繼續掃描...\n")


def get_nasdaq_list():
    print("📋 正在獲取市場名單...")
    try:
        url = "http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(response.text), sep="|")
        tickers = df[df["Test Issue"] == "N"]["Symbol"].astype(str).tolist()
        clean = [t for t in tickers if t.isalpha() and len(t) <= 4]
        print(f"📊 總數: {len(clean)} | 準備進行群組化高速掃描...")
        return list(set(clean))
    except Exception:
        return ["AAPL", "MSFT", "NVDA"]


def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def proximity_band_pct(adr_pct: float) -> float:
    """離高允許的最大 %：ADR% × 乘數，再可選 clamp。"""
    band = float(adr_pct) * ADR_MULTIPLIER
    if PROX_BAND_MIN_PCT is not None:
        band = max(band, float(PROX_BAND_MIN_PCT))
    if PROX_BAND_MAX_PCT is not None:
        band = min(band, float(PROX_BAND_MAX_PCT))
    return band


def session_file_name(expected) -> str:
    return f"Strong_Stocks_V3_{expected.strftime('%Y%m%d')}.txt"


def screen_symbol(symbol, hist_5y):
    """Return (symbol, output_line) if the symbol passes, else None.

    Filter math is unchanged from the previous in-loop body.
    """
    try:
        hist_5y = hist_5y.dropna(subset=["Close", "High", "Low", "Volume"])

        if len(hist_5y) < 200:
            return None

        hist_2y = hist_5y.tail(252 * 2)
        current_price = float(hist_2y["Close"].iloc[-1])

        if pd.isna(current_price) or current_price < MIN_PRICE:
            return None

        # 趨勢檢查 (EMA)
        ema_200 = hist_2y["Close"].ewm(span=200, adjust=False).mean().iloc[-1]
        ema_50 = hist_2y["Close"].ewm(span=50, adjust=False).mean().iloc[-1]
        if current_price < ema_200 or ema_50 < ema_200:
            return None

        # ADR%：近 ADR_LEN 日平均 (H-L)/L × 100（沿用原算法）
        daily_range_pct = (hist_5y["High"] - hist_5y["Low"]) / hist_5y["Low"]
        adr_pct = float(daily_range_pct.tail(ADR_LEN).mean() * 100)
        if pd.isna(adr_pct) or adr_pct <= 0:
            return None

        band_pct = proximity_band_pct(adr_pct)

        # 接近高位：相對「相關期（RELEVANT_YEARS）收市最高」嘅離高% ≤ ADR 動態門檻
        # （已移除固定 PROXIMITY_TO_HIGH=10% 同埋獨立 52 週 10% 關）
        relevant_ath = float(hist_5y["Close"].max())
        if relevant_ath <= 0 or pd.isna(relevant_ath):
            return None
        dist_to_ath_pct = (relevant_ath - current_price) / relevant_ath * 100.0
        if dist_to_ath_pct > band_pct:
            return None

        # 成交量檢查
        avg_vol = float(hist_2y["Volume"].iloc[-20:].mean())
        if current_price * avg_vol < MIN_DOLLAR_VOLUME:
            return None

        output = (
            f"✅ [強勢領先] {symbol} | 現價: {current_price:.2f} | "
            f"距高點: {dist_to_ath_pct:.1f}% | ADR: {adr_pct:.1f}% | "
            f"門檻: {band_pct:.1f}% ({ADR_MULTIPLIER}×)"
        )
        return symbol, output
    except Exception:
        return None


def cleanup_old_files():
    print("\n🧹 開始檢查並清理過期檔案...")
    now = time.time()
    cutoff = now - (5 * 86400)

    for file in os.listdir("."):
        if file.startswith("Strong_Stocks_V3_") and file.endswith(".txt"):
            file_path = os.path.join(".", file)
            if os.path.getmtime(file_path) < cutoff:
                try:
                    os.remove(file_path)
                    print(f"🗑️ 已自動刪除 5 天前的舊檔案: {file}")
                except Exception as e:
                    print(f"❌ 刪除 {file} 失敗: {e}")


def main() -> int:
    start_time = time.time()
    expected = last_complete_us_session_date()
    file_name = session_file_name(expected)
    all_tickers = get_nasdaq_list()

    print(f"🚀 開始批次掃描，總共 {len(all_tickers)} 隻（session {expected.isoformat()}）...")
    wait_for_internet()
    frames, stats = download_daily(
        all_tickers,
        expected,
        period=f"{RELEVANT_YEARS}y",
        batch_size=CHUNK_SIZE,
        pause_sec=1.5,
    )

    fresh_n = sum(1 for s in all_tickers if last_bar_date(frames.get(s)) == expected)
    denom_n = len(all_tickers)
    frac = (fresh_n / denom_n) if denom_n else 0.0
    fresh_ok = denom_n > 0 and frac >= MIN_FRESH_FRAC
    result_word = "WROTE" if fresh_ok else "BLOCKED"
    print(
        format_status(
            session=expected,
            writer="Strong",
            fresh=fresh_n,
            denom=denom_n or 1,
            yahoo=stats.yahoo,
            retry=stats.retry,
            stooq=stats.stooq,
            result=result_word,
        ),
        flush=True,
    )
    if not fresh_ok:
        print(
            f"BLOCKED (freshness < {MIN_FRESH_FRAC}): fresh={fresh_n}/{denom_n}={frac:.4f} — "
            f"{file_name} not written",
            file=sys.stderr,
            flush=True,
        )
        return 3

    hits = []
    for symbol in all_tickers:
        fr = frames.get(symbol)
        if fr is None or getattr(fr, "empty", True):
            continue
        hit = screen_symbol(symbol, fr)
        if hit is None:
            continue
        sym, output = hit
        print(output)
        hits.append(sym)

    if hits:
        with open(file_name, "w", encoding="utf-8") as f:
            for symbol in hits:
                f.write(f"{symbol}\n")
        print(f"📁 清單已寫入: {file_name} ({len(hits)})")
    else:
        print("\n❌ 今日未發現符合標準的股票。")

    cleanup_old_files()

    end_time = time.time()
    print("-" * 50)
    print(f"🏆 掃描與清理完成！總耗時: {(end_time - start_time)/60:.1f} 分鐘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
