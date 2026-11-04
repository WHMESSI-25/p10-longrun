# P10 长期运行：让一条 Agent 流水线无人值守跑一个月

这是「Agent 从 0 到精通」阶段 4（可靠）的第三个项目。

前两个项目解决的是「怎么做得对」（P7 多 Agent）、「怎么量得准」（P8 工程化）、「怎么防得住」（P9 注入攻防）。
这个项目解决最后一件、也是最难装出来的事：**它能不能自己活一个月。**

> 代码本身不是产出。**失败数据**才是产出。
> 所以这里的重点不是「怎么不挂」，而是「挂了以后有没有留下可分析的痕迹」。

被跑的是 P7 那条求职匹配流水线（解析 → 匹配 → 建议，LangGraph 编排），代码沿用 P8 的工程化版本，锁死 `langgraph==1.2.19`。

---

## 每天发生什么

```
UTC 00:00（北京 08:00）
        │
        ├─ 01 选题    第 N 次运行 → JD 池第 N%6 条 + 变体第 N%10 类
        │             6 与 10 的最小公倍数是 30，24 天内组合不重复
        ├─ 02 注变    超长 / 无换行 / 零宽字符 / 末尾埋指令 / 权威施压 / …
        ├─ 03 开跑    先查成本熔断，再跑三节点，两个人工确认点自动放行
        ├─ 04 判定    ok / degraded / failed 三档，判据只看代码不靠印象
        ├─ 05 留痕    runs/<run_id>.json 存完整 state，stats.json 记账
        └─ 06 收尾    重算看板 → 提交 → 失败就开 Issue
```

## 三档判定，为什么不是「成功 / 失败」

只分两档会把「跑完了但结果可疑」全算成成功，一个月后看不出任何问题。

| 判定 | 含义 | 典型来源 |
|---|---|---|
| `ok` | 跑通且结果可信 | 硬性条件非空、无内部告警 |
| `degraded` | 跑通了，但结果不该直接信 | `semantic_invalid`（一条硬性条件都没抽到）、硬性条件为空、内部告警 |
| `failed` | 进程级失败 | 网络、限流、鉴权、上游 5xx、模型输出不可解析、输入数据问题、成本熔断 |

`type_overridden` 不算 degraded——那是 P9 加的正确性纠正机制（含「优先/加分」的行由代码强制判 plus），它生效说明防护在工作。

## 失败分类口径

分类函数只有一处（`resilience/retry.py`），看板、统计表、Issue 标签全读它，避免「日志说网络错、看板算成未归类」。

| 分类 | 会重试吗 | 第一反应该查什么 |
|---|---|---|
| `network` 网络中断/超时 | 会（2s、4s） | DNS 与出网；连续失败说明不是抖动 |
| `rate_limit` 限流 429 | 会 | 退避已生效，连续超限要降频 |
| `server` 上游 5xx | 会 | 等上游恢复，重试已自动做过 |
| `auth` 鉴权失败 | **不会** | Key 失效或额度不足，重试纯属浪费配额 |
| `schema` 模型输出不可解析 | 不会 | 看原始回复，判断是提示词还是上下文超长 |
| `data` 输入数据问题 | 不会 | 查 `data/` 与 `tasks/pool.json` |
| `budget` 成本熔断 | 不会 | 当日成本触顶，先确认是不是异常烧钱 |
| `unknown` 未归类 | 不会 | 看 traceback 后补规则 |

「不会重试」是刻意的：401 重试三次只会多花 6 秒和 3 次配额，还会把一条明确的配置错误伪装成「偶发故障」。

## 看板

- `dashboard/index.html` —— 图形版（连续天数 / 正常率 / 时间线 / 失败分类）
- `dashboard/README.md` —— 表格版，GitHub 上直接看

两份都由 `scripts/gen_dashboard.py` 从同一份 `stats/stats.json` 生成，不依赖任何 CDN，能离线打开。

> 想看网页版：仓库 Settings → Pages → Source 选 `main` / `docs` 之外的 `/dashboard` 目录（或用 Actions 部署）。没开也不影响，README.md 版本够看。

## 本地跑

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt

python runner.py --dry-run      # 只打印今天跑什么，不花钱
python runner.py                # 真跑一次（约 ¥0.005）
python runner.py --force n3     # 指定基线 JD
python runner.py --no-stress    # 不加变体，只跑干净输入
python scripts/gen_dashboard.py # 重新生成看板
```

Key 只从环境变量 `LLM_API_KEY` 读，不写进任何文件。CI 里来自仓库 Secrets。

## 目录

```
runner.py              长跑入口：选题 → 注变 → 跑 → 判定 → 记账
pipeline.py            跑一次的封装（自动放行两个人工确认点）
graph.py / state.py    编排图与状态定义（沿用 P7/P8）
nodes/                 解析员、匹配员、建议员
llm.py                 DeepSeek 调用（出网守卫 + 按类型重试）
resilience/
  retry.py             失败分类 + 指数退避
  budget.py            单日成本熔断
tasks/
  pool.json            任务池（6 条基线 JD × 10 类变体）
  variants.py          十类刁难变体，无随机数，可复现
runs/                  每次运行的完整 state（可复盘到信息在哪一步变形）
stats/stats.json       账本：逐次记录 + 失败明细 + 当日成本
dashboard/             自动生成的看板（勿手改）
```

## 24 天计划

| 阶段 | 做什么 | 谁 |
|---|---|---|
| Day1-2 | 建仓库、接 cron、跑通一轮 | agent |
| Day3-25 | 每天自动跑；按失败数据补容错（重试 → 断点续跑 → 熔断 → 告警） | agent 跑，人看日报 |
| Day26-28 | 汇总失败模式统计表 + 复盘 | agent 写，人验收 |

验收口径：连续运行 ≥24 天 · 有失败模式统计表 · ≥2 个自动容错机制上线。

## 两个已知取舍

**只有 6 条基线 JD，不是 30 条。** 与其编 24 条假 JD 凑数，不如用 6 条真实风格的样例轮转 4 轮、每天换一种刁难方式。这样同一条 JD 在不同变体下的差异反而更容易定位成因。后续若发现某类失败总落在特定岗位类型上，再定向补样例。

**不追求 100% 成功率。** 这个项目的成功标准是「有多少种失败被看见并归了类」，不是「一次都没挂」。一个从不失败的长跑，通常意味着测试压力不够。
