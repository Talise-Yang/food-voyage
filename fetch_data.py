# -*- coding: utf-8 -*-
"""
fetch_data.py —— 候选国贸易数据拉取脚本
数据来源：UN Comtrade 官方 API（https://comtradeapi.un.org）
用途：拉取 5 个优先国家的 HS 0712（脱水蔬菜）进口数据，
     原始 JSON 存入 data/raw/（这就是答辩时的"数据管道存档"），
     清洗后的汇总存入 data/comtrade_summary.csv

使用方法：
1. 把 API key 填到下面 config.ini 里（或设环境变量 COMTRADE_KEY）
2. 运行：python fetch_data.py
"""

import csv
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime

# ---------- 配置 ----------

def load_key():
    """优先读环境变量，其次读同目录 config.ini"""
    key = os.environ.get("COMTRADE_KEY", "").strip()
    if key:
        return key
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if os.path.exists(cfg):
        with open(cfg, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("COMTRADE_KEY"):
                    return line.split("=", 1)[1].strip()
    return ""

# 5 个优先国家：UN M49 代码
COUNTRIES = {
    "842": "USA",           # 美国
    "392": "Japan",         # 日本
    "410": "Rep. of Korea", # 韩国
    "276": "Germany",       # 德国
    "704": "Viet Nam",      # 越南
}

HS_CODE = "0712"          # 脱水蔬菜
YEARS = [2022, 2023, 2024]  # 拉 3 年，用来算"市场增速"维度

BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
# C=货物贸易 A=年度 HS=HS编码分类

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
OUT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "comtrade_summary.csv")


# ---------- 核心函数 ----------

def fetch_one(reporter_code, year, key, partner_code=0):
    """拉一个国家一年的 HS0712 进口额。partner_code=0 全世界合计；156=从中国进口"""
    params = (
        f"?reporterCode={reporter_code}"
        f"&period={year}"
        f"&cmdCode={HS_CODE}"
        f"&flowCode=M"              # M = 进口
        f"&partnerCode={partner_code}"
        f"&subscription-key={key}"
    )
    url = BASE_URL + params
    req = urllib.request.Request(url, headers={"User-Agent": "shiqiyuanhang/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_raw(data, reporter_name, year):
    """原始 JSON 存档——文件名自带日期时间，可复现、可审计"""
    os.makedirs(RAW_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{reporter_name}_{HS_CODE}_{year}_{stamp}.json"
    path = os.path.join(RAW_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def extract_total(data):
    """从返回里取进口总额（美元）。
    有的国家（如德国、越南）一次返回多条记录，必须过滤到"总量口径"：
    motCode=0（全部运输方式）、customsCode='C00'（全部报关方式）、partner2Code=0（无第二伙伴国）
    """
    rows = data.get("data", [])
    if not rows:
        return None
    totals = [r for r in rows
              if str(r.get("motCode")) == "0"
              and str(r.get("customsCode")) == "C00"
              and str(r.get("partner2Code")) == "0"]
    if not totals:          # 有的国家不分运输/报关方式，只有一条总记录
        totals = rows
    # 仍有多条时取金额最大的一条（同一口径的重复记录数值相同，取大不取小防止取到残片）
    best = max(totals, key=lambda r: r.get("primaryValue") or 0)
    return best.get("primaryValue")


def main():
    key = load_key()
    if not key:
        print("[错误] 没找到 API key。请建一个 config.ini，写一行：COMTRADE_KEY=你的key")
        return

    results = []  # (国家英文名, 年份, 进口总额美元, 从中国进口美元)
    for code, name in COUNTRIES.items():
        for year in YEARS:
            row = [name, year, None, None]
            for partner, label in ((0, "全球"), (156, "中国")):  # 156 = 中国
                print(f"正在拉取 {name} {year}（来源：{label}）...")
                try:
                    data = fetch_one(code, year, key, partner_code=partner)
                    raw_path = save_raw(data, f"{name}_{label}", year)
                    value = extract_total(data)
                    if value is None:
                        print(f"  无数据（原始返回已存 {raw_path}）")
                    else:
                        print(f"  {value:,.0f} 美元")
                    row[2 if partner == 0 else 3] = value
                except urllib.error.HTTPError as e:
                    print(f"  [失败] HTTP {e.code}：{e.reason}（429=触发限流，等 1 分钟再跑）")
                except Exception as e:
                    print(f"  [失败] {e}")
                time.sleep(1.2)  # 免费 key 限流：每秒 1 次，每天 100 次，慢一点稳
            results.append(tuple(row))

    # 汇总成 CSV（utf-8-sig，Excel 打开不乱码）
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["国家", "年份", "HS0712进口总额_美元", "从中国进口_美元", "中国份额_%",
                    "数据来源", "拉取时间"])
        for name, year, total, from_cn in results:
            share = round(from_cn / total * 100, 1) if (total and from_cn) else ""
            w.writerow([name, year,
                        f"{total:.0f}" if total is not None else "未报告",
                        f"{from_cn:.0f}" if from_cn is not None else "未报告",
                        share, "UN Comtrade API", datetime.now().strftime("%Y-%m-%d %H:%M")])
    print(f"\n完成。汇总表：{OUT_CSV}")
    print(f"原始存档在：{RAW_DIR}（这个文件夹别删，答辩要展示）")


if __name__ == "__main__":
    main()
