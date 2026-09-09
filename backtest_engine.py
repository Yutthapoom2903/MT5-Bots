import pandas as pd

def run_backtest(csv_file):
    if not os.path.exists(csv_file):
        print("ยังไม่มีไฟล์ข้อมูล CSV")
        return

    df = pd.read_csv(csv_file)
    
    # กรองเฉพาะแถวที่คุณใส่การตัดสินใจแล้ว
    results = df[df['your_decision'].isin(['BUY', 'SELL'])]
    
    total_trades = len(results)
    wins = len(results[results['trade_result'] == 'WIN'])
    losses = len(results[results['trade_result'] == 'LOSS'])
    
    print(f"--- ผลสรุปการตัดสินใจของคุณ ---")
    print(f"จำนวนเทรดทั้งหมด: {total_trades}")
    print(f"ชนะ (WIN): {wins}")
    print(f"แพ้ (LOSS): {losses}")
    
    if total_trades > 0:
        win_rate = (wins / total_trades) * 100
        print(f"Win Rate: {win_rate:.2f}%")

run_backtest("market_training_data.csv")