# -*- coding: utf-8 -*-
"""
app.py —— 系统的界面（网页）
运行方法（在本文件夹里打开命令行）：
    streamlit run app.py

【唐紫涵】分三步做：
    Step 1（已有）：页面能打开、有输入框、点按钮有反应
    Step 2（今天）：把"示例数据"换成真数据 —— 调 scoring.evaluate()
    Step 3（11/10-11）：把结果卡片做漂亮，加得分明细和异常提示
"""

import os
import sqlite3

import pandas as pd
import streamlit as st

import scoring

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "market.db")

st.set_page_config(page_title="食品出口市场评估系统", layout="wide")


# ---------- 小工具 ----------
@st.cache_data
def load_channels():
    """从数据库读渠道规则，做成 {国家: {...}} 方便查"""
    if not os.path.exists(DB_FILE):
        return {}
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql("SELECT * FROM channels", conn)
    except Exception:
        return {}
    finally:
        conn.close()
    return {row["国家"]: row.to_dict() for _, row in df.iterrows()}


def badge(score):
    """按分数给个颜色标"""
    if score >= 75:
        return "🟢"
    if score >= 65:
        return "🟡"
    return "🟠"


def _render_detail(r, channels):
    """渲染单个国家的九指标明细 + 风险扣分 + 渠道建议"""
    # —— 九指标得分明细 ——
    st.markdown(" **九指标得分明细** ")
    detail_rows = []
    for d in r["指标明细"]:
        detail_rows.append({
            "指标": d["指标"],
            "实际值": str(d["原始值"])[:38],
            "得分(0-5)": d["得分"],
            "权重": f"{d['权重'] * 100:.0f}%",
            "加权分": round(d["加权分"], 3),
            "打分依据": d["依据"],
        })
    st.markdown(pd.DataFrame(detail_rows).to_html(index=False), unsafe_allow_html=True)

    if r["风险明细"]:
        st.markdown(" **风险扣分项** ")
        for rd in r["风险明细"]:
            st.write(f"- {rd}")

    # —— 渠道建议 ——
    ch = channels.get(r["国家"])
    if ch:
        st.markdown(" **推荐渠道** ")
        st.write(ch.get("推荐渠道", "（未填）"))
        st.markdown(" **目标客户画像** ")
        st.write(ch.get("客户画像", "（未填）"))
        st.markdown(" **为什么选这个渠道** ")
        st.write(ch.get("为什么是这个渠道", "（未填）"))
    else:
        st.info("这个国家还没有渠道建议（渠道表里没有它的名字，检查国家英文名是否完全一致）")


# ---------- 头部 ----------
st.title("食品出口市场评估系统")
st.caption("输入产品信息，系统按 AHP 九指标加权模型评估，输出最值得进入的国家及渠道建议")

# ---------- 输入区 ----------
st.header("第一步：填写产品信息")

col1, col2 = st.columns(2)
with col1:
    product_name = st.text_input("产品名称", value="脱水蔬菜", placeholder="例如：脱水胡萝卜粒")
    hs_code = st.text_input("海关 HS 编码", value="0712", placeholder="例如：0712")
with col2:
    target_count = st.number_input("想看前几个国家？", min_value=1, max_value=5, value=5)
    note = st.text_area("备注（可不填）", placeholder="例如：走 B 端渠道，希望稳定供货")

st.divider()
run_button = st.button("开始评估", type="primary")

# ---------- 结果区 ----------
st.header("第二步：查看推荐结果")

if not run_button:
    st.info("填好上面的信息，点「开始评估」")
    st.stop()

if not product_name or not hs_code:
    st.warning("请填写产品名称和 HS 编码")
    st.stop()

# HS 编码校验：本版本只支持 0712
if not hs_code.strip().startswith("071"):
    st.error(f"暂时只支持脱水蔬菜（HS 0712）品类，你输入的是 {hs_code}。"
             f"其他品类的数据还在采集，后续版本会开放。")
    st.stop()

if not os.path.exists(DB_FILE):
    # 首次运行 / 云端部署时数据库还不存在，直接从 data/*.csv 自动构建，避免"打不开就用不了"
    with st.spinner("首次运行，正在构建数据库……"):
        try:
            import build_db
            build_db.build(verbose=False)
        except Exception as e:
            st.error(f"自动构建数据库失败：{e}")
            st.stop()

if not os.path.exists(DB_FILE):
    st.error("找不到数据库 market.db。请先运行 python build_db.py（或 python db.py + python import_excel.py）")
    st.stop()

try:
    results = scoring.evaluate(top_n=int(target_count))
except Exception as e:
    st.error(f"评分引擎出错了：{e}")
    st.exception(e)
    st.stop()

channels = load_channels()

st.success(f"评估完成：{product_name}（HS {hs_code}），共 {len(results)} 个国家参与排名")
st.caption("评分说明：九指标各打 0–5 分 → 乘 AHP 权重求和 → 换算成百分制 → 减去风险扣分")

_top = sorted(results[0]["指标明细"], key=lambda d: d["得分"] * d["权重"], reverse=True)[:2]
_adv = "、".join(d["指标"].split(" ")[1] for d in _top)
st.info(f"📊  **结论** ：综合评估， **{results[0]['中文名']}**  是当前最值得优先进入的市场，得分 {results[0]['总分']:.1f}，主要优势指标为 {_adv}。")

with st.expander("📘 评分方法说明（这套分是怎么算出来的）", expanded=False):
    st.markdown("""**一、九指标打分**：九项指标各按分档规则打 0–5 分。规则写成阶梯判定，从上往下第一条满足即为该档；相邻门槛带等号，边界值归入较好的一档，不重不漏。

**二、AHP 权重**：九项权重由层次分析法（AHP）成对比较确定，通过一致性检验（CR < 0.1）后冻结，不随国家变化。权重前三项为：利润空间 27%、文化适配度 16%、合规维护成本 15%。

**三、百分制换算**：九项得分 × 各自权重求和 = 基础分（5 分制），再 × 20 换算成百分制。**总分 = 九指标加权合计 × 20 − 风险扣分**。

**四、风险扣分（第三层，与加权打分相互独立，命中才扣）**：

| 触发条件 | 扣分 |
|---|---|
| 目的国对华加征性关税（美国 301 清单） | −5 |
| 认证费用超过毛利 5%（德国有机认证 + 加严抽检） | −2 |
| 结算货币年汇率波幅超过 10%（越南盾） | −2 |

**五、数据口径**：品类为脱水蔬菜（HS 0712），贸易数据取自 UN Comtrade 官方 API 的原始存档，每个数字可追溯到采集日期。区间型数据统一取区间下限——下限代表正常水平成本，上界是极端情况，而极端情况已由第三层风险扣分单独惩罚，不重复计算。

**六、结论怎么读**：分差在 5 分以内的名次应理解为"同一梯队"而非严格优劣——例如当前第 3、4 名仅差 1.2 分，敏感性分析显示二者在部分权重方案下会换位；而日本的领先优势（18 分以上）在 8 种权重调整方案下全部保持稳定。""")
for r in results:
    if r["淘汰"]:
        st.warning(f"{r['中文名']}（{r['国家']}）未通过硬门槛：{r['淘汰原因']}")
        continue

    title = f"{badge(r['总分'])} 第 {r['排名']} 名：{r['中文名']}（{r['国家']}）—— {r['总分']:.1f} 分"

    if r["排名"] == 1:
        # ===== 第 1 名：高亮卡片（绿色框）=====
        st.success(f"🥇  **第 1 名｜{r['中文名']}（{r['国家']}）** 　总分 {r['总分']:.1f} 分")
        c1, c2, c3 = st.columns(3)
        c1.metric("基础分（百分制）", f"{r['基础分'] * 20:.1f}")
        c2.metric(label="风险扣分", value=f"-{r['风险扣分']:.0f}" if r['风险扣分'] > 0 else "0")
        c3.metric("最终总分", f"{r['总分']:.1f}")

        with st.container(border=True):
            st.markdown(" **📊 九指标明细与渠道建议** ")
            _render_detail(r, channels)

    else:
        # ===== 第 2~5 名：折叠面板 =====
        with st.expander(title, expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("基础分（百分制）", f"{r['基础分'] * 20:.1f}")
            c2.metric(label="风险扣分", value=f"-{r['风险扣分']:.0f}" if r['风险扣分'] > 0 else "0")
            c3.metric("最终总分", f"{r['总分']:.1f}")
            _render_detail(r, channels)

with st.expander("看五国横向对比表"):
    compare = []
    for r in results:
        if r["淘汰"]:
            continue
        row = {"国家": r["中文名"], "总分": r["总分"]}
        for d in r["指标明细"]:
            row[d["指标"].split(" ")[0]] = d["得分"]
        compare.append(row)
    st.markdown(pd.DataFrame(compare).to_html(index=False), unsafe_allow_html=True)
    st.caption("C1 规模 / C2 增速 / C3 文化 / C4 利润 / C5 渠道 / C6 合规 / C7 竞争 / C8 退货 / C9 物流")

st.divider()
st.caption("数据口径：脱水蔬菜（HS 0712） ｜ 贸易数据：UN Comtrade 官方 API 原始存档，可追溯采集日期 ｜ "
           "评估范围：日本、美国、德国、韩国、越南 ｜ 模型：AHP 九指标加权 + 三层漏斗（硬门槛 → 加权评分 → 风险扣分）")
