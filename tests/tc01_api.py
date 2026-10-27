"""
TC-01：網頁指令能不能可靠地驅動車輪（W7 講義 p.06、p.10）

前置條件：輪子架空（車子墊高）、i2cdetect -y 1 看得到 40、app.py 已經在跑
操作：重複 N 次「前進 → 停住 hold 秒 → 停止」
記錄：每個請求的 HTTP 結果、來回延遲、後端處理時間、I2C 錯誤數
輸出：logs/tc01_<時間>.csv + 終端機摘要

用法（在電腦或 Pi 上都可以）：
  python tests/tc01_api.py --host 192.168.0.50                 # 單一 client，100 次
  python tests/tc01_api.py --host 192.168.0.50 --clients 3     # 併發壓力：3 個 client 同時送
  python tests/tc01_api.py --host 192.168.0.50 -n 30 --hold 1  # 講義 p.10「前進 1s → 停止 ×30」
"""
import argparse
import csv
import threading
import time

from carapi import Car, describe, out_path, pct

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True, help="Pi 的 IP，或 http://ip:port")
ap.add_argument("-n", type=int, default=100, help="每個 client 跑幾輪")
ap.add_argument("--clients", type=int, default=1, help="同時幾個 client（併發壓力測試用 2~5）")
ap.add_argument("--hold", type=float, default=0.3, help="前進維持幾秒（< 0.6 才不會被看門狗停掉）")
ap.add_argument("--action", default="forward", choices=["forward", "backward", "left", "right"])
ap.add_argument("--speed", type=int, help="測試前先設速度（建議低速，例如 25）")
ap.add_argument("--max-fail", type=float, default=0.0, help="允許的失敗率 %%（通過門檻依老師公布）")
ap.add_argument("--max-p95", type=float, default=200.0, help="允許的 p95 延遲 ms")
args = ap.parse_args()

car = Car(args.host)
code, st, _ = car.status()
if code != 200:
    raise SystemExit(f"連不上 {car.base}：{st}")
err0 = st["motor"].get("i2c_errors", 0)
print(f"連線 OK，run_id={st.get('run_id')}，hardware={st['motor']['hardware']}，開始前 I2C 錯誤={err0}")
if args.speed:
    car.speed(args.speed)

rows, lock = [], threading.Lock()
t_start = time.time()


def worker(cid):
    for i in range(args.n):
        for action in (args.action, "stop"):
            t_send = time.time()
            code, p, ms = car.move(action)
            ok = code == 200 and p.get("ok", False)
            with lock:
                rows.append({"client": cid, "iter": i, "action": action,
                             "t_send_ms": int(t_send * 1000), "latency_ms": round(ms, 2),
                             "http": code, "ok": int(ok), "server_action": p.get("action", ""),
                             "server_ms": p.get("server_ms", ""), "error": p.get("error", "")})
            if action != "stop":
                time.sleep(args.hold)
        time.sleep(0.05)


threads = [threading.Thread(target=worker, args=(c,)) for c in range(args.clients)]
for t in threads:
    t.start()
for t in threads:
    t.join()
car.move("stop")
dur = time.time() - t_start

code, st, _ = car.status()
err1 = st["motor"].get("i2c_errors", 0) if code == 200 else None
path = out_path(f"tc01_c{args.clients}")
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

lat = [r["latency_ms"] for r in rows if r["ok"]]
fails = [r for r in rows if not r["ok"]]
# 單一 client 時，後端回報的動作必須等於剛送的動作；多 client 時本來就會被別人蓋掉，不算錯
mismatch = sum(1 for r in rows if r["ok"] and r["server_action"] != r["action"]) if args.clients == 1 else 0
fail_rate = 100 * len(fails) / len(rows)
p95 = pct(lat, 95) or 0
i2c = None if err1 is None else err1 - err0

print(f"\n===== TC-01 結果（{args.clients} client × {args.n} 輪，{len(rows)} 個請求，{dur:.1f} 秒）=====")
print(f"失敗：{len(fails)}（{fail_rate:.2f}%）   I2C 錯誤增加：{i2c}   動作不一致：{mismatch}")
print(f"來回延遲 ms   {describe(lat)}")
for act in (args.action, "stop"):
    v = [float(r["server_ms"]) for r in rows if r["ok"] and r["action"] == act and r["server_ms"] != ""]
    print(f"後端處理 ms [{act:>8}]  {describe(v)}")
print("  ↑ 前進從靜止起步有緩啟動（1/3 → 1/2 → 全速，各 50ms），所以約 100ms；stop 應接近 0")
print(f"測試後車子狀態：{st['motor']['action'] if code == 200 else '連不上（Pi 可能當機了）'}")
for r in fails[:5]:
    print("  失敗範例：", r["action"], r["http"], r["error"])
passed = fail_rate <= args.max_fail and p95 <= args.max_p95 and not i2c and mismatch == 0 \
    and code == 200 and st["motor"]["action"] == "stop"
print(f"判定：{'PASS' if passed else 'FAIL'}（門檻：失敗率 ≤ {args.max_fail}%、p95 ≤ {args.max_p95}ms、"
      f"I2C 錯誤 0、結束時停車）")
print(f"CSV：{path}")
