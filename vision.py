"""
影像處理：相機擷取、YOLO 物件辨識、車道偵測、錄影

三條執行緒各做各的，互不卡住：
  camera  一直抓最新畫面
  yolo    每 YOLO_EVERY 幀辨識一次（imgsz=320），結果存起來給網頁讀
  lane    每幀跑 lane_detect.py 的車道偵測，算 CTE
網頁的 /video/* 只是把「最新的結果」編成 JPEG 送出去，不會等 YOLO 算完（講義：非同步狀態更新）。
"""
import os
import threading
import time
from collections import Counter

import cv2
import numpy as np

import lane_detect as ld

W, H = 640, 480
YOLO_EVERY = 3            # 跳幀：每 3 幀辨識一次
# ---- 省電設定（10/14 實測：YOLO 讓 CPU 全速 + 馬達啟動 → throttled=0x50000 電壓不足，Pi5 當機）----
YOLO_MAX_FPS = 3.0        # YOLO 每秒最多辨識幾次，CPU 不用一直全速
YOLO_THREADS = 2          # torch 只用 2 個核心（預設 4 核全開，瞬間電流最大）
LANE_MAX_FPS = 10.0       # 車道偵測每秒最多幾次
MOTOR_START_PAUSE = 0.8   # 馬達起步後這幾秒先暫停 YOLO，避開啟動電流尖峰
YOLO_IMGSZ = 320
YOLO_CONF = 0.4
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yolo_model.pt")


class FPSMeter:
    def __init__(self):
        self.t, self.fps = time.time(), 0.0

    def tick(self):
        now = time.time()
        dt, self.t = now - self.t, now
        if dt > 0:
            self.fps = 1 / dt if self.fps == 0 else 0.9 * self.fps + 0.1 / dt
        return self.fps


class Vision:
    def __init__(self, flip=True, exposure=8000, gain=10.0, lane_mode="tophat", use_yolo=True, use_lane=True,
                 log=None, motor=None, yolo_threads=YOLO_THREADS, yolo_fps=YOLO_MAX_FPS,
                 yolo_moving_fps=None):
        self.flip = flip
        self.yolo_threads = yolo_threads
        self.yolo_fps = yolo_fps                          # 停著時 YOLO 每秒最多幾次
        self.yolo_moving_fps = yolo_moving_fps or yolo_fps  # 車子在動時每秒最多幾次（0 = 動的時候不跑）
        self.log = log                    # logger.RunLogger
        self.motor = motor                # 只讀狀態寫進 log
        self.frame_t = 0.0                # 最新畫面的時間（frame_age：SAFE-02 相機斷訊用）
        self.frame = None                 # 最新原始畫面
        self.frame_id = 0
        self.yolo_frame = None
        self.lane_frame = None
        self.yolo_info = {"enabled": False, "objects": {}, "fps": 0.0, "msg": "載入中"}
        self.lane_info = {"state": "--", "cte": None, "fps": 0.0}
        self.cam_fps = FPSMeter()
        self.cam_source = "none"
        self.lock = threading.Lock()
        self.recorder = None
        self.rec_lock = threading.Lock()     # 停止錄影和寫入畫面可能同時發生
        self.rec_path = None
        self.rec_start = 0.0
        self.lane = ld.LaneDetector(lane_mode)
        self.yolo_pause_until = 0.0

        self._open_camera(exposure, gain)
        threading.Thread(target=self._camera_loop, daemon=True).start()
        if use_yolo:
            threading.Thread(target=self._yolo_loop, daemon=True).start()
        else:
            self.yolo_info["msg"] = "YOLO 已關閉（--no-yolo）"
        if use_lane:
            threading.Thread(target=self._lane_loop, daemon=True).start()

    # ---------- 相機 ----------
    def _open_camera(self, exposure, gain):
        try:
            from picamera2 import Picamera2
            self.cam = Picamera2()
            self.cam.configure(self.cam.create_video_configuration(
                main={"size": (W, H), "format": "RGB888"}))
            self.cam.start()
            if exposure > 0:
                self.cam.set_controls({"ExposureTime": exposure, "AnalogueGain": gain})
            self._grab = lambda: self.cam.capture_array()      # RGB888 實際是 BGR 排列
            self.cam_source = "picamera2"
            return
        except Exception as e:
            print(f"[vision] PiCamera 開不起來（{e}），改用 USB 相機 / 測試畫面")
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            self._grab = lambda: cap.read()[1]
            self.cam_source = "usb"
            self.flip = False
        else:
            self._grab = self._test_pattern
            self.cam_source = "test"
            self.flip = False

    def _test_pattern(self):
        time.sleep(1 / 20)
        img = np.full((H, W, 3), (60, 85, 110), np.uint8)
        t = time.time()
        off = int(60 * np.sin(t))
        cv2.line(img, (90 + off, H), (260 + off, H // 2), (230, 230, 230), 22)
        cv2.line(img, (560 + off, H), (390 + off, H // 2), (230, 230, 230), 22)
        cv2.putText(img, "TEST PATTERN (no camera)", (150, 60), 0, 0.8, (255, 255, 255), 2)
        return img

    def _camera_loop(self):
        while True:
            f = self._grab()
            if f is None:
                time.sleep(0.05)
                continue
            if f.shape[1] != W:
                f = cv2.resize(f, (W, H))
            if self.flip:
                f = cv2.flip(f, -1)
            with self.lock:
                self.frame = f
                self.frame_id += 1
                self.frame_t = time.time()
            self.cam_fps.tick()

    def latest(self):
        with self.lock:
            return (None if self.frame is None else self.frame.copy()), self.frame_id

    # ---------- YOLO ----------
    def _yolo_loop(self):
        try:
            import torch
            torch.set_num_threads(self.yolo_threads)
            from ultralytics import YOLO
            model = YOLO(MODEL_PATH)
            self.yolo_info.update(enabled=True, msg="執行中")
        except Exception as e:
            self.yolo_info.update(enabled=False, msg=f"YOLO 未載入：{type(e).__name__}")
            print(f"[vision] {self.yolo_info['msg']} {e}")
            return
        meter, last = FPSMeter(), -1
        while True:
            frame, fid = self.latest()
            moving = self.motor is not None and self.motor.action != "stop"
            fps_cap = self.yolo_moving_fps if moving else self.yolo_fps
            if frame is None or fid - last < YOLO_EVERY or time.time() < self.yolo_pause_until or fps_cap <= 0:
                time.sleep(0.01)
                continue
            last = fid
            t0 = time.time()
            r = model.predict(frame, imgsz=YOLO_IMGSZ, conf=YOLO_CONF, verbose=False)[0]
            names = [r.names[int(c)] for c in r.boxes.cls.tolist()]
            self.yolo_frame = r.plot()
            self.yolo_info.update(objects=dict(Counter(names)), fps=meter.tick())
            time.sleep(max(0.0, 1 / fps_cap - (time.time() - t0)))   # 限速，讓 CPU 休息

    # ---------- 車道 ----------
    def _lane_loop(self):
        meter, last = FPSMeter(), -1
        while True:
            frame, fid = self.latest()
            if frame is None or fid == last:
                time.sleep(0.01)
                continue
            last = fid
            t0 = time.time()
            out, info = self.lane.process(frame)
            fps = meter.tick()
            ld.draw_hud(out, info, fps)
            self.lane_frame = out
            self.lane_info = {"state": info["state"],
                              "cte": None if info["cte"] is None else round(info["cte"], 1),
                              "fps": round(fps, 1)}
            if self.log:
                m = self.motor.status() if self.motor else {}
                self.log.frame(frame_id=fid, lane_mode=info["state"].replace(" ONLY", "").replace("NO LANE", "NONE")
                               .replace("DOUBLE", "BOTH"),
                               cte_px=self.lane_info["cte"], delta_x_px=round(info["delta_x"], 1),
                               lane_fps=round(fps, 1), cam_fps=round(self.cam_fps.fps, 1),
                               yolo_fps=round(self.yolo_info["fps"], 1), frame_age_ms=self.frame_age_ms(),
                               action=m.get("action", ""), speed=m.get("speed", ""),
                               recording=int(self.recorder is not None))
            time.sleep(max(0.0, 1 / LANE_MAX_FPS - (time.time() - t0)))
            self._record(out)

    # ---------- 錄影（存 YOLO 或車道畫面，給「繞場一周」的作業錄影用）----------
    def start_recording(self, folder="recordings"):
        os.makedirs(folder, exist_ok=True)
        with self.rec_lock:
            if self.recorder is not None:
                return self.rec_path
            self.rec_path = os.path.join(folder, time.strftime("run_%Y%m%d_%H%M%S.mp4"))
            self.recorder = cv2.VideoWriter(self.rec_path, cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (W * 2, H))
            self.rec_start = time.time()
        if self.log:
            self.log.event("record_start", self.rec_path)
        return self.rec_path

    def stop_recording(self):
        with self.rec_lock:
            rec, self.recorder = self.recorder, None
            if rec:
                rec.release()
        if rec and self.log:
            self.log.event("record_stop", self.rec_path)
        return self.rec_path

    def _record(self, lane_out):
        if self.recorder is None:
            return
        yolo = self.yolo_frame if self.yolo_frame is not None else np.zeros_like(lane_out)
        both = np.hstack([cv2.resize(yolo, (W, H)), lane_out])
        cv2.putText(both, f"REC {time.time() - self.rec_start:5.1f}s", (W * 2 - 190, 30), 0, 0.8, (0, 0, 255), 2)
        with self.rec_lock:
            if self.recorder is not None:
                self.recorder.write(both)

    def pause_yolo(self, sec=MOTOR_START_PAUSE):
        """馬達起步時呼叫：暫停 YOLO 一下，讓啟動電流和 CPU 全速不要同時發生。"""
        self.yolo_pause_until = time.time() + sec

    def frame_age_ms(self):
        return int((time.time() - self.frame_t) * 1000) if self.frame_t else None

    def status(self):
        return {"camera": self.cam_source, "cam_fps": round(self.cam_fps.fps, 1),
                "frame_age_ms": self.frame_age_ms(),
                "yolo": {**self.yolo_info, "fps": round(self.yolo_info["fps"], 1)},
                "lane": self.lane_info,
                "recording": self.recorder is not None,
                "rec_sec": round(time.time() - self.rec_start, 1) if self.recorder else 0}

    def jpeg(self, kind):
        img = {"raw": self.latest()[0], "yolo": self.yolo_frame, "lane": self.lane_frame}.get(kind)
        if img is None:
            img = np.zeros((H, W, 3), np.uint8)
            msg = self.yolo_info["msg"] if kind == "yolo" else "waiting..."
            cv2.putText(img, msg.encode("ascii", "ignore").decode() or "YOLO not loaded",
                        (40, H // 2), 0, 0.8, (200, 200, 200), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes()
