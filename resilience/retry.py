# -*- coding: utf-8 -*-
"""失败分类 + 重试（容错层第 1 件）

为什么单独抽一层：
P8 的 llm.py 是无差别重试 2 次——Key 填错（401）也要等 6 秒再撞两次，
真正的限流（429）反而只等 2 秒。重试策略必须按错误类型分开：
值得重试的才重试，不值得的立刻抛，让失败记录保持干净。

分类口径只在这里定义一次：看板、失败统计表、Issue 标签全读这一份，
避免出现「日志说网络错、看板算成未归类」这种对不上的账。
"""
import functools
import json
import socket
import time
import urllib.error

# 失败分类 → 中文说明（统计表的中文列直接取这里）
CATEGORIES = {
    "network": "网络中断/超时",
    "rate_limit": "上游限流 429",
    "auth": "鉴权失败",
    "server": "上游 5xx",
    "schema": "模型输出不可解析",
    "data": "输入数据问题",
    "budget": "成本熔断",
    "unknown": "未归类",
}

# 只有这三类会自己好，才值得等
RETRYABLE = {"network", "rate_limit", "server"}

# 每类失败第一反应该查什么（写进 Issue，省得到时候翻日志）
FIX_HINT = {
    "network": "先看 DNS 与出网；重试已自动做过，连续失败说明不是抖动",
    "rate_limit": "退避已生效；若连续超限要降频或换时段",
    "auth": "Key 失效或额度不足，去仓库 Secrets 换，重试无意义",
    "server": "上游故障，等它恢复；重试已自动做过",
    "schema": "模型没按 JSON 输出，看原始回复判断是提示词还是上下文超长",
    "data": "输入文件缺失/编码异常，检查 data/ 与 tasks/pool.json",
    "budget": "当日成本触顶，确认是否异常烧钱再手动放行",
    "unknown": "没有归类的错误，看 traceback 后补分类规则",
}


class BudgetExceeded(RuntimeError):
    """当日成本超过上限。放这里是为了让 classify 能统一认它。"""


def classify(exc: BaseException) -> str:
    """把一个异常归到八类之一。

    判断顺序有讲究：HTTPError 是 URLError 的子类，必须先判 HTTP 状态码，
    否则 401 会被当成网络错误去重试。
    """
    if isinstance(exc, BudgetExceeded):
        return "budget"

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "rate_limit"
        if exc.code in (401, 403):
            return "auth"
        if 500 <= exc.code < 600:
            return "server"
        return "unknown"

    if isinstance(exc, urllib.error.URLError):
        return "network"

    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError)):
        return "network"

    if isinstance(exc, json.JSONDecodeError):
        return "schema"

    if isinstance(exc, (FileNotFoundError, IsADirectoryError, UnicodeDecodeError)):
        return "data"

    return "unknown"


def with_retry(max_attempts: int = 3, base: float = 2.0, log=None):
    """指数退避重试，只对 RETRYABLE 里的类型生效。

    第 1 次失败等 2s，第 2 次等 4s——比 P8 的固定节奏更长，
    因为 GitHub Actions 里 1 秒的抖动很常见，2 秒往往已经够跨过去。
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last = None
            for i in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001 —— 这里就是要兜住一切再去分类
                    last = e
                    cat = classify(e)
                    if cat not in RETRYABLE or i == max_attempts - 1:
                        raise
                    delay = base ** (i + 1)
                    if log:
                        log(f"[重试] {cat} 第 {i + 1}/{max_attempts} 次失败，"
                            f"{delay:.0f}s 后再试：{e}")
                    time.sleep(delay)
            raise last  # 理论上到不了这

        return wrapper

    return deco
