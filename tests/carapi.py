"""測試腳本共用：用標準函式庫呼叫小車的 Flask API（電腦上不用裝任何套件）。"""
import json
import os
import statistics
import time
import urllib.error
import urllib.request

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


class Car:
    def __init__(self, host, port=5000, timeout=2.0):
        if not host.startswith("http"):
            host = f"http://{host}" + ("" if ":" in host else f":{port}")
        self.base = host.rstrip("/")
        self.timeout = timeout

    def _req(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                code, payload = r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            code, payload = e.code, _safe_json(e.read())
        except Exception as e:                       # 逾時、連不上、Pi 當機
            code, payload = 0, {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return code, payload, (time.perf_counter() - t0) * 1000

    def move(self, action):
        return self._req("/api/move", {"action": action})

    def status(self):
        return self._req("/api/status")

    def speed(self, v):
        return self._req("/api/speed", {"speed": v})


def _safe_json(b):
    try:
        return json.loads(b)
    except Exception:
        return {"ok": False, "error": b[:200].decode(errors="ignore")}


def pct(values, p):
    """第 p 百分位數（線性內插，跟 numpy.percentile 預設一樣）。"""
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def describe(values):
    if not values:
        return "無資料"
    return (f"n={len(values)}  平均={statistics.mean(values):.1f}  p50={pct(values, 50):.1f}  "
            f"p95={pct(values, 95):.1f}  最大={max(values):.1f}")


def out_path(name):
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
