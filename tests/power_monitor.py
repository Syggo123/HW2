"""
電源監控（獨立於 app.py，app 當機時也能留下當機前的數據）

每 0.3 秒記錄：throttled 旗標、5V 輸入電壓（PMIC）、核心電壓、CPU 溫度、CPU 使用率
每行立刻 flush 到 logs/power_<時間>.csv，旗標一變就在終端機大字提醒。

throttled 位元（vcgencmd get_throttled）：
  0x1     現在電壓不足          0x10000  開機後曾經電壓不足
  0x4     現在被降頻            0x40000  開機後曾經被降頻
  0x8     現在溫度過高被限制    0x80000  開機後曾經過熱
  10/14 實測當機前看到 0x50000 = 曾經電壓不足 + 曾經降頻

用法（在 Pi 上另開一個 PuTTY 視窗）：
  python3 tests/power_monitor.py
  python3 tests/power_monitor.py --interval 0.1
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import _CPU, read_core_v, read_cpu_temp, read_ext5v, read_throttled  # noqa: E402
from carapi import out_path  # noqa: E402

FLAGS = {0x1: "現在電壓不足", 0x2: "ARM 頻率上限", 0x4: "現在降頻", 0x8: "現在溫度限制",
         0x10000: "曾經電壓不足", 0x20000: "曾經頻率上限", 0x40000: "曾經降頻", 0x80000: "曾經溫度限制"}


def explain(v):
    return "正常" if v == 0 else "、".join(t for b, t in FLAGS.items() if v & b)


ap = argparse.ArgumentParser()
ap.add_argument("--interval", type=float, default=0.3)
args = ap.parse_args()

path = out_path("power")
cpu = _CPU()
last = None
print(f"記錄到 {path}，Ctrl+C 結束")
with open(path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["timestamp_ms", "time", "throttled", "meaning", "ext5v_v", "core_v", "cpu_temp_c", "cpu_pct"])
    try:
        while True:
            thr = read_throttled()
            row = [int(time.time() * 1000), time.strftime("%H:%M:%S"),
                   None if thr is None else hex(thr), "" if thr is None else explain(thr),
                   read_ext5v(), read_core_v(), read_cpu_temp(), cpu.pct()]
            w.writerow(row)
            f.flush()
            os.fsync(f.fileno())                   # 掉電當機也要確定寫進 SD 卡
            print(f"\r{row[1]}  throttled={row[2]}  5V={row[4]}  core={row[5]}  "
                  f"{row[6]}°C  CPU={row[7]}%   ", end="", flush=True)
            if thr != last and last is not None:
                print(f"\n!!! {row[1]} throttled 變化：{hex(last)} → {hex(thr)}（{explain(thr)}）")
            last = thr
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n結束，CSV：{path}")
