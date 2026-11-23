# -*- coding: utf-8 -*-
"""
import_excel.py —— 把 Excel 里的内容自动搬进数据库
运行方法：python import_excel.py

注意：本脚本用于组内 Excel 协作流程，需要 data/ 下存在两个 .xlsx 源文件。
仓库内未放 Excel（二进制不便审阅），数据已导出为 data/*.csv，
    直接用 python build_db.py 即可建库，不需要本脚本。
"""
import os
import sqlite3

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "market.db")

# 三个数据来源（文件名要和文件夹里的一致，改了名字这里也要改）
DATA_PACK = os.path.join(HERE, "data", "国贸数据包_v2.1_AHP九指标_HS0712_5国.xlsx")
WEIGHT_BOOK = os.path.join(HERE, "data", "九维指标权重表_已修正1107.xlsx")

EXPECT_COUNTRIES = {"USA", "Japan", "Rep. of Korea", "Germany", "Viet Nam"}


def _read_sheet(path, sheet_name):
    """读一个 Sheet，读不到就返回 None（并说清为什么）"""
    if not os.path.exists(path):
        print(f"[跳过] 找不到文件：{os.path.basename(path)}")
        return None
    try:
        return pd.read_excel(path, sheet_name=sheet_name)
    except ValueError as e:
        # 最常见：Sheet 名字改了
        print(f"[失败] 打不开 Sheet「{sheet_name}」：{e}")
        return None


def import_country_metrics():
    """搬第 3 张表：五国九指标数据"""
    df = _read_sheet(DATA_PACK, "1-九指标数据总表")
    if df is None:
        return False

    df = df.dropna(subset=["国家"])          # 丢掉表格里的空行
    df = df[["国家", "中文名", "指标名称", "数值", "数据来源", "可信度", "备注"]]

    conn = sqlite3.connect(DB_FILE)
    df.to_sql("country_metrics", conn, if_exists="replace", index=False)
    conn.close()
    print(f"[成功] 国家指标数据 {len(df)} 行")
    return True


def _find_col(df, keyword):
    """按关键字找列名，返回真实列名；找不到返回 None。
    这样即使表头写成"怎么打分（0–5分）"也能认出来，比要求一字不差宽容。"""
    for c in df.columns:
        if keyword in str(c):
            return c
    return None


def import_weights():
    """搬第 1 张表：九维权重"""
    df = _read_sheet(WEIGHT_BOOK, "权重表")
    if df is None:
        return False

    # 必需列（用关键字容错匹配）
    col_map = {}
    for want, kw in [("序号", "序号"), ("维度名称", "维度名称"), ("这个维度看什么", "看什么"),
                     ("数据从哪来", "从哪来"), ("权重", "权重"), ("怎么打分", "打分"),
                     ("分数方向", "方向")]:
        real = _find_col(df, kw)
        if real is None:
            print(f"[失败] 权重表里找不到「{want}」这一列（按关键字「{kw}」搜的）")
            print(f"      现在表头是：{list(df.columns)}")
            print("      让国贸同学改 Excel 表头，不要改代码")
            return False
        col_map[want] = real

    df = df.rename(columns={v: k for k, v in col_map.items()})
    df = df[["序号", "维度名称", "这个维度看什么", "数据从哪来", "权重", "怎么打分", "分数方向"]]
    df = df.dropna(subset=["维度名称"])
    df["权重"] = pd.to_numeric(df["权重"], errors="coerce").fillna(0)

    conn = sqlite3.connect(DB_FILE)
    df.to_sql("weights", conn, if_exists="replace", index=False)
    conn.close()

    total = float(df["权重"].sum())
    print(f"[成功] 权重表 {len(df)} 行，权重合计 {total:.2f}")
    if abs(total - 1) > 0.01:
        print(f"      [!] 权重合计不等于 1（现在是 {total}），后面算分会偏，请国贸核对")
    return True


def import_channels():
    """搬第 2 张表：五国渠道规则"""
    df = _read_sheet(WEIGHT_BOOK, "渠道规则")
    if df is None:
        return False

    col_map = {}
    for want, kw in [("国家", "国家"), ("国家中文名", "中文名"), ("推荐渠道", "推荐渠道"),
                     ("客户画像", "客户画像"), ("为什么是这个渠道", "为什么")]:
        real = _find_col(df, kw)
        if real is None:
            print(f"[失败] 渠道表里找不到「{want}」这一列")
            print(f"      现在表头是：{list(df.columns)}")
            return False
        col_map[want] = real

    df = df.rename(columns={v: k for k, v in col_map.items()})
    df = df[["国家", "国家中文名", "推荐渠道", "客户画像", "为什么是这个渠道"]]
    df = df.dropna(subset=["国家"])
    df["国家"] = df["国家"].astype(str).str.strip()   # 去掉首尾空格，"Rep.of Korea" 这类错误会在这里暴露

    conn = sqlite3.connect(DB_FILE)
    df.to_sql("channels", conn, if_exists="replace", index=False)
    conn.close()
    print(f"[成功] 渠道规则 {len(df)} 行")
    return True


def show_check():
    """查一遍：三张表各有多少行、国家名对不对得上"""
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    print("\n———— 检查结果 ————")
    for t in ["country_metrics", "weights", "channels"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:18s} {cur.fetchone()[0]:>4d} 行")
        except sqlite3.OperationalError:
            print(f"  {t:18s}    表不存在（先跑 python db.py）")

    print("\n国家名核对（三张表必须完全一致，差一个空格就会匹配不上）：")
    cur.execute("SELECT DISTINCT 国家 FROM country_metrics ORDER BY 国家")
    m = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT 国家 FROM channels ORDER BY 国家")
    c = {r[0] for r in cur.fetchall()}

    for name in sorted(m):
        flag = "OK" if name in c else "!! 渠道表里没有这个国家（名字对不上？）"
        print(f"  数据表：{name!r:20s} {flag}")

    only_c = c - m
    if only_c:
        print(f"  只在渠道表出现、数据表里没有：{only_c}")

    missing_country = EXPECT_COUNTRIES - m
    if missing_country:
        print(f"\n  [!] 数据表里少了这些国家：{missing_country}")

    conn.close()


if __name__ == "__main__":
    print("开始搬运……\n")
    import_country_metrics()
    import_weights()
    import_channels()
    show_check()
