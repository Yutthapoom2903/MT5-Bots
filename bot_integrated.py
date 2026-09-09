import os, requests, time, MetaTrader5 as mt5, pandas as pd
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except: pass

def execute_trade(symbol, action, close_price):
    # คำนวณ SL/TP (ใช้ค่าคงที่พื้นฐาน ทองคำสวิงประมาณ 300-500 จุด)
    point = mt5.symbol_info(symbol).point
    if action == "buy":
        sl = close_price - 300 * point
        tp = close_price + 600 * point
        price = mt5.symbol_info_tick(symbol).ask
        order_type = mt5.ORDER_TYPE_BUY
    else:
        sl = close_price + 300 * point
        tp = close_price - 600 * point
        price = mt5.symbol_info_tick(symbol).bid
        order_type = mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": 0.01,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "magic": 123456,
        "deviation": 20,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(request)

if not mt5.initialize(): exit()
send_telegram("🚀 บอท Auto-Trade (SL/TP Enabled) เริ่มทำงานแล้ว!")

last_signal_time = None

try:
    while True:
        rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 100)
        if rates is None: continue
        df = pd.DataFrame(rates)
        df["ma_f"] = df["close"].rolling(20).mean()
        df["ma_s"] = df["close"].rolling(50).mean()
        
        prev, curr = df.iloc[-3], df.iloc[-2]
        
        signal = "HOLD"
        # เงื่อนไขเข้าเทรด
        if prev["ma_f"] <= prev["ma_s"] and curr["ma_f"] > curr["ma_s"]: signal = "BUY"
        elif prev["ma_f"] >= prev["ma_s"] and curr["ma_f"] < curr["ma_s"]: signal = "SELL"
            
        if signal != "HOLD" and str(curr["time"]) != last_signal_time:
            last_signal_time = str(curr["time"])
            res = execute_trade("XAUUSD", "buy" if signal == "BUY" else "sell", curr["close"])
            
            status = "Success" if res and res.retcode == 10009 else f"Failed: {mt5.last_error()}"
            msg = f"✅ Auto {signal} Executed!\nPrice: {curr['close']}\nStatus: {status}\nSL/TP Set."
            send_telegram(msg)
            
        time.sleep(30)
except KeyboardInterrupt:
    mt5.shutdown()