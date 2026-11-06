#!/usr/bin/env python3
# Concat MCP bridge: HTTP wrapper around `concat --mcp headless` (stdlib only)
import json, os, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json")))
HOME = CFG.get("home", "/opt/concat-home")
CONCAT = CFG.get("concat", "/opt/concat/concat")
LIB = CFG.get("lib", "/opt/concat/lib")
PORT = int(CFG.get("port", 18190))
MEDIA_MAP = CFG.get("media_map", {})
os.makedirs(os.path.join(HOME, "exports"), exist_ok=True)

class MCP:
    def __init__(self):
        self.lock = threading.RLock()
        self.i = 0
        self._spawn()
    def _spawn(self):
        env = dict(os.environ, LD_LIBRARY_PATH=LIB, HOME=HOME)
        self.p = subprocess.Popen([CONCAT, "--mcp", "headless"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=HOME, text=True, bufsize=1)
        self.i = 0
        m = {"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"bridge","version":"1"}}}
        self.p.stdin.write(json.dumps(m)+"\n"); self.p.stdin.flush()
        deadline = time.time()+30
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line: raise RuntimeError("concat died on start")
            line = line.strip()
            if line.startswith("{"):
                d = json.loads(line)
                if d.get("id") == 0: break
        self.p.stdin.write(json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+"\n"); self.p.stdin.flush()
    def _raw(self, name, args, timeout=900):
        self.i += 1; mid = self.i
        self.p.stdin.write(json.dumps({"jsonrpc":"2.0","id":mid,"method":"tools/call","params":{"name":name,"arguments":args}})+"\n"); self.p.stdin.flush()
        deadline = time.time()+timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line: raise RuntimeError("concat process died")
            line = line.strip()
            if not line.startswith("{"): continue
            d = json.loads(line)
            if d.get("id") == mid:
                res = d.get("result") or {}
                txt = "".join(c.get("text","") for c in res.get("content",[]) if c.get("type")=="text")
                images = [{"mime": c.get("mimeType","image/png"), "data": c.get("data","")}
                          for c in res.get("content",[]) if c.get("type")=="image"]
                return {"isError": bool(res.get("isError")), "text": txt, "images": images}
        raise RuntimeError("tool timeout " + name)
    def call(self, name, args, timeout=900):
        with self.lock:
            if self.p.poll() is not None: self._spawn()
            try:
                r = self._raw(name, args, timeout)
            except RuntimeError:
                self._spawn()
                r = self._raw(name, args, timeout)
            # 项目未打开 → 自动 open 后重试一次
            if r["isError"] and "not open" in r.get("text","") and isinstance(args.get("path"), str):
                try: self._raw("project_open", {"path": args["path"]}, 60)
                except Exception: pass
                r = self._raw(name, args, timeout)
            return r
    def list_tools(self):
        with self.lock:
            if self.p.poll() is not None: self._spawn()
            self.i += 1; mid = self.i
            self.p.stdin.write(json.dumps({"jsonrpc":"2.0","id":mid,"method":"tools/list"})+"\n"); self.p.stdin.flush()
            while True:
                line = self.p.stdout.readline()
                if not line: raise RuntimeError("eof")
                line = line.strip()
                if not line.startswith("{"): continue
                d = json.loads(line)
                if d.get("id") == mid: return d.get("result", {}).get("tools", [])

mcp = MCP()

def translate(path):
    for k, v in MEDIA_MAP.items():
        if path.startswith(k): return v + path[len(k):]
    return path

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "concat": "0.2.8-headless"})
        if self.path == "/tools":
            try: return self._json(200, {"tools": mcp.list_tools()})
            except Exception as e: return self._json(500, {"error": str(e)})
        if self.path.startswith("/export/"):
            name = os.path.basename(self.path[len("/export/"):])
            fp = os.path.join(HOME, "exports", name)
            if not os.path.isfile(fp): return self._json(404, {"error":"not found"})
            self.send_response(200); self.send_header("Content-Type","video/mp4")
            self.send_header("Content-Length", str(os.path.getsize(fp))); self.end_headers()
            with open(fp,"rb") as f:
                while True:
                    chunk = f.read(1<<20)
                    if not chunk: break
                    self.wfile.write(chunk)
            return
        self._json(404, {"error":"not found"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except Exception: return self._json(400, {"error":"bad json"})
        if self.path == "/tools/call":
            name = body.get("tool"); args = body.get("args") or {}
            for key in ("path","file"):
                if isinstance(args.get(key), str): args[key] = translate(args[key])
            if isinstance(args.get("output"), str) and args["output"]:
                args["output"] = os.path.join(HOME, "exports", os.path.basename(args["output"]))
            try: return self._json(200, mcp.call(name, args, timeout=int(body.get("timeout") or 900)))
            except Exception as e: return self._json(500, {"error": str(e)})
        self._json(404, {"error":"not found"})

ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
