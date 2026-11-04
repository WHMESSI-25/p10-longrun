# -*- coding: utf-8 -*-
"""看板生成：读 stats.json，吐出 dashboard/index.html + dashboard/README.md

为什么生成两份：
- index.html 给 GitHub Pages 用（有图表、能一眼看出趋势）
- README.md 给仓库首页用（GitHub 直接渲染表格，不用开 Pages 也能看）
两份读同一份 stats.json，不会出现「网页说 3 次失败、文档说 2 次」的账。

产物是纯静态的，不依赖任何 CDN——长跑产物要能离线打开、也要能活很久。
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "stats" / "stats.json"
OUT_DIR = ROOT / "dashboard"

VERDICT_CN = {"ok": "正常", "degraded": "可疑", "failed": "失败"}
VERDICT_COLOR = {"ok": "#2f8f5b", "degraded": "#c98a1b", "failed": "#c0392b"}

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--line:#e3e2dd;--text:#1f1f1d;--muted:#6b6a65;--accent:#2f8f5b}
@media (prefers-color-scheme:dark){
  :root{--bg:#161615;--card:#1f1f1e;--line:#333230;--text:#ecebe6;--muted:#9a9992;--accent:#5dcaa5}
}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--text);
     font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:22px;font-weight:600;margin:0 0 6px}
h2{font-size:15px;font-weight:600;margin:32px 0 12px}
.sub{color:var(--muted);font-size:13px;margin:0 0 24px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card .label{color:var(--muted);font-size:12px;margin-bottom:6px}
.card .num{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.card .num small{font-size:13px;font-weight:400;color:var(--muted);margin-left:3px}
.timeline{display:flex;flex-wrap:wrap;gap:4px}
.dot{width:22px;height:22px;border-radius:5px;display:flex;align-items:center;justify-content:center;
     font-size:11px;color:#fff;font-variant-numeric:tabular-nums}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:7px;font-size:13px}
.bar-label{width:104px;flex:none;color:var(--muted);font-size:12px;text-align:right}
.bar-track{flex:1;height:16px;background:var(--card);border:1px solid var(--line);border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px}
.bar-num{width:32px;flex:none;font-variant-numeric:tabular-nums;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:11.5px}
td.wrap-cell{white-space:normal;color:var(--muted)}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;color:#fff}
.empty{color:var(--muted);font-size:13px;padding:12px 0}
"""


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def bar(label: str, n: int, total: int, color: str) -> str:
    pct = (n / total * 100) if total else 0.0
    return (f'<div class="bar-row"><span class="bar-label">{esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;'
            f'background:{color}"></div></div><span class="bar-num">{n}</span></div>')


def render(stats: dict) -> str:
    hist = stats.get("history", [])
    fails = stats.get("failures", [])
    ok = stats.get("ok", 0)
    deg = stats.get("degraded", 0)
    bad = stats.get("failed", 0)
    total = ok + deg + bad
    rate = (ok / total * 100) if total else 0.0

    # —— KPI ——
    kpi = f"""
<div class="kpi">
  <div class="card"><div class="label">连续运行</div><div class="num">{stats.get("consecutive_days", 0)}<small>天</small></div></div>
  <div class="card"><div class="label">正常率</div><div class="num">{rate:.0f}<small>%</small></div></div>
  <div class="card"><div class="label">累计运行</div><div class="num">{total}<small>次</small></div></div>
  <div class="card"><div class="label">累计成本</div><div class="num">¥{stats.get("total_cost", 0):.4f}</div></div>
  <div class="card"><div class="label">正常 / 可疑 / 失败</div><div class="num">{ok} / {deg} / {bad}</div></div>
</div>"""

    # —— 时间线：方块上写第几次，颜色表判定 ——
    dots = []
    for i, h in enumerate(hist, 1):
        v = h.get("verdict", "failed")
        tip = f'第{i}次 {h.get("date","")} {h.get("jd_label","")} / {h.get("stress_label","")}：{VERDICT_CN.get(v,v)}'
        dots.append(f'<div class="dot" style="background:{VERDICT_COLOR.get(v,"#888")}" '
                    f'title="{esc(tip)}">{i}</div>')
    timeline = f'<div class="timeline">{"".join(dots)}</div>' if dots else '<div class="empty">还没有运行记录</div>'

    # —— 失败分类（硬失败） ——
    cat_cnt = Counter(f.get("category", "unknown") for f in fails)
    cat_cn = {f.get("category", "unknown"): f.get("category_cn", "") for f in fails}
    blocks = [bar(f"{cat_cn.get(c, c)}", n, len(fails), "#c0392b")
              for c, n in cat_cnt.most_common()]
    fail_chart = "".join(blocks) if blocks else '<div class="empty">暂无硬失败——这不代表没问题，可疑项看下一节</div>'

    # —— degraded 原因 ——
    deg_cnt = Counter(h.get("reason", "") for h in hist if h.get("verdict") == "degraded")
    deg_blocks = [bar(reason or "未标注", n, deg, "#c98a1b")
                  for reason, n in deg_cnt.most_common()]
    deg_chart = "".join(deg_blocks) if deg_blocks else '<div class="empty">暂无可疑记录</div>'

    # —— 明细 ——
    rows = []
    for i, h in enumerate(reversed(hist), 1):
        v = h.get("verdict", "")
        rows.append(
            f"<tr><td>{len(hist) - i + 1}</td><td>{esc(h.get('date',''))}</td>"
            f"<td>{esc(h.get('jd_label',''))}</td><td>{esc(h.get('stress_label',''))}</td>"
            f'<td><span class="tag" style="background:{VERDICT_COLOR.get(v,"#888")}">'
            f"{VERDICT_CN.get(v, v)}</span></td>"
            f"<td>{h.get('score','')}</td><td>{h.get('must_count','')}</td>"
            f"<td>{h.get('ms',0)/1000:.1f}s</td><td>¥{h.get('cost',0):.4f}</td>"
            f'<td class="wrap-cell">{esc(h.get("reason",""))}</td></tr>')
    detail = ("<table><thead><tr><th>#</th><th>日期</th><th>基线 JD</th><th>变体</th>"
              "<th>判定</th><th>分数</th><th>硬性</th><th>耗时</th><th>成本</th><th>备注</th>"
              f"</tr></thead><tbody>{''.join(rows)}</tbody></table>") if rows \
        else '<div class="empty">还没有运行记录</div>'

    # —— 硬失败明细（失败没有 state，只能靠这里回溯） ——
    frows = "".join(
        f"<tr><td>{esc(f.get('date',''))}</td><td>{esc(f.get('category_cn', f.get('category','')))}</td>"
        f"<td>{esc(f.get('jd',''))} / {esc(f.get('stress') or '无')}</td>"
        f'<td class="wrap-cell">{esc(f.get("detail",""))}</td>'
        f'<td class="wrap-cell">{esc(f.get("fix_hint",""))}</td></tr>'
        for f in reversed(fails[-20:]))
    fail_detail = ("<table><thead><tr><th>日期</th><th>分类</th><th>任务</th>"
                   "<th>现象</th><th>下一步</th></tr></thead>"
                   f"<tbody>{frows}</tbody></table>") if fails \
        else '<div class="empty">还没有硬失败记录</div>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P10 长跑看板</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>P10 长跑看板</h1>
<p class="sub">求职匹配流水线 · 每日无人值守运行 · 最后更新 {esc(stats.get("updated") or "—")}</p>
{kpi}
<h2>运行时间线</h2>{timeline}
<h2>硬失败分类</h2>{fail_chart}
<h2>可疑原因分布</h2>{deg_chart}
<h2>运行明细</h2>{detail}
<h2>失败明细</h2>{fail_detail}
</div></body></html>"""


def render_md(stats: dict) -> str:
    hist = stats.get("history", [])
    fails = stats.get("failures", [])
    ok, deg, bad = stats.get("ok", 0), stats.get("degraded", 0), stats.get("failed", 0)
    total = ok + deg + bad
    rate = (ok / total * 100) if total else 0.0

    lines = [
        "# P10 长跑看板（自动生成）",
        "",
        f"> 最后更新：{stats.get('updated') or '—'}　连续运行：**{stats.get('consecutive_days', 0)} 天**",
        "",
        f"| 正常率 | 次数 | 正常 / 可疑 / 失败 | 累计成本 |",
        f"|---|---|---|---|",
        f"| {rate:.0f}% | {total} | {ok} / {deg} / {bad} | ¥{stats.get('total_cost', 0):.4f} |",
        "",
        "## 运行明细",
        "",
    ]
    if hist:
        lines += ["| # | 日期 | 基线 JD | 变体 | 判定 | 分数 | 硬性 | 耗时 | 成本 | 备注 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for i, h in enumerate(reversed(hist), 1):
            lines.append(
                f"| {len(hist) - i + 1} | {h.get('date','')} | {h.get('jd_label','')} | "
                f"{h.get('stress_label','')} | {VERDICT_CN.get(h.get('verdict',''), '')} | "
                f"{h.get('score','')} | {h.get('must_count','')} | "
                f"{h.get('ms',0)/1000:.1f}s | ¥{h.get('cost',0):.4f} | {h.get('reason','')} |")
    else:
        lines.append("_还没有运行记录_")

    lines += ["", "## 失败明细", ""]
    if fails:
        lines += ["| 日期 | 分类 | 任务 | 现象 | 下一步 |", "|---|---|---|---|---|"]
        for f in reversed(fails[-20:]):
            lines.append(f"| {f.get('date','')} | {f.get('category_cn', f.get('category',''))} | "
                         f"{f.get('jd','')} / {f.get('stress') or '无'} | "
                         f"{str(f.get('detail','')).replace('|', '/')} | {f.get('fix_hint','')} |")
    else:
        lines.append("_还没有硬失败记录_")

    lines += ["", "详见 [index.html](index.html)。", ""]
    return "\n".join(lines)


def main() -> None:
    if not STATS.exists():
        print("stats.json 还不存在，没有可渲染的东西")
        return
    stats = json.loads(STATS.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(render(stats), encoding="utf-8")
    (OUT_DIR / "README.md").write_text(render_md(stats), encoding="utf-8")
    print(f"看板已生成：dashboard/index.html、dashboard/README.md"
          f"（{stats.get('run_count', 0)} 次运行）")


if __name__ == "__main__":
    main()
