import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import datetime
import requests
import io
import time
import random
import os

# --- 🎯 2.0 策略參數 ---
MIN_PRICE = 5               
PROXIMITY_TO_HIGH = 0.10     
ADR_MULTIPLIER = 4.5         
MIN_DOLLAR_VOLUME = 1500000  
RELEVANT_YEARS = 5           

FILE_NAME = f"Strong_Stocks_V2_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
CHECKPOINT_FILE = "processed_tickers.log"  # 記錄已處理過的股票

def is_internet_up():
    """檢查互聯網是否連通"""
    try:
        requests.get("http://www.google.com", timeout=5)
        return True
    except:
        return False

def wait_for_internet():
    """如果斷網，進入暫停模式等待恢復"""
    if not is_internet_up():
        print("\n⚠️ 偵測到網絡斷開！腳本已自動暫停...")
        while not is_internet_up():
            time.sleep(10)  # 每 10 秒檢查一次
        print("✅ 網絡已恢復，繼續掃描...\n")

def fetch_data_with_retry(ticker, period, retries=3):
    """增加網絡檢測與重試機制"""
    for i in range(retries):
        try:
            wait_for_internet() # 每次抓取前確認網絡
            data = ticker.history(period=period, auto_adjust=True)
            if not data.empty:
                return data
            time.sleep(2)
        except Exception as e:
            time.sleep(2)
    return pd.DataFrame()

def check_stock(symbol):
    # 如果已經掃描過，跳過
    time.sleep(random.uniform(0.3, 0.8)) # 稍微加快速度
    
    try:
        ticker = yf.Ticker(symbol)
        
        # 1. 抓取數據 (加入斷網檢測)
        hist_2y = fetch_data_with_retry(ticker, "2y")
        if hist_2y is None or len(hist_2y) < 200: 
            mark_as_processed(symbol)
            return None
        
        current_price = hist_2y['Close'].iloc[-1]
        if current_price < MIN_PRICE: 
            mark_as_processed(symbol)
            return None
        
        # 趨勢檢查
        ema_200 = hist_2y['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
        ema_50 = hist_2y['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
        if current_price < ema_200 or ema_50 < ema_200:
            mark_as_processed(symbol)
            return None

        # 2. 52 週新高檢查
        year_high = hist_2y['Close'].tail(252).max()
        if (year_high - current_price) / year_high > PROXIMITY_TO_HIGH:
            mark_as_processed(symbol)
            return None

        # 3. 近期高位與 ADR 檢查
        hist_5y = fetch_data_with_retry(ticker, f"{RELEVANT_YEARS}y")
        if hist_5y.empty:
            mark_as_processed(symbol)
            return None
            
        relevant_ath = hist_5y['Close'].max()
        daily_range_pct = (hist_5y['High'] - hist_5y['Low']) / hist_5y['Low']
        adr_pct = daily_range_pct.tail(20).mean() * 100
        dist_to_ath = (relevant_ath - current_price) / relevant_ath
        
        if dist_to_ath > (adr_pct * ADR_MULTIPLIER) / 100:
            mark_as_processed(symbol)
            return None

        # 4. 成交量檢查
        avg_vol = hist_2y['Volume'].iloc[-20:].mean()
        if current_price * avg_vol < MIN_DOLLAR_VOLUME:
            mark_as_processed(symbol)
            return None

        # 🎉 通過篩選
        output = f"✅ [強勢領先] {symbol} | 現價: {current_price:.2f} | 距高點: {dist_to_ath*100:.1f}% | ADR: {adr_pct:.1f}%"
        print(output)
        with open(FILE_NAME, "a") as f:
            f.write(f"{symbol}\n")
        
        mark_as_processed(symbol)
        return symbol
            
    except Exception:
        mark_as_processed(symbol)
        return None

def mark_as_processed(symbol):
    """將已處理的股票寫入 Log"""
    with open(CHECKPOINT_FILE, "a") as f:
        f.write(f"{symbol}\n")

def get_nasdaq_list():
    print("📋 正在獲取市場名單...")
    processed = []
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            processed = [line.strip() for line in f.readlines()]
            
    try:
        url = "http://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_csv(io.StringIO(response.text), sep='|')
        tickers = df[df['Test Issue'] == 'N']['Symbol'].astype(str).tolist()
        clean = [t for t in tickers if t.isalpha() and len(t) <= 4]
        
        # 排除掉已經處理過的股票 (斷點續傳關鍵)
        remaining = [t for t in clean if t not in processed]
        print(f"📊 總數: {len(clean)} | 已處理: {len(processed)} | 剩餘待掃描: {len(remaining)}")
        
        random.shuffle(remaining)
        return list(set(remaining))
    except:
        return ["AAPL", "MSFT", "NVDA"]

if __name__ == "__main__":
    start_time = time.time()
    all_tickers = get_nasdaq_list()
    
    if not all_tickers:
        print("🎊 所有股票已掃描完畢！如果要重新開始，請刪除 processed_tickers.log")
    else:
        print(f"🚀 開始掃描，使用 4 個線程...")
        # 使用 4 個線程保持穩定，避免 Yahoo 封鎖
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(check_stock, all_tickers)
        
        end_time = time.time()
        print("-" * 50)
        print(f"🏆 掃描完成！總耗時: {(end_time - start_time)/60:.1f} 分鐘")
        print(f"📁 強勢股清單: {FILE_NAME}")
        print(f"💡 提示: 若要重新完整掃描，請手動刪除 {CHECKPOINT_FILE}")
