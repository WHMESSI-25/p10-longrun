# -*- coding: utf-8 -*-
"""长跑入口：选今天的任务 → 跑一次 → 留痕 → 更新统计

设计取舍：
- 不做「真定时」。定时交给 GitHub Actions 的 cron，runner 只管「跑一次」，
  这样本地也能随时重跑任意一天的组合，便于复现。
- 失败不吞。写完记录照样抛，让 Action 变红、开 Issue。
  ROADMAP 的禁令是「跑挂了就手动重启不记录」，不是「不许挂」。
- 判定分三档而不是两档：ok / degraded / failed。
  只分成功失败会把「跑完了但结果可疑」全算成成功，一个月后看不出问题。

用法：
    python runner.py                 跑今天该跑的那一组
    python runner.py --dry-run       只打印今天跑什么（不花钱、不写盘）
    python runner.py --force n3      指定基线 JD（调试用）
    python runner.py --no-stress     不加变体，只跑干净 JD
"""
import argparse
import json
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from resilience import budget
from resilience.retry import CATEGORIES, FIX_HINT, classify
from tasks import variants

ROOT = Path(__file__).resolve().parent
POOL = ROOT / "tasks" / "pool.json"
STATS = ROOT / "stats" / "stats.json"
RUNS = ROOT / "runs"

# DeepSeek 单价（美元/百万 token），与 P8 docs/指标口径.md 一致
PRICE_IN, PRICE_OUT, FX = 0.14 / 1_000_000, 0.28 / 1_000_000, 7.1


def bj_now() -> datetime:
    """统一按北京时间记日期。GitHub runner 是 UTC，直接用会跟本地日记差一天。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def load_stats() -> dict:
    if STATS.exists():
        try:
            return json.loads(STATS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("⚠ stats.json 解析失败，按空账本重建")
    return {"updated": "", "run_count": 0, "ok": 0, "degraded": 0, "failed": 0,
            "total_cost": 0.0, "total_tokens": 0, "daily": {},
            "history": [], "failures": []}


def save_stats(stats: dict) -> None:
    stats["updated"] = bj_now().strftime("%Y-%m-%d %H:%M")
    stats["consecutive_days"] = consecutive_days(stats.get("history", []))
    STATS.parent.mkdir(exist_ok=True)
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def consecutive_days(history: list[dict]) -> int:
    """从今天往前数，连续有记录的天数。今天还没跑就是 0。"""
    dates = {h["date"] for h in history}
    cur = bj_now().date()
    if cur.isoformat() not in dates:
        return 0
    n = 0
    while cur.isoformat() in dates:
        n += 1
        cur -= timedelta(days=1)
    return n


def pick(day_index: int, force: str | None, no_stress: bool) -> tuple[dict, dict | None, str]:
    """按第几次运行挑组合；返回 (基线, 变体信息或 None, JD 正文)"""
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    normal_list, stress_list = pool["normal"], pool["stress"]

    normal = next((n for n in normal_list if n["id"] == force), None) if force else None
    if force and normal is None:
        raise SystemExit(f"任务池里没有 id={force}，可选：{[n['id'] for n in normal_list]}")
    normal = normal or normal_list[day_index % len(normal_list)]

    jd_text = (ROOT / normal["file"]).read_text(encoding="utf-8")

    if no_stress:
        return normal, None, jd_text

    stress = stress_list[day_index % len(stress_list)]
    max_chars = pool.get("rules", {}).get("max_jd_chars_for_long_text", 8000)
    if stress["kind"] == "long_text" and len(jd_text) > max_chars:
        # 本身已是超长 JD 再乘 40 倍就是烧钱，顺延一个变体
        stress = stress_list[(day_index + 1) % len(stress_list)]
        print(f"   ⤷ JD 已有 {len(jd_text)} 字，跳过 long_text，顺延为 {stress['label']}")

    return normal, stress, variants.build(stress["kind"], jd_text)


def verdict(state: dict) -> tuple[str, str]:
    """三档判定。判据写在这里，看板和统计表都读同一份结论。

    type_overridden 不算问题：那是 P9 加的纠正机制（含「优先/加分」的行由代码
    强制判 plus），它生效说明防护在工作，不是流水线出错。
    """
    job = state.get("job", {})
    if job.get("semantic_invalid"):
        return "degraded", "解析未抽到硬性条件（semantic_invalid）"
    if not job.get("must"):
        return "degraded", "硬性条件为空"
    errs = [e for e in state.get("errors", [])
            if e.get("type") not in ("type_overridden",)]
    if errs:
        return "degraded", f"{len(errs)} 条内部告警：{errs[0].get('type', '')}"
    return "ok", ""


def cost_of(state: dict) -> tuple[float, int]:
    tin = sum(t.get("tokens_in", 0) for t in state.get("trace", []))
    tout = sum(t.get("tokens_out", 0) for t in state.get("trace", []))
    return (tin * PRICE_IN + tout * PRICE_OUT) * FX, tin + tout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印今天跑什么")
    ap.add_argument("--force", default=None, help="强制指定基线 JD 的 id")
    ap.add_argument("--no-stress", action="store_true", help="不加变体")
    args = ap.parse_args()

    stats = load_stats()
    day_index = int(stats.get("run_count", 0))
    normal, stress, jd_text = pick(day_index, args.force, args.no_stress)
    resume_text = (ROOT / "data" / "resume.md").read_text(encoding="utf-8")

    print("=" * 64)
    print(f"第 {day_index + 1} 次长跑  |  {bj_now():%Y-%m-%d %H:%M}")
    print(f"基线：{normal['id']} {normal['label']}（{len(jd_text)} 字）")
    print(f"变体：{stress['label'] if stress else '无（干净输入）'}")
    print("=" * 64)

    if args.dry_run:
        print("（dry-run，不调用模型、不写盘）")
        return

    budget.check()

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    print(f"run_id={run_id}")

    t0 = time.time()
    try:
        from pipeline import run_once
    except ImportError as e:
        # 环境没装好不算业务失败。记进账本会把「依赖没装」写成「未归类故障」，
        # 24 天后统计表里多一行假数据，比跑挂更麻烦。
        print(f"✗ 环境缺依赖：{e}\n   先执行 pip install -r requirements.txt")
        raise SystemExit(2)

    try:
        state = run_once(jd_text, resume_text, run_id=run_id)
    except Exception as e:  # noqa: BLE001 —— 长跑要兜住一切，分类后再决定记什么
        cat = classify(e)
        ms = int((time.time() - t0) * 1000)
        print(f"\n✗ 失败（{cat}）：{e}")
        print(traceback.format_exc(limit=3))
        stats["failed"] = stats.get("failed", 0) + 1
        stats["run_count"] = day_index + 1
        stats["failures"].append({
            "run_id": run_id, "date": bj_now().strftime("%Y-%m-%d"),
            "category": cat, "category_cn": CATEGORIES.get(cat, cat),
            "detail": f"{type(e).__name__}: {e}"[:500],
            "fix_hint": FIX_HINT.get(cat, ""),
            "jd": normal["id"], "stress": stress["kind"] if stress else None,
            "ms": ms,
        })
        save_stats(stats)
        raise SystemExit(1)

    ms = int((time.time() - t0) * 1000)
    kind, reason = verdict(state)
    cost, tokens = cost_of(state)
    score = state.get("match", {}).get("score")

    record = {
        "run_id": run_id,
        "date": bj_now().strftime("%Y-%m-%d"),
        "ts": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
        "jd": normal["id"], "jd_label": normal["label"],
        "stress": stress["kind"] if stress else None,
        "stress_label": stress["label"] if stress else "无",
        "verdict": kind, "reason": reason,
        "score": score,
        "must_count": len(state.get("job", {}).get("must", [])),
        "plus_count": len(state.get("job", {}).get("plus", [])),
        "rewrites": len(state.get("advice", {}).get("rewrites", [])),
        "truncated": bool(state.get("job", {}).get("truncated")),
        "tokens": tokens, "cost": round(cost, 6), "ms": ms,
        "errors": [e.get("type", "") for e in state.get("errors", [])],
    }

    RUNS.mkdir(exist_ok=True)
    (RUNS / f"{run_id}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    stats["run_count"] = day_index + 1
    stats[kind] = stats.get(kind, 0) + 1
    stats["total_cost"] = round(stats.get("total_cost", 0.0) + cost, 6)
    stats["total_tokens"] = stats.get("total_tokens", 0) + tokens
    today = record["date"]
    stats.setdefault("daily", {})[today] = round(
        stats["daily"].get(today, 0.0) + cost, 6)
    stats["history"].append(record)
    save_stats(stats)

    mark = {"ok": "✓", "degraded": "△"}.get(kind, "?")
    print(f"\n{mark} {kind}  分数={score}  硬性={record['must_count']} 条  "
          f"耗时={ms / 1000:.1f}s  tokens={tokens}  ¥{cost:.4f}")
    if reason:
        print(f"   原因：{reason}")
    print(f"留痕：runs/{run_id}.json")


if __name__ == "__main__":
    main()
