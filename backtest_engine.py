"""
สรุปผลการตัดสินใจที่กรอกมือไว้ใน market_training_data.csv

วัดผล "การตัดสินใจของคน" ไม่ใช่สัญญาณของบอท — นับเฉพาะแถวที่กรอก
your_decision เป็น BUY/SELL และกรอก trade_result เป็น WIN/LOSS แล้ว
"""

import os
import sys

import pandas as pd

CSV_FILE = "market_training_data.csv"
REQUIRED_COLUMNS = ("your_decision", "trade_result")


def summarize(frame, title):
    """พิมพ์สรุปของชุดข้อมูลหนึ่งชุด — ข้ามไปถ้าไม่มีไม้เลย"""
    total = len(frame)
    if total == 0:
        return

    wins = (frame["trade_result"] == "WIN").sum()
    losses = (frame["trade_result"] == "LOSS").sum()
    win_rate = wins / total * 100

    print(f"{title:<12} เทรด {total:>3} | ชนะ {wins:>3} | แพ้ {losses:>3} | Win rate {win_rate:6.2f}%")


def run_backtest(csv_file=CSV_FILE):
    if not os.path.exists(csv_file):
        print(f"ยังไม่มีไฟล์ข้อมูล: {csv_file}")
        return

    df = pd.read_csv(csv_file)

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        print(f"ไฟล์ขาดคอลัมน์: {', '.join(missing)}")
        return

    decided = df[df["your_decision"].isin(["BUY", "SELL"])]
    scored = decided[decided["trade_result"].isin(["WIN", "LOSS"])]

    print(f"--- สรุปการตัดสินใจของคุณ ({csv_file}) ---")
    print(f"แถวทั้งหมดในไฟล์: {len(df)}")
    print(f"แถวที่ตัดสินใจเข้าไม้: {len(decided)}")
    print(f"แถวที่กรอกผลแล้ว: {len(scored)}")

    if len(scored) == 0:
        print("\nยังไม่มีแถวที่กรอก trade_result เป็น WIN/LOSS จึงยังคำนวณ win rate ไม่ได้")
        return

    print()
    summarize(scored, "ทั้งหมด")
    summarize(scored[scored["your_decision"] == "BUY"], "เฉพาะ BUY")
    summarize(scored[scored["your_decision"] == "SELL"], "เฉพาะ SELL")

    # ดูว่าการเทรดตามเทรนด์ H1 ให้ผลต่างจากการเทรดสวนเทรนด์แค่ไหน
    if "h1_trend" in scored.columns:
        print()
        for trend, group in scored.groupby("h1_trend"):
            summarize(group, f"H1 {trend}")


if __name__ == "__main__":
    run_backtest(sys.argv[1] if len(sys.argv) > 1 else CSV_FILE)
