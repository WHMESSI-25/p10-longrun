# -*- coding: utf-8 -*-
"""跑一次的封装：main.py 是人工入口，这里给无人值守用

与 main.py 的区别只有三点：
1. 不吃命令行参数，直接收文本（长跑的任务来自 tasks/pool.json）
2. 两个人工暂停点一律自动放行（半夜没人守着）
3. 不打印交互提示，但返回值是完整 state，留痕由 runner 负责

刻意不复制 main.py 的人审逻辑，而是 import 它——两处逻辑一旦分叉，
「人工跑出来的结果」和「长跑跑出来的结果」就不可比了。
"""
import time
import uuid

from graph import build
from main import _apply_edit, _apply_reject
from state import new_state

MAX_PAUSES = 10  # 正常只有 2 个暂停点，超过就是死循环


def run_once(jd_text: str, resume_text: str, add_must: str | None = None,
             reject: list[str] | None = None, run_id: str | None = None) -> dict:
    """跑完整条流水线，返回终态 state（与 main.py 存进 runs/ 的是同一个东西）"""
    run_id = run_id or (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])
    config = {"configurable": {"thread_id": run_id}}

    app = build()
    app.invoke(new_state(jd_text, resume_text, run_id), config)

    edited = False
    for _ in range(MAX_PAUSES):
        snap = app.get_state(config)
        if not snap.next:
            return app.get_state(config).values
        node = snap.next[0]
        if node == "gate1" and not edited:
            # edited 保证人工改动只应用一次，否则每轮循环都会追加一条重复条件
            _apply_edit(app, config, snap.values, add_must)
            edited = True
        elif node == "gate2":
            _apply_reject(app, config, snap.values, reject or [])
        app.invoke(None, config)

    raise RuntimeError(f"暂停点循环超过 {MAX_PAUSES} 次，疑似死循环（run_id={run_id}）")
