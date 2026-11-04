# -*- coding: utf-8 -*-
"""成本熔断（容错层第 2 件）

为什么必须有：
P10 是无人值守的。正常一天一条 JD 只要几厘钱，但如果哪天写错进了重试死循环，
或者提示词把上下文撑爆，一晚上能烧掉几十块——人不在场，熔断是唯一的刹车。

口径：按「自然日」累计，读 stats/stats.json 的 daily 字段，
上限由环境变量 DAILY_COST_LIMIT 控制（默认 1 元，比正常消耗高两个数量级，
只拦异常，不拦日常）。
"""
import json
import os
import time
from pathlib import Path

from resilience.retry import BudgetExceeded

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats" / "stats.json"

LIMIT = float(os.environ.get("DAILY_COST_LIMIT", "1.0"))


def today() -> str:
    return time.strftime("%Y-%m-%d")


def spent_today() -> float:
    """今天已经花掉多少钱；文件不存在或格式坏了都按 0 处理，不能因为统计坏了就停跑"""
    if not STATS.exists():
        return 0.0
    try:
        data = json.loads(STATS.read_text(encoding="utf-8"))
        return float(data.get("daily", {}).get(today(), 0.0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0.0


def check() -> None:
    """开跑前检查，超了直接抛，让 runner 记一条 budget 类失败"""
    spent = spent_today()
    if spent >= LIMIT:
        raise BudgetExceeded(
            f"今日已花 ¥{spent:.4f}，超过上限 ¥{LIMIT:.2f}，停止运行")
