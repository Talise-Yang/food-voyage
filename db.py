# -*- coding: utf-8 -*-
"""
db.py —— 建数据库（创建一个 market.db 文件，里面画三张表）
运行方法：python db.py

【唐紫涵】这个文件不用改，直接跑。跑完文件夹里会多一个 market.db。
"""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "market.db")


def create_tables():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # ---- 抽屉1：权重表 ----
    # 列名必须和《九维指标权重表》的"权重表"Sheet 表头一模一样
    cur.execute("""
    CREATE TABLE IF NOT EXISTS weights (
        序号          INTEGER,
        维度名称        TEXT,
        这个维度看什么    TEXT,
        数据从哪来      TEXT,
        权重          REAL,
        怎么打分       TEXT,
        分数方向       TEXT
    )
    """)

    # ---- 抽屉2：渠道规则表 ----
    cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        国家          TEXT,
        国家中文名     TEXT,
        推荐渠道       TEXT,
        客户画像       TEXT,
        为什么是这个渠道  TEXT
    )
    """)

    # ---- 抽屉3：各国指标数据表 ----
    # 列名必须和数据包 Sheet「1-九指标数据总表」的表头一模一样
    cur.execute("""
    CREATE TABLE IF NOT EXISTS country_metrics (
        国家      TEXT,
        中文名    TEXT,
        指标名称  TEXT,
        数值     TEXT,
        数据来源  TEXT,
        可信度   TEXT,
        备注     TEXT
    )
    """)

    conn.commit()
    conn.close()
    print("搞定！三张表建好了，数据库文件叫：", DB_FILE)


if __name__ == "__main__":
    create_tables()
