# -*- coding: utf-8 -*-
"""编排图：节点 + 边 + 两个人工暂停点

    START → 解析员 → [人审1] → 匹配员 →(score<40→劝退) → [人审2] → 建议员 → END

两个 gate 是空节点，靠 compile(interrupt_before=[...]) 在进入前暂停。
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from nodes import advisor, matcher, parser
from state import State, fold_job

GATE1, GATE2 = "gate1", "gate2"


def _gate(state: State) -> dict:
    """空节点：暂停点本身不改数据，人审的改动由 main.py 写入 edits"""
    return {}


def _route_after_match(state: State) -> str:
    """匹配度 < 40 且用户没选择继续 → 直接劝退，不进建议员（省 token）"""
    score = state.get("match", {}).get("score", 0)
    if score < 40 and not state.get("user_continue", False):
        return "give_up"
    return GATE2


def _give_up(state: State) -> dict:
    """低分终局：不给改写建议，只给差距清单"""
    gaps = state.get("match", {}).get("gaps", [])
    return {
        "status": "done",
        "advice": {"rev": 0, "match_rev": state.get("match", {}).get("rev", 1),
                   "rewrites": [], "questions": [],
                   "actions": [f"差距过大，建议先补：{g['desc']}" for g in gaps[:3]]},
    }


def _before_advisor(state: State) -> dict:
    """进建议员前做版本体检：match 必须基于当前生效的 job 算出"""
    job = fold_job(state.get("job", {}), state.get("edits", []))
    match = state.get("match", {})
    if match.get("job_rev") != job.get("rev"):
        # 理论上不该发生（gate1 后必走匹配员），留痕便于联调定位
        return {"errors": list(state.get("errors", [])) + [{
            "step": "guard", "type": "stale_match",
            "detail": f"match.job_rev={match.get('job_rev')} 与 job.rev={job.get('rev')} 不一致",
            "recovered": False}]}
    return {}


def _finish(state: State) -> dict:
    """收尾：打完成标记，复盘时能区分「跑完」和「中途放弃」"""
    return {"status": "done"}


def build():
    g = StateGraph(State)
    g.add_node("parser", parser.run)
    g.add_node(GATE1, _gate)
    g.add_node("matcher", matcher.run)
    g.add_node(GATE2, _gate)
    g.add_node("guard", _before_advisor)
    g.add_node("advisor", advisor.run)
    g.add_node("give_up", _give_up)
    g.add_node("finish", _finish)

    g.add_edge(START, "parser")
    g.add_edge("parser", GATE1)
    g.add_edge(GATE1, "matcher")
    g.add_conditional_edges("matcher", _route_after_match,
                            {"give_up": "give_up", GATE2: GATE2})
    g.add_edge("give_up", "finish")
    g.add_edge(GATE2, "guard")
    g.add_edge("guard", "advisor")
    g.add_edge("advisor", "finish")
    g.add_edge("finish", END)

    return g.compile(checkpointer=MemorySaver(),
                     interrupt_before=[GATE1, GATE2])
