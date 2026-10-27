"""
AI 自走車 Flask 控制台（第二次作業）

執行：
    source .venv/bin/activate
    python app.py
然後在同一個網路的電腦 / 手機開 http://<Pi5 的 IP>:5000

路由：
    /                    控制台網頁
    /video/<raw|yolo|lane>   MJPEG 視訊串流（三個視訊框）
    /api/move   POST {"action": "forward|backward|left|right|stop"}
    /api/speed  POST {"speed": 0~100}
    /api/camera POST {"dir": "up|down|left|right|center"}
    /api/record POST {"on": true|false}
    /api/status GET  車況 + 視訊狀態（網頁每 0.5 秒抓一次）

每次執行都會在 logs/ 留下 CSV 測試紀錄（見 logger.py），加 --no-log 關掉。
"""
import argparse
import atexit
import time

from flask import Flask, Response, jsonify, render_template, request

from logger import RunLogger
from motor_control import I2CError, MotorController
from vision import Vision

app = Flask(__name__)
motor = None
vision = None
log = None


@app.route("/")
def index():
    return render_template("index.html")


def mjpeg(kind):
    while True:
        frame = vision.jpeg(kind)
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(1 / 15)            # 串流最多 15 fps，省頻寬也省 CPU


@app.route("/video/<kind>")
def video(kind):
    if kind not in ("raw", "yolo", "lane"):
        return "unknown stream", 404
    return Response(mjpeg(kind), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/move")
def api_move():
    t0 = time.perf_counter()
    action = (request.get_json(silent=True) or {}).get("action", "stop")
    if action != "stop" and motor.action != action:
        vision.pause_yolo()           # 換動作 / 起步瞬間電流最大，先讓 YOLO 停一下
    ok, err, code, i2c = True, "", 200, 0
    try:
        motor.move(action)
    except ValueError as e:
        ok, err, code = False, str(e), 400
    except I2CError as e:
        ok, err, code, i2c = False, f"I2C: {e}", 500, 1
    ms = round((time.perf_counter() - t0) * 1000, 2)
    log.api_call(endpoint="move", action=action, ok=int(ok), api_latency_ms=ms, i2c_error=i2c, error=err)
    if not ok:
        return jsonify(ok=False, error=err), code
    return jsonify(ok=True, action=motor.action, server_ms=ms)


@app.post("/api/speed")
def api_speed():
    motor.set_speed((request.get_json(silent=True) or {}).get("speed", 40))
    return jsonify(ok=True, speed=motor.speed)


@app.post("/api/camera")
def api_camera():
    motor.nudge_camera((request.get_json(silent=True) or {}).get("dir", "center"))
    return jsonify(ok=True, tilt=motor.tilt, pan=motor.pan)


@app.post("/api/record")
def api_record():
    on = bool((request.get_json(silent=True) or {}).get("on"))
    path = vision.start_recording() if on else vision.stop_recording()
    log.api_call(endpoint="record", action="on" if on else "off", ok=1)
    return jsonify(ok=True, recording=on, path=path)


@app.get("/api/status")
def api_status():
    return jsonify(motor=motor.status(), vision=vision.status(), time=time.strftime("%H:%M:%S"),
                   server_ms=int(time.time() * 1000), cpu_temp=cpu_temp(), run_id=log.run_id)


def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000, 1)
    except OSError:
        return None


def main():
    global motor, vision, log
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--speed", type=int, default=40)
    ap.add_argument("--no-flip", action="store_true", help="相機沒裝反就加這個")
    ap.add_argument("--lane-mode", default="tophat", choices=["tophat", "gray", "white", "hsv"])
    ap.add_argument("--no-yolo", action="store_true", help="關掉 YOLO（除錯用：看是不是 CPU 不夠）")
    ap.add_argument("--no-lane", action="store_true", help="關掉車道偵測")
    ap.add_argument("--only-raw", action="store_true", help="關掉 YOLO 和車道偵測，只留原始畫面，用來單測馬達")
    ap.add_argument("--no-log", action="store_true", help="不寫 logs/ 的 CSV")
    ap.add_argument("--yolo-threads", type=int, default=2, help="YOLO 用幾個 CPU 核心（1 最省電）")
    ap.add_argument("--yolo-fps", type=float, default=3.0, help="車子停著時 YOLO 每秒最多幾次")
    ap.add_argument("--yolo-moving-fps", type=float, default=None,
                    help="車子在動時 YOLO 每秒最多幾次（預設同 --yolo-fps；0 = 動的時候不跑）")
    ap.add_argument("--soft-start", type=float, default=0.1, help="起步緩啟動秒數（0.1 原本、0.4 較緩）")
    ap.add_argument("--reverse-pause", type=float, default=0.1, help="換方向前先停輪幾秒（避免馬達直接反轉）")
    ap.add_argument("--tag", default="", help="這次測試的標籤，會加在 run_id 後面，例如 --tag S1_straight")
    args = ap.parse_args()
    if args.only_raw:
        args.no_yolo = args.no_lane = True

    import vision as V
    log = RunLogger(config={**vars(args), "YOLO_IMGSZ": V.YOLO_IMGSZ, "YOLO_EVERY": V.YOLO_EVERY,
                            "LANE_MAX_FPS": V.LANE_MAX_FPS, "ROI_TOP_Y": V.ld.ROI_TOP_Y},
                    enabled=not args.no_log, tag=args.tag)
    motor = MotorController(args.speed, log=log, soft_start=args.soft_start,
                            reverse_pause=args.reverse_pause)
    log.status_fn = lambda: motor.action
    vision = Vision(flip=not args.no_flip, lane_mode=args.lane_mode,
                    use_yolo=not args.no_yolo, use_lane=not args.no_lane, log=log, motor=motor,
                    yolo_threads=args.yolo_threads, yolo_fps=args.yolo_fps,
                    yolo_moving_fps=args.yolo_moving_fps)
    log.event("start", " ".join(f"--{k}={v}" for k, v in vars(args).items()))
    atexit.register(motor.shutdown)
    atexit.register(vision.stop_recording)
    # threaded=True：三個視訊串流 + 控制指令要同時處理
    app.run(host="0.0.0.0", port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
