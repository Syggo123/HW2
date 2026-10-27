"""
測試日誌（W7：每個影格 / 每次控制都要留 CSV 證據）

每次啟動 app.py 會產生一組 run_id（例如 R20261021_141530），在 logs/ 底下寫四個檔：
  <run>_config.json   這次的參數、套件版本、Git commit（講義 p.07「可重現測試環境」）
  <run>_frames.csv    每個車道影格：lane_mode、CTE、FPS、frame_age、當下馬達動作
  <run>_api.csv       每次網頁指令：動作、後端處理時間、I2C 錯誤
  <run>_system.csv    每秒一次：CPU 使用率、溫度、throttled（電壓不足旗標）、5V 輸入電壓
  <run>_events.csv    看門狗停車、I2C 錯誤、錄影開始 / 結束等事件

每寫一行就 flush：Pi5 掉電當機時，當機前的資料還留得住（10/14 電壓問題就是靠這個抓到）。
"""
import csv
import json
import os
import platform
import subprocess
import sys
import threading
import time

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

FRAME_FIELDS = ["timestamp_ms", "run_id", "frame_id", "lane_mode", "cte_px", "delta_x_px",
                "lane_fps", "cam_fps", "yolo_fps", "frame_age_ms", "action", "speed", "recording"]
API_FIELDS = ["timestamp_ms", "run_id", "endpoint", "action", "ok", "api_latency_ms", "i2c_error", "error"]
SYSTEM_FIELDS = ["timestamp_ms", "run_id", "cpu_pct", "cpu_temp_c", "throttled", "under_voltage_now",
                 "ext5v_v", "core_v", "action"]
EVENT_FIELDS = ["timestamp_ms", "run_id", "event", "detail"]


def now_ms():
    return int(time.time() * 1000)


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout.strip()
    except Exception:
        return ""


def read_throttled():
    """vcgencmd get_throttled → int；bit0 = 現在電壓不足，bit16 = 開機後曾經電壓不足。"""
    out = _run(["vcgencmd", "get_throttled"])            # throttled=0x50000
    try:
        return int(out.split("=")[1], 16)
    except (IndexError, ValueError):
        return None


def read_ext5v():
    """Pi5 PMIC 量到的 5V 輸入電壓（真正的供電），measure_volts core 只是 CPU 核心電壓。"""
    for line in _run(["vcgencmd", "pmic_read_adc", "EXT5V_V"]).splitlines():
        if "=" in line:
            try:
                return float(line.split("=")[1].rstrip("V"))
            except ValueError:
                pass
    return None


def read_core_v():
    out = _run(["vcgencmd", "measure_volts", "core"])     # volt=0.8778V
    try:
        return float(out.split("=")[1].rstrip("V"))
    except (IndexError, ValueError):
        return None


def read_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000, 1)
    except OSError:
        return None


class _CPU:
    """從 /proc/stat 算兩次呼叫之間的 CPU 使用率。"""
    def __init__(self):
        self.prev = self._read()

    @staticmethod
    def _read():
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            return sum(v), v[3] + v[4]                    # total, idle + iowait
        except OSError:
            return None

    def pct(self):
        cur = self._read()
        if not cur or not self.prev:
            return None
        dt, di = cur[0] - self.prev[0], cur[1] - self.prev[1]
        self.prev = cur
        return round(100 * (1 - di / dt), 1) if dt > 0 else None


def git_commit():
    here = os.path.dirname(os.path.abspath(__file__))
    return _run(["git", "-C", here, "rev-parse", "--short", "HEAD"]) or "no-git"


def versions():
    v = {"python": sys.version.split()[0], "platform": platform.platform()}
    for name in ("cv2", "flask", "numpy", "torch", "ultralytics", "picamera2"):
        try:
            mod = __import__(name)
            v[name] = getattr(mod, "__version__", "?")
        except Exception:
            v[name] = None
    try:
        with open("/proc/device-tree/model") as f:
            v["board"] = f.read().strip("\x00\n")
    except OSError:
        pass
    return v


class _CSV:
    def __init__(self, path, fields):
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.DictWriter(self.f, fieldnames=fields, extrasaction="ignore")
        self.w.writeheader()
        self.lock = threading.Lock()

    def write(self, row):
        with self.lock:
            self.w.writerow(row)
            self.f.flush()

    def close(self):
        with self.lock:
            self.f.close()


class RunLogger:
    def __init__(self, config=None, enabled=True, tag=""):
        self.enabled = enabled
        self.run_id = "R" + time.strftime("%Y%m%d_%H%M%S") + (f"_{tag}" if tag else "")
        self.status_fn = None              # app 設定：回傳目前馬達動作，給 system log 用
        if not enabled:
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        base = os.path.join(LOG_DIR, self.run_id)
        with open(base + "_config.json", "w", encoding="utf-8") as f:
            json.dump({"run_id": self.run_id, "start": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "git_commit": git_commit(), "versions": versions(), "config": config or {}},
                      f, ensure_ascii=False, indent=2)
        self.frames = _CSV(base + "_frames.csv", FRAME_FIELDS)
        self.api = _CSV(base + "_api.csv", API_FIELDS)
        self.system = _CSV(base + "_system.csv", SYSTEM_FIELDS)
        self.events = _CSV(base + "_events.csv", EVENT_FIELDS)
        self._cpu = _CPU()
        threading.Thread(target=self._system_loop, daemon=True).start()
        print(f"[log] 這次的紀錄：logs/{self.run_id}_*.csv")

    def frame(self, **row):
        if self.enabled:
            self.frames.write({"timestamp_ms": now_ms(), "run_id": self.run_id, **row})

    def api_call(self, **row):
        if self.enabled:
            self.api.write({"timestamp_ms": now_ms(), "run_id": self.run_id, **row})

    def event(self, event, detail=""):
        if self.enabled:
            self.events.write({"timestamp_ms": now_ms(), "run_id": self.run_id,
                               "event": event, "detail": detail})

    def _system_loop(self):
        last_flag = None
        while True:
            thr = read_throttled()
            row = {"timestamp_ms": now_ms(), "run_id": self.run_id, "cpu_pct": self._cpu.pct(),
                   "cpu_temp_c": read_cpu_temp(), "throttled": None if thr is None else hex(thr),
                   "under_voltage_now": None if thr is None else int(bool(thr & 0x1)),
                   "ext5v_v": read_ext5v(), "core_v": read_core_v(),
                   "action": self.status_fn() if self.status_fn else ""}
            self.system.write(row)
            if thr is not None and thr != last_flag:
                if last_flag is not None:
                    self.event("throttled_change", f"{hex(last_flag)} -> {hex(thr)}")
                last_flag = thr
            time.sleep(1.0)
