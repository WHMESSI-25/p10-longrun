# -*- coding: utf-8 -*-
"""DeepSeek 调用（沿用 P6 的出网守卫做法）

规矩：
- Key 只从环境变量 LLM_API_KEY 读，不写进任何文件、不打印
- https + 域名白名单 + DNS 必须解析到公网地址 + 禁止重定向（防 SSRF）
- 失败指数退避重试
"""
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request

from resilience.retry import RETRYABLE, classify

URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
ALLOW_HOST = "api.deepseek.com"
TIMEOUT = 60
# 无人值守比手动跑更怕失败，多给一次机会：3 次尝试，退避 2s / 4s
RETRIES = 3


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _guard_url(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname != ALLOW_HOST or (p.port not in (None, 443)):
        raise ValueError(f"URL 未通过白名单校验: {url}")
    for info in socket.getaddrinfo(p.hostname, 443, proto=socket.IPPROTO_TCP):
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError(f"DNS 解析到非公网地址 {info[4][0]}，拒绝请求")


def chat(messages: list[dict], temperature: float = 0.2) -> dict:
    """返回 {"text": str, "usage": {"prompt": int, "completion": int}}"""
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise RuntimeError("环境变量 LLM_API_KEY 未设置")

    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")

    last = None
    for i in range(RETRIES + 1):
        try:
            _guard_url(URL)
            req = urllib.request.Request(
                URL, data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
            with _OPENER.open(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage") or {}
            return {
                "text": data["choices"][0]["message"]["content"],
                "usage": {"prompt": usage.get("prompt_tokens", 0),
                          "completion": usage.get("completion_tokens", 0)},
            }
        except Exception as e:          # 按类型决定值不值得重试
            last = e
            cat = classify(e)
            if cat not in RETRYABLE or i >= RETRIES:
                raise
            time.sleep(2 ** (i + 1))
    raise last


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def chat_json(system: str, user: str, temperature: float = 0.1) -> tuple[dict, dict]:
    """要求模型只输出 JSON；解析失败会抛错，由调用方决定重试或降级"""
    r = chat([{"role": "system", "content": system},
              {"role": "user", "content": user}], temperature)
    text = r["text"].strip()
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1)
    else:                                # 没有代码块就取第一个花括号到最后一个
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    return json.loads(text), r["usage"]
