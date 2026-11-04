# -*- coding: utf-8 -*-
"""把 LLM_API_KEY 写进 GitHub 仓库 Secrets（用 libsodium 公钥加密）

GitHub 不允许明文传 Secret：必须用仓库公钥做 sealed box 加密再 PUT。
这个脚本只做这一件事，Key 从环境变量读，不打印、不落盘。

用法：
    GITHUB_TOKEN=xxx LLM_API_KEY=sk-xxx python scripts/set_secret.py WHMESSI-25/p10-longrun

依赖：pip install pynacl
"""
import base64
import json
import os
import sys
import urllib.request

from nacl import encoding, public

API = "https://api.github.com"
SECRET_NAME = "LLM_API_KEY"


def api(method, path, payload=None, token="", ua="p10-set-secret"):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：set_secret.py <owner/repo>")
    repo = sys.argv[1]
    token = os.environ.get("GITHUB_TOKEN", "")
    value = os.environ.get("LLM_API_KEY", "")
    if not token or not value:
        sys.exit("需要同时设置 GITHUB_TOKEN 与 LLM_API_KEY 环境变量")

    key = api("GET", f"/repos/{repo}/actions/secrets/public-key", None, token)
    pub = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pub).encrypt(value.encode("utf-8"))
    api("PUT", f"/repos/{repo}/actions/secrets/{SECRET_NAME}",
        {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key["key_id"]},
        token)
    print(f"已写入 {repo} 的 Secrets：{SECRET_NAME}（{len(value)} 字符）")


if __name__ == "__main__":
    main()
