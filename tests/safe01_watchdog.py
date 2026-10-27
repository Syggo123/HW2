"""
SAFE-01：網頁控制心跳中斷後，馬達會不會在逾時內歸零（W7 講義 p.21）

模擬「網路斷線 / 手機鎖屏 / 分頁當掉」：先像網頁一樣每 150ms 送一次前進，
然後突然不送了（也不送 stop），看後端看門狗多久把車停下來。

量兩種時間：
  server_gap_ms   後端時鐘：最後一次收到指令 → 看門狗停車（最準，不受網路影響）
  client_gap_ms   電腦時鐘：最後一次送出指令 → 輪詢 /api/status 看到 stop
前置條件：輪子架空、app.py 在跑（看門狗設定 WATCHDOG_SEC = 0.6）
建議同時用手機錄影輪子停下來的畫面，當作證據。

用法：
  python tests/safe01_watchdog.py --host 192.168.0.50            # 10 次
  python tests/safe01_watchdog.py --host 192.168.0.50 -n 20 --limit 800
"""
import argparse
import csv
import time

from carapi import Car, describe, out_path

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True)
ap.add_argument("-n", type=int, default=10, help="測幾次")
ap.add_argument("--drive", type=float, default=1.5, help="每次先正常送心跳幾秒")
ap.add_argument("--beat", type=float, default=0.15, help="心跳間隔（跟網頁一樣 150ms）")
ap.add_argument("--limit", type=float, default=None, help="安全門檻 ms，預設 = 看門狗秒數 + 150ms")
ap.add_argument("--speed", type=int, default=25)
args = ap.parse_args()

car = Car(args.host)
code, st, _ = car.status()
if code != 200:
    raise SystemExit(f"連不上 {car.base}：{st}")
wd_ms = st["motor"].get("watchdog_sec", 0.6) * 1000
limit = args.limit or wd_ms + 150
car.speed(args.speed)
print(f"看門狗設定 {wd_ms:.0f}ms，安全門檻 {limit:.0f}ms，共 {args.n} 次（輪子要架空！）")

rows = []
for i in range(args.n):
    car.move("stop")
    time.sleep(0.5)
    t_end = time.time() + args.drive
    while time.time() < t_end:                     # 1. 正常心跳
        car.move("forward")
        last_send = time.time()
        time.sleep(args.beat)
    car.move("forward")                            # 2. 最後一次心跳，之後「斷線」
    last_send = time.time()

    seen_stop, polls = None, 0                     # 3. 只輪詢狀態（不會餵看門狗）
    while time.time() - last_send < 3.0:
        code, st, _ = car.status()
        polls += 1
        if code == 200 and st["motor"]["action"] == "stop":
            seen_stop = time.time()
            break
        time.sleep(0.02)

    m = st["motor"] if code == 200 else {}
    server_gap = (m.get("last_auto_stop_ms", 0) - m.get("last_cmd_ms", 0)) if seen_stop else None
    client_gap = (seen_stop - last_send) * 1000 if seen_stop else None
    ok = server_gap is not None and server_gap <= limit
    rows.append({"trial": i + 1, "last_send_ms": int(last_send * 1000),
                 "server_gap_ms": server_gap, "client_gap_ms": None if client_gap is None else round(client_gap),
                 "polls": polls, "pass": int(ok)})
    print(f"  #{i + 1:2d}  後端 {server_gap} ms   電腦端 {rows[-1]['client_gap_ms']} ms   "
          f"{'PASS' if ok else 'FAIL（3 秒內沒停！）' if server_gap is None else 'FAIL'}")
    time.sleep(0.8)

car.move("stop")
path = out_path("safe01")
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

srv = [r["server_gap_ms"] for r in rows if r["server_gap_ms"] is not None]
cli = [r["client_gap_ms"] for r in rows if r["client_gap_ms"] is not None]
n_pass = sum(r["pass"] for r in rows)
print(f"\n===== SAFE-01 結果 =====")
print(f"後端停車時間 ms   {describe(srv)}")
print(f"電腦端看到停車 ms {describe(cli)}")
print(f"判定：{n_pass}/{len(rows)} 次在 {limit:.0f}ms 內停車 → {'PASS' if n_pass == len(rows) else 'FAIL'}")
print(f"CSV：{path}")
