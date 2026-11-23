# -*- coding: utf-8 -*-
"""
scoring.py —— 评分引擎（系统的心脏）

运行方法：
    python scoring.py            # 跑一遍五国，把结果打印出来
    python scoring.py --detail   # 额外打印每个国家九个指标各得几分

这个文件做三件事（对应三层漏斗）：
    第一层  硬门槛过滤   —— 有些国家直接淘汰，不进打分
    第二层  九指标加权   —— 每个指标打 0-5 分，乘权重，加起来
    第三层  风险扣分     —— 汇率、关税、认证费，再扣几分

【陈子然】你要做的是：用这个脚本的输出，跟你手算的表逐格对照。
        如果哪一格不一样，先看这个文件里对应指标的"分档规则"，再看你的算法。
"""

import argparse
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, "market.db")

# ============ 九指标权重（来自 AHP，冻结不可改）============
WEIGHTS = {
    "C1 目标品类进口规模": 0.09,
    "C2 进口增速": 0.05,
    "C3 品类文化适配度": 0.16,
    "C4 利润空间": 0.27,
    "C5 渠道覆盖成本": 0.08,
    "C6 合规维护成本": 0.15,
    "C7 竞争集中度": 0.11,
    "C8 退货与售后成本占比": 0.06,
    "C9 冷链/温控物流适配度": 0.03,
}

COUNTRIES = ["Japan", "USA", "Germany", "Rep. of Korea", "Viet Nam"]
CN_NAME = {"Japan": "日本", "USA": "美国", "Germany": "德国",
           "Rep. of Korea": "韩国", "Viet Nam": "越南"}

# ============ C3 人工定分（11/9 陈子然拍板，程序不再猜关键词）============
# 为什么改成人工定分：C3 的原始数据是给人读的描述文本，不是给程序读的。
# 程序只能靠关键词判断，会误伤——例如德国"偏好可追溯供应链"被读成"需调整"（3分），
# 而"偏好可追溯"其实是加分项；日本"味噌汤料、拉面料包刚需"只给到 4 分，
# 可日本是五国最大进口国（3.54 亿美元），越南只有 0.73 亿却因为"饮食习惯相近"拿了 5 分。
# 所以五国各定一个分数，理由可讲、可复核。拍板记录见《陈子然_C2C3_手算表与拍板》。
#
# 格式：国家: (得分, 一句话理由)
# 这句话会直接显示在网页的"打分依据"列——答辩时老师追问"为什么是5分"，
# 指着这一行就能答，所以这里写的是正式表述，不是内部备注。
C3_MANUAL = {
    "Japan":         (5, "味噌汤/拉面料包刚性需求，2024 年进口 3.54 亿美元为五国第一"),
    "Rep. of Korea": (5, "泡菜料包/汤料用干蔬菜用量大，干蒜需求突出，饮食高度契合"),
    "Viet Nam":      (4, "饮食相近+华人社区需求，但市场规模小（0.73 亿）且以边贸低价货为主"),
    "USA":           (4, "亚裔社区刚需+露营应急食品市场，主流人群接受度有限"),
    "Germany":       (3, "素食有机趋势是机会，但口味差异大、可追溯要求极高，落地需改配方"),
}

# ============ 第一层：硬门槛 ============
# 只要命中一条，该国直接淘汰，不参与排名（结果里标注淘汰原因）
HARD_GATES = [
    ("法规不可准入", "该国禁止进口中国产脱水蔬菜，或需要中方无法取得的强制前置注册"),
    ("宗教合规冲突", "该国强制要求清真/犹太洁食认证且我方短期无法取得"),
    ("保质期余量不足", "海运在途 + 清关 + 上架周期 > 保质期的 60%（保质期余量指数 < 0.4）"),
]


# ============ 从数据库取数 ============
def load_metrics(db=DB_FILE):
    """把 country_metrics 表读成 {国家: {指标全名: 数值文本}}"""
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT 国家, 指标名称, 数值 FROM country_metrics")
    rows = cur.fetchall()
    conn.close()

    data = {}
    for country, metric, value in rows:
        data.setdefault(country.strip(), {})[metric.strip()] = (value or "").strip()
    return data


def num(text):
    """从一段文字里抠出第一个数字。'≈ 15%（关税4.9%...）' → 15.0；抠不到返回 None"""
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else None


def num_after(text, keyword):
    """抠出关键字后面紧跟的那个数字。
    C2 的数值是「3年 CAGR -0.7%（2023→2024 +0.3%）」，
    直接取第一个数字会拿到"3"（年份），必须取「CAGR」后面的。
    """
    if text is None:
        return None
    m = re.search(re.escape(keyword) + r"[^-\d]*(-?\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else num(text)


def num_growth(text):
    """C2 专用：增长率一律取「带负号或紧跟百分号、且不是年份(19xx/20xx)」的那个数。
    能处理三种写法：
        「3年 CAGR -0.7%（2023→2024 +0.3%）」  → -0.7
        「2022→2023 -16.1%」                  → -16.1
        「+6.5%」                             →  6.5
    """
    if text is None:
        return None
    t = str(text)
    # 优先取 CAGR 后面那个
    m = re.search(r"CAGR[^-\d]*(-?\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1))
    # 否则扫描所有"带负号"或"带百分号的数"，跳过 19xx/20xx 这种年份
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*%?", t):
        val = float(m.group(1))
        tail = t[m.end():m.end() + 1]
        is_year = 1990 <= abs(val) <= 2100 and abs(val) == int(abs(val)) and tail != "%"
        if is_year:
            continue
        if m.group(1).startswith("-") or tail == "%":
            return val
    return None


def num_pct(text):
    """抠出「≈ 15%（...）」里那个带百分号的数——即出现在第一个（之前的数。
    C4 的写法是「≈ 50%（关税43.3% + 物流≈6.5%...）」，取第一个数才是总成本 50；
    直接取第一个数字虽然也是 50，但万一写法变成「50%（约43.3%关税）」会误取，
    所以这里明确取括号外、百分号前的第一个数字。
    """
    if text is None:
        return None
    head = str(text).split("（")[0].split("(")[0]
    return num(head)


def get(data, country, metric):
    for k, v in data.get(country, {}).items():
        if k.startswith(metric.split(" ")[0]):      # 用 "C1"/"C4" 这种编号匹配
            return v
    return ""


# ============ 第二层：九个指标的打分函数 ============
# 每个函数输入"实际数值"，输出 0-5 分。规则跟《权重表》F 列一一对应。

def score_c1(text):
    """C1 进口规模（亿美元）：≥3→5；2-3→4；1-2→3；0.8-1→2；<0.8→1；无数据→0"""
    v = num(text)
    if v is None:
        return 0, "无数据"
    v = v / 1e8 if v > 1e6 else v          # 兼容"353898214"和"3.54"两种写法
    if v >= 3:
        return 5, f"{v:.2f} 亿美元 ≥3"
    if v >= 2:
        return 4, f"{v:.2f} 亿美元 2-3"
    if v >= 1:
        return 3, f"{v:.2f} 亿美元 1-2"
    if v >= 0.8:
        return 2, f"{v:.2f} 亿美元 0.8-1"
    return 1, f"{v:.2f} 亿美元 <0.8"


def score_c2(text):
    """C2 进口增速（%）：≥5→5；0-5→4；-3-0→3；-10--3→2；<-10→1
    数据写的是「3年 CAGR -0.7%」，要取 CAGR 后面那个数，不是年份的 3"""
    v = num_growth(text)
    if v is None:
        return 0, "无数据"
    if v >= 5:
        return 5, f"{v:+.1f}% ≥+5"
    if v >= 0:
        return 4, f"{v:+.1f}% 0~+5"
    if v >= -3:
        return 3, f"{v:+.1f}% -3~0"
    if v >= -10:
        return 2, f"{v:+.1f}% -10~-3"
    return 1, f"{v:+.1f}% <-10"


def score_c3(text, country=None):
    """C3 文化适配度（定性，人工定档）：高度契合5；较契合4；需调整3；宗教限制2；不接受1

    【11/9 起改为人工定分】国家在 C3_MANUAL 里就直接取那个分数，不再读文本。
    下面的关键词逻辑只作为兜底（比如以后加了第六个国家、还没定分时用）。

    判定顺序很重要：先判宗教限制，再判契合度——否则「无宗教限制」会被误判成有宗教限制。
    另外「刚需」不等于「零容忍」，「零容忍」说的是品控要求高，属于"需调整规格"档，不是"不接受"。"""
    if country and country in C3_MANUAL:
        s, reason = C3_MANUAL[country]
        return s, reason

    t = str(text)
    if not t or t == "" or t == "无":
        return 0, "无数据"
    # 先看宗教：明确"存在宗教限制"才降档，"无宗教限制"属于反向表述，跳过
    if "存在宗教限制" in t or "明确宗教限制" in t or "宗教限制严格" in t:
        return 2, "存在宗教限制"
    if "高度契合" in t or "饮食习惯相近" in t or "饮食相近" in t:
        return 5, "饮食习惯高度契合"
    if "刚需" in t or "量大" in t or "需求突出" in t:
        return 4, "品类需求稳定（刚需/量大）"
    if "契合" in t:
        return 4, "饮食习惯较契合"
    if "零容忍" in t or "要求高" in t or "需调整" in t or "偏好" in t:
        return 3, "需调整规格/品控要求高"
    if "不接受" in t:
        return 1, "基本不接受"
    return 3, "定性资料不足，按中性处理"


def score_c4(text):
    """C4 利润空间（总成本占货值比，%）：≤20→5；20-30→4；30-40→3；40-50→2；>50→1
    注意：这是"成本占比"，越低越好。取的是第一个百分号前的数，
    括号里那些细项（关税43.3%、物流6.5%）不参与"""
    v = num_pct(text)
    if v is None:
        return 0, "无数据"
    if v <= 20:
        return 5, f"成本占比 {v:.0f}% ≤20%"
    if v <= 30:
        return 4, f"成本占比 {v:.0f}% 20-30%"
    if v <= 40:
        return 3, f"成本占比 {v:.0f}% 30-40%"
    if v <= 50:
        return 2, f"成本占比 {v:.0f}% 40-50%"
    return 1, f"成本占比 {v:.0f}% >50%"


def score_c5(text):
    """C5 渠道覆盖成本（档位词）：低5；中等偏低4；中等3；中等偏高2；极高1
    注意：成本越低分越高"""
    t = str(text)
    if "极高" in t:
        return 1, "极高"
    if "中等偏高" in t:
        return 2, "中等偏高"
    if "中等偏低" in t:
        return 4, "中等偏低"
    if "中等" in t:
        return 3, "中等"
    if "低" in t:
        return 5, "低"
    return 0, "无数据"


def score_c6(text):
    """C6 合规维护成本（占销售额 %）：≤1→5；1-2→4；2-3→3；3-5→2；>5→1"""
    v = num(text)
    if v is None:
        return 0, "无数据"
    if v <= 1:
        return 5, f"{v:.1f}% ≤1%"
    if v <= 2:
        return 4, f"{v:.1f}% 1-2%"
    if v <= 3:
        return 3, f"{v:.1f}% 2-3%"
    if v <= 5:
        return 2, f"{v:.1f}% 3-5%"
    return 1, f"{v:.1f}% >5%"


def score_c7(text):
    """C7 竞争集中度（中国份额 %）：<35→5；35-65→4；65-80→3；80-90→2；>90→1
    注意：份额越低分越高（说明还有别人在卖，市场没被中国货占满）"""
    v = num(text)
    if v is None:
        return 0, "无数据"
    if v < 35:
        return 5, f"中国份额 {v:.1f}% <35%"
    if v < 65:
        return 4, f"中国份额 {v:.1f}% 35-65%"
    if v < 80:
        return 3, f"中国份额 {v:.1f}% 65-80%"
    if v <= 90:
        return 2, f"中国份额 {v:.1f}% 80-90%"
    return 1, f"中国份额 {v:.1f}% >90%"


def score_c8(text):
    """C8 退货售后成本（占销售额 %）：0-1→5；1-2→4；2-3→3；3-4→2；≥4→1"""
    v = num(text)
    if v is None:
        return 0, "无数据"
    if v <= 1:
        return 5, f"{v:.0f}% 0-1%"
    if v <= 2:
        return 4, f"{v:.0f}% 1-2%"
    if v <= 3:
        return 3, f"{v:.0f}% 2-3%"
    if v <= 4:
        return 2, f"{v:.0f}% 3-4%"
    return 1, f"{v:.0f}% ≥4%"


def score_c9(text):
    """C9 物流时效（天）：≤5→5；5-15→4；15-20→3；20-40→2；≥40→1
    注意：这里取的是运输天数，天数越少分越高"""
    t = str(text)
    # 从"釜山/仁川 1-3 天"这类文字里取天数区间的较小值
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*天", t)
    if m:
        days = float(m.group(1))
    else:
        m2 = re.search(r"(\d+)\s*天", t)
        if not m2:
            return 0, "无数据"
        days = float(m2.group(1))
    if days <= 5:
        return 5, f"{days:.0f} 天 ≤5"
    if days <= 15:
        return 4, f"{days:.0f} 天 5-15"
    if days <= 20:
        return 3, f"{days:.0f} 天 15-20"
    if days <= 40:
        return 2, f"{days:.0f} 天 20-40"
    return 1, f"{days:.0f} 天 >40"


SCORERS = {
    "C1 目标品类进口规模": score_c1,
    "C2 进口增速": score_c2,
    "C3 品类文化适配度": score_c3,
    "C4 利润空间": score_c4,
    "C5 渠道覆盖成本": score_c5,
    "C6 合规维护成本": score_c6,
    "C7 竞争集中度": score_c7,
    "C8 退货与售后成本占比": score_c8,
    "C9 冷链/温控物流适配度": score_c9,
}


# ============ 第三层：风险扣分 ============
def risk_deductions(country):
    """返回 (扣分合计, [扣分明细])。规则来自母版第三层。"""
    detail = []
    total = 0.0

    # 汇率年波幅 >10%，扣 2 分（简化口径：五国里只有新兴市场货币会触发）
    if country in ("Viet Nam",):
        total += 2
        detail.append("越南盾年波幅 >10%（扣 2）")

    # 对华加征性关税（美国 301 关税属于此类），扣 5 分
    if country in ("USA",):
        total += 5
        detail.append("美国对华加征关税（301 清单，扣 5）")

    # 认证费 > 毛利 5%，扣 2 分（德国有机认证+加严抽检）
    if country in ("Germany",):
        total += 2
        detail.append("德国有机认证年费+加严抽检 > 毛利 5%（扣 2）")

    return total, detail


# ============ 总流程 ============
def evaluate(data=None, top_n=5):
    """跑完三层漏斗，返回排序后的结果列表"""
    if data is None:
        data = load_metrics()

    results = []
    for country in COUNTRIES:
        item = {
            "国家": country,
            "中文名": CN_NAME.get(country, country),
            "淘汰": False,
            "淘汰原因": "",
            "指标明细": [],
            "基础分": 0.0,
            "风险扣分": 0.0,
            "风险明细": [],
            "总分": 0.0,
        }

        # —— 第一层：硬门槛 ——（本轮五国都过，规则已写在文件顶部备查）
        # 说明：数据包里 C3 若有"宗教限制"且无法解决，就在这里淘汰
        c3_raw = get(data, country, "C3")
        if "明确宗教限制" in str(c3_raw) or "禁止" in str(c3_raw):
            item["淘汰"] = True
            item["淘汰原因"] = "宗教合规冲突"
            results.append(item)
            continue

        # —— 第二层：九指标加权 ——
        base = 0.0
        for metric, weight in WEIGHTS.items():
            raw = get(data, country, metric)
            # C3 走人工定分（不读文本，避免关键词误伤）；其余八项照常按数值打档
            if metric.startswith("C3"):
                score, why = score_c3(raw, country)
            else:
                score, why = SCORERS[metric](raw)
            weighted = score * weight
            base += weighted
            item["指标明细"].append({
                "指标": metric, "原始值": raw, "得分": score,
                "权重": weight, "加权分": round(weighted, 4), "依据": why,
            })
        item["基础分"] = round(base, 3)

        # —— 第三层：风险扣分 ——
        deduct, detail = risk_deductions(country)
        item["风险扣分"] = deduct
        item["风险明细"] = detail

        # 总分换算成百分制（5 分制 × 20，再扣风险分）
        item["总分"] = round(item["基础分"] * 20 - deduct, 1)
        results.append(item)

    # 淘汰的排最后；其余按总分降序
    results.sort(key=lambda x: (x["淘汰"], -x["总分"]))
    for i, r in enumerate(results, 1):
        r["排名"] = i if not r["淘汰"] else "-"
    return results[:top_n] if top_n else results


def print_result(results, detail=False):
    print("=" * 74)
    print("食品出口市场评估结果（脱水蔬菜 HS 0712）")
    print("=" * 74)
    print(f"{'排名':<4}{'国家':<8}{'中文名':<6}{'基础分(百分制)':<14}{'风险扣分':<10}{'总分':<8}")
    print("-" * 74)
    for r in results:
        if r["淘汰"]:
            print(f"{'-':<4}{r['国家']:<8}{r['中文名']:<6}淘汰：{r['淘汰原因']}")
            continue
        print(f"{r['排名']:<4}{r['国家']:<8}{r['中文名']:<6}"
              f"{r['基础分'] * 20:<14.1f}{r['风险扣分']:<10.0f}{r['总分']:<8.1f}")

    if detail:
        for r in results:
            if r["淘汰"]:
                continue
            print("\n" + "=" * 74)
            print(f"{r['排名']}. {r['中文名']}（{r['国家']}）总分 {r['总分']}")
            print("=" * 74)
            for d in r["指标明细"]:
                print(f"  {d['指标']:<18} 得分 {d['得分']}  × 权重 {d['权重']:.2f}"
                      f" = {d['加权分']:.3f}   ← {d['依据']}")
            print(f"  基础分合计 {r['基础分']:.3f} → 百分制 {r['基础分'] * 20:.1f}")
            for rd in r["风险明细"]:
                print(f"  风险扣分：{rd}")
            print(f"  最终总分 {r['总分']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--detail", "-d", action="store_true", help="打印九指标明细")
    args = p.parse_args()
    print_result(evaluate(), detail=args.detail)
