import glob, os, time
cutoff = time.time() - (5 * 86400) # 5 天前的秒數
for f in glob.glob("Strong_Stocks_V3_*.txt") + glob.glob("Maggiestocks_*.txt"):
    if os.path.getmtime(f) < cutoff:
        os.remove(f)
        print(f"已刪除過期檔案: {f}")
