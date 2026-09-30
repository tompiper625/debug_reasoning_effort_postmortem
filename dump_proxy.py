#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取 Claude Code 发出的 Anthropic API 请求体，用于验证 effort/thinking 字段。"""
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen

UPSTREAM = "https://api.moonshot.cn"
LOG = sys.argv[2] if len(sys.argv) > 2 else "request_dump.jsonl"

class Handler(BaseHTTPRequestHandler):
    def _proxy(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"_raw": body.decode("utf-8", "replace")}
        record = {
            "time": time.strftime("%H:%M:%S"),
            "path": self.path,
            "effort_related": {
                k: parsed.get(k) for k in
                ("thinking", "output_config", "reasoning_effort", "reasoning", "model")
                if k in parsed
            },
            "all_top_level_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
        }
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        req = Request(UPSTREAM + self.path, data=body or None, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "connection"):
                req.add_header(k, v)
        try:
            resp = urlopen(req, timeout=120)
            data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            msg = str(e).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    do_POST = do_GET = do_PUT = _proxy
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8399
    print(f"dump proxy on http://127.0.0.1:{port} -> {UPSTREAM}, log: {LOG}")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
