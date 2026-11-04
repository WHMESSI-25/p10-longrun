# -*- coding: utf-8 -*-
"""P7 状态定义：全局只有一份 state，约定「只增不改」

设计依据见 docs/编排图.md 第三节。几条硬规矩：
1. 人审改动不改本体，写进 edits[]，下游读 effective_job = fold(job, edits)
2. 每条结论带版本锚点（match.job_rev / advice.match_rev），对不上就重跑
3. evidence 必须是原文区间，落库前做子串校验
"""
from typing import Any, TypedDict


class State(TypedDict, total=False):
    # 运行标识与生命周期
    run_id: str                      # 并发隔离用，也是 LangGraph 的 thread_id
    status: str                      # running | paused | abandoned | done

    # 原始输入，永不修改
    input: dict[str, str]            # {"jd": str, "resume": str}

    # ① 解析员产出
    job: dict[str, Any]              # {rev, title, must[], plus[], keywords[], truncated}
    resume: dict[str, Any]           # {skills[], years, edu, sentences[], projects[]}

    # 人审改动日志（只追加）
    edits: list[dict[str, Any]]

    # ② 匹配员产出
    match: dict[str, Any]            # {rev, job_rev, score, breakdown[], gaps[]}

    # ③ 建议员产出
    advice: dict[str, Any]           # {rev, match_rev, rewrites[], questions[], actions[]}

    # 人审决策与运行痕迹
    decisions: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    errors: list[dict[str, Any]]

    # 人审点 2 的选择：低分是否仍然继续
    user_continue: bool


def new_state(jd_text: str, resume_text: str, run_id: str) -> State:
    """初始化一份干净的状态"""
    return State(
        run_id=run_id,
        status="running",
        input={"jd": jd_text, "resume": resume_text},
        job={},
        resume={},
        edits=[],
        match={},
        advice={},
        decisions=[],
        trace=[],
        errors=[],
        user_continue=False,
    )


def fold_job(job: dict, edits: list[dict]) -> dict:
    """把 edits 折叠到 job 上，得到「当前生效版本」

    人审改了 must / plus 之后，下游一律读这个结果，不读 job 本体——
    job 本体保留的是「模型第一次抽出来的样子」，用于复盘对比。
    """
    if not edits:
        return job
    effective = {
        "rev": job.get("rev", 1),
        "title": job.get("title", ""),
        "must": [dict(m) for m in job.get("must", [])],
        "plus": [dict(p) for p in job.get("plus", [])],
        "keywords": list(job.get("keywords", [])),
        "truncated": job.get("truncated", False),
    }
    for e in edits:
        target = e.get("target", "")
        if target == "job.must":
            effective["must"] = [dict(m) for m in e.get("after", [])]
        elif target == "job.plus":
            effective["plus"] = [dict(p) for p in e.get("after", [])]
        effective["rev"] = effective.get("rev", 1) + 1
    return effective


def append(state: State, key: str, item: dict) -> list:
    """只追加：返回新列表，不原地改（保持 state 不可变语义）"""
    return list(state.get(key, [])) + [item]
