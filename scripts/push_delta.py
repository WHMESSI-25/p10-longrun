# -*- coding: utf-8 -*-
"""增量推送：只推「代码与文档」，绝不碰云端产出的 runs/stats/dashboard

为什么不能直接 git push、更不能用整库还原脚本：
P10 的长跑机器人每天往仓库提交一次留痕。本地一推就与它分叉；
而整库还原（force PATCH ref）会把过去 N 天的运行记录直接抹掉——
那正是本项目唯一的产出。

所以这里只做增量：
1. 取远端 HEAD 的 tree 当 base_tree
2. 只上传本地有变化的代码文件（GitHub tree API 支持 base_tree，未列出的自动继承）
3. 新 commit 的 parent 是远端 HEAD，历史不断，也不允许强推

用法：
    GITHUB_TOKEN=xxx python scripts/push_delta.py WHMESSI-25/p10-longrun main "提交说明"

不带说明时用「同步代码 <本地时间>」。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"

# 这些目录归云端机器人所有，本地只读不写
CLOUD_OWNED = ("runs/", "stats/", "dashboard/")
# 本地垃圾
LOCAL_JUNK = (".venv/", "__pycache__/", ".deploy-tmp/")


def api(method, path, payload=None, token=""):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "p10-push-delta"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"API {method} {path} 失败（{e.code}）：{detail}")
    return json.loads(body) if body else {}


def run(*args) -> bytes:
    return subprocess.run(args, capture_output=True, check=True).stdout


def pushable(path: str) -> bool:
    if any(path.startswith(p) for p in CLOUD_OWNED + LOCAL_JUNK):
        return False
    if path.endswith(".pyc"):
        return False
    return True


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("用法：push_delta.py <owner/repo> <branch> [提交说明]")
    repo, branch = sys.argv[1], sys.argv[2]
    message = sys.argv[3] if len(sys.argv) > 3 else f"同步代码 {time.strftime('%Y-%m-%d %H:%M')}"
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("环境变量 GITHUB_TOKEN 未设置")

    head_sha = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", None, token)["object"]["sha"]
    head = api("GET", f"/repos/{repo}/git/commits/{head_sha}", None, token)
    base_tree = head["tree"]["sha"]
    print(f"远端 HEAD {head_sha[:7]}，基准 tree {base_tree[:7]}")

    remote = {}
    tree = api("GET", f"/repos/{repo}/git/trees/{base_tree}?recursive=1", None, token)
    for item in tree.get("tree", []):
        if item["type"] == "blob":
            remote[item["path"]] = item["sha"]

    files = run("git", "ls-files", "--cached", "--others", "--exclude-standard").decode("utf-8").split()
    changed, entries = [], []
    for path in files:
        if not pushable(path):
            continue
        local_sha = run("git", "hash-object", path).decode().strip()
        if remote.get(path) == local_sha:
            continue
        content = run("git", "cat-file", "blob", local_sha)  # 与工作区一致，已按 git 规则处理编码
        blob = api("POST", f"/repos/{repo}/git/blobs",
                   {"content": base64.b64encode(content).decode(), "encoding": "base64"}, token)
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        changed.append(path)

    if not entries:
        print("本地代码与远端一致，无需推送")
        return

    new_tree = api("POST", f"/repos/{repo}/git/trees",
                   {"base_tree": base_tree, "tree": entries}, token)["sha"]
    commit = api("POST", f"/repos/{repo}/git/commits",
                 {"message": message, "tree": new_tree, "parents": [head_sha]}, token)["sha"]
    api("PATCH", f"/repos/{repo}/git/refs/heads/{branch}",
        {"sha": commit, "force": False}, token)   # 不强推：落后就报错，而不是覆盖
    print(f"已推送 {len(changed)} 个文件 → {commit[:7]}")
    for p in changed:
        print(f"  · {p}")


if __name__ == "__main__":
    main()
