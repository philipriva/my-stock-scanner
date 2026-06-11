import yfinance as yf
import pandas as pd
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
CHUNK_SIZE = 100             # 每次打包下載 100 隻股票

# 💡 保持與你的 scan.yml 一致，使用 V3 命名
FILE_NAME = f"Strong_Stocks_V3_{datetime.datetime.now().strftime('%Y%m%d')}.txt"

def is_internet_up():
    try:
        requests.get("http://www.google.com", timeout=5)
        return True
    except:
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
        df = pd.read_csv(io.StringIO(response.text), sep='|')
        tickers = df[df['Test Issue'] == 'N']['Symbol'].astype(str).tolist()
        clean = [t for t in tickers if t.isalpha() and len(t) <= 4]
        
        # 💡 雲端自動化每次都是全新開機，所以直接全盤掃描（2分鐘搞定，不需要 log 檔案）
        print(f"📊 總數: {len(clean)} | 準備進行群組化高速掃描...")
        return list(set(clean))
    except:
        return ["AAPL", "MSFT", "NVDA"]

def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def process_batch(chunk_tickers):
    wait_for_internet()
    try:
        batch_data = yf.download(
            tickers=chunk_tickers, 
            period=f"{RELEVANT_YEARS}y", 
            group_by='ticker', 
            auto_adjust=True, 
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f"❌ 批次下載失敗: {e}")
        return

    for symbol in chunk_tickers:
        try:
            if len(chunk_tickers) == 1:
                hist_5y = batch_data
            else:
                if symbol not in batch_data.columns.levels[0]:
                    continue
                hist_5y = batch_data[symbol]
                
            # 🔥 關鍵修復：強力濾除歷史數據中包含 NaN 的無效行（防止輸出價格 nan 股票）
            hist_5y = hist_5y.dropna(subset=['Close', 'High', 'Low', 'Volume'])
            
            if len(hist_5y) < 200:
                continue
                
            hist_2y = hist_5y.tail(252 * 2)
            current_price = hist_2y['Close'].iloc[-1]
            
            if pd.isna(current_price) or current_price < MIN_PRICE:
                continue
                
            # 趨勢檢查 (EMA)
            ema_200 = hist_2y['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
            ema_50 = hist_2y['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
            if current_price < ema_200 or ema_50 < ema_200:
                continue

            # 52 週新高檢查
            year_high = hist_2y['Close'].tail(252).max()
            if (year_high - current_price) / year_high > PROXIMITY_TO_HIGH:
                continue

            # 近期高位與 ADR 檢查
            relevant_ath = hist_5y['Close'].max()
            daily_range_pct = (hist_5y['High'] - hist_5y['Low']) / hist_5y['Low']
            adr_pct = daily_range_pct.tail(20).mean() * 100
            dist_to_ath = (relevant_ath - current_price) / relevant_ath
            
            if dist_to_ath > (adr_pct * ADR_MULTIPLIER) / 100:
                continue

            # 成交量檢查
            avg_vol = hist_2y['Volume'].iloc[-20:].mean()
            if current_price * avg_vol < MIN_DOLLAR_VOLUME:
                continue

            # 🎉 通過篩選
            output = f"✅ [強勢領先] {symbol} | 現價: {current_price:.2f} | 距高點: {dist_to_ath*100:.1f}% | ADR: {adr_pct:.1f}%"
            print(output)
            with open(FILE_NAME, "a") as f:
                f.write(f"{symbol}\n")
                
        except Exception:
            pass

if __name__ == "__main__":
    start_time = time.time()
    all_tickers = get_nasdaq_list()
    
    ticker_chunks = list(chunk_list(all_tickers, CHUNK_SIZE))
    print(f"🚀 開始批次掃描，總共分爲 {len(ticker_chunks)} 組執行...")
    
    for i, chunk in enumerate(ticker_chunks):
        process_batch(chunk)
        time.sleep(random.uniform(1.0, 2.0))
    
    end_time = time.time()
    print("-" * 50)
    print(f"🏆 掃描完成！總耗時: {(end_time - start_time)/60:.1f} 分鐘")
