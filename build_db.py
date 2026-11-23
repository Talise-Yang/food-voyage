# -*- coding: utf-8 -*-
"""
build_db.py —— 从 *.csv 构建 market.db（纯标准库，不需要 pandas）

运行方法：
    python build_db.py

为什么有这个脚本：
    团队内部的数据源是 Excel（国贸组维护），但 Excel 是二进制文件，
    在仓库里既不能 diff 也无法审阅。所以仓库内把三张表导出成 CSV，
    这个脚本负责把 CSV 装回 SQLite。schema 仍然复用 db.py，不另起一套。

对应关系：
    country_metrics.csv → 表 country_metrics（五国 × 九指标）
    weights.csv               → 表 weights（九维权重与打分规则）
    channels.csv              → 表 channels（五国渠道与客户画像）
"""

import csv
import os
import sqlite3

import db

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_data_dir():
    # CSV 可能在根目录（网页上传拍平）或 data/ 子目录，两种结构都兼容
    for cand in (HERE, os.path.join(HERE, "data")):
        if os.path.exists(os.path.join(cand, "country_metrics.csv")):
            return cand
    return os.path.join(HERE, "data")


DATA_DIR = _find_data_dir()

# 表名 → 字段顺序（必须与 db.py 的建表语句一致）
TABLES = {
    "country_metrics": ["国家", "中文名", "指标名称", "数值", "数据来源", "可信度", "备注"],
    "weights": ["序号", "维度名称", "这个维度看什么", "数据从哪来", "权重", "怎么打分", "分数方向"],
    "channels": ["国家", "国家中文名", "推荐渠道", "客户画像", "为什么是这个渠道"],
}


def build(verbose=True):
    """建库 + 灌数据，返回是否成功"""
    db.create_tables()
    conn = sqlite3.connect(db.DB_FILE)
    cur = conn.cursor()

    for table, cols in TABLES.items():
        path = os.path.join(DATA_DIR, f"{table}.csv")
        if not os.path.exists(path):
            if verbose:
                print(f"[跳过] 找不到 {path}")
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        cur.execute(f"DELETE FROM {table}")
        placeholders = ",".join("?" * len(cols))
        cur.executemany(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
            [[r.get(c, "") for c in cols] for r in rows],
        )
        if verbose:
            print(f"[成功] {table:16s} {len(rows):>3d} 行")

    conn.commit()

    # 核对：三张表的国家名必须完全一致，差一个空格就会静默匹配不上
    if verbose:
        cur.execute("SELECT DISTINCT 国家 FROM country_metrics ORDER BY 国家")
        metrics = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT 国家 FROM channels ORDER BY 国家")
        channels = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT SUM(权重) FROM weights")
        total = cur.fetchone()[0] or 0

        print("\n———— 核对 ————")
        for name in metrics:
            flag = "OK" if name in channels else "!! 渠道表里没有这个国家（名字对不上？）"
            print(f"  数据表：{name!r:20s} {flag}")
        print(f"  权重合计 {total:.2f}" + ("" if abs(total - 1) < 0.01 else "  [!] 不等于 1，算分会偏"))

    conn.close()
    return True


if __name__ == "__main__":
    build()
