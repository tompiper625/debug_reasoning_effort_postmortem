#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""effort 端到端对比实验：同一道题，high vs max，验证 Kimi 服务端是否真的按档位区别对待。

原理：抓包只能证明"客户端发了什么"，证明不了"Kimi 收到后内部做了什么"。
本实验直接对比服务端行为：同一 prompt 发两次，仅 output_config.effort 不同，
对比 thinking 长度、usage、耗时、stop_reason。

为什么用流式：high/max 档位 thinking 时间长，非流式请求长时间无字节返回，
会被服务端/中间代理判定为死连接直接掐断（RemoteDisconnected）。SSE 持续有数据帧，不会断。

用法：
    python effort_compare.py                # 用默认难题
    python effort_compare.py "你自己的题目"  # 自定义题目

token 自动从 ~/.claude/settings.json 读取，无需手填。
"""
import json, sys, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.stdout.reconfigure(encoding="utf-8")

SETTINGS = Path.home() / ".claude" / "settings.json"
TOKEN = json.loads(SETTINGS.read_text(encoding="utf-8"))["env"]["ANTHROPIC_AUTH_TOKEN"]
URL = "https://api.moonshot.cn/anthropic/v1/messages"

# 默认题目：快乐数问题的完整证明版——需要持续推理，能拉开 effort 差距
PROMPT = sys.argv[1] if len(sys.argv) > 1 else (
    "设 f(n) 为将正整数 n 的各位数字平方求和得到的数。从任意正整数出发反复迭代 f。"
    "请证明：该序列要么最终到达 1，要么进入循环；若是后者，找出所有可能的循环，"
    "并证明不存在其他循环。"
)

def run_once(effort):
    """单次流式请求，逐帧累积 thinking / text。"""
    body = {
        "model": "kimi-k3",
        "max_tokens": 32000,
        "stream": True,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": PROMPT}],
    }
    req = Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "content-type": "application/json",
        "accept": "text/event-stream",
        "x-api-key": TOKEN,
        "authorization": f"Bearer {TOKEN}",
        "anthropic-version": "2023-06-01",
    })
    t0 = time.time()
    resp = urlopen(req, timeout=600)  # timeout 作用于每次读操作，流式下单帧间隔远小于此
    thinking_parts, text_parts = [], []
    usage, stop_reason = {}, None
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue  # 跳过 event: 行和 keep-alive 注释行
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        evt = json.loads(payload)
        etype = evt.get("type")
        if etype == "message_start":
            usage.update(evt.get("message", {}).get("usage", {}))
        elif etype == "content_block_delta":
            d = evt.get("delta", {})
            if d.get("type") == "thinking_delta":
                thinking_parts.append(d.get("thinking", ""))
            elif d.get("type") == "text_delta":
                text_parts.append(d.get("text", ""))
        elif etype == "message_delta":
            usage.update(evt.get("usage", {}))
            stop_reason = evt.get("delta", {}).get("stop_reason") or stop_reason
    dt = time.time() - t0
    return {
        "effort": effort,
        "http": resp.status,
        "seconds": round(dt, 1),
        "stop_reason": stop_reason,
        "usage": usage,
        "thinking": "".join(thinking_parts),
        "answer": "".join(text_parts),
    }

def run(effort, retries=5):
    """带重试和错误诊断的包装：HTTP 错误打印响应体，断连自动重试。"""
    for attempt in range(1, retries + 1):
        try:
            r = run_once(effort)
            break
        except HTTPError as e:
            # 服务器返回了错误码——响应体里通常有具体原因（参数错误/限流等）
            return {"effort": effort, "http": e.code,
                    "error": e.read().decode("utf-8", "replace")[:2000]}
        except OSError as e:
            # 覆盖 RemoteDisconnected / URLError / TimeoutError，以及 Windows 上
            # socket 断开被误映射成的 FileNotFoundError(Errno 2)——全都是连接中断
            print(f"[{effort}] 第 {attempt} 次连接中断: {e!r}", flush=True)
            if attempt == retries:
                return {"effort": effort, "error": f"重试 {retries} 次仍失败: {e}"}
            time.sleep(5 * attempt)
    # 完整内容落盘，方便人工对比回答质量
    out = Path(__file__).with_name(f"effort_{effort}_response.json")
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in r.items() if k not in ("thinking", "answer")}
    summary["thinking_chars"] = len(r["thinking"])
    summary["answer_chars"] = len(r["answer"])
    summary["saved_to"] = str(out)
    print(f"[{effort}] 完成: {summary['seconds']}s, "
          f"thinking {summary['thinking_chars']} 字, answer {summary['answer_chars']} 字", flush=True)
    return summary

if __name__ == "__main__":
    print(f"题目: {PROMPT[:50]}...\n", flush=True)
    results = []
    results_file = Path(__file__).with_name("effort_compare_results.json")
    for e in ("high", "max"):
        results.append(run(e))
        # 每档跑完立即落盘——后面档位失败也不丢已有结果
        results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    h, m = results
    if h.get("thinking_chars") and m.get("thinking_chars"):
        ratio = m["thinking_chars"] / h["thinking_chars"]
        print(f"\nthinking 长度比值 (max/high): {ratio:.2f}")
        print(">> 明显大于 1：Kimi 确实按档位区分；接近 1：两档内部可能没区别（建议换题或重复几次排除噪声）")
