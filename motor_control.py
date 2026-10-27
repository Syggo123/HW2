"""
馬達與鏡頭伺服控制（Flask 後端用）

講義 W6 Troubleshooting 的三個重點都做在這裡：
  1. threading.Lock：Flask 是多執行緒，多個網頁請求同時寫 I2C 會讓 PCA9685 當機
  2. 後退緩啟動：分段加速，減少瞬間電流
  3. 看門狗：網頁每 0.15 秒送一次指令，超過 WATCHDOG_SEC 沒收到就自動停車
     （網路斷線、手機鎖屏、分頁關掉時車子不會一直跑）

在沒有小車的電腦上執行時，會自動改用模擬馬達，方便先測網頁。
"""
import threading
import time

try:
    from LOBOROBOT2 import LOBOROBOT
    HARDWARE = True
except Exception as e:          # 不在 Pi5 上 / 沒開 I2C / 沒有 LOBOROBOT2.py
    print(f"[motor] 找不到小車硬體，改用模擬模式（{e}）")
    HARDWARE = False

TILT_CH, PAN_CH = 9, 10
TILT_RANGE = (20, 60)       # ch9 鏡頭上下，講義 25 = 朝下、50 = 朝上
PAN_RANGE = (50, 130)       # ch10 鏡頭左右，講義 70 = 右、110 = 左
TILT_CENTER, PAN_CENTER = 40, 90
WATCHDOG_SEC = 0.6

ACTIONS = ("forward", "backward", "left", "right", "stop")


class _FakeRobot:
    """沒有硬體時的替身，只印出動作。"""
    def __getattr__(self, name):
        def f(*args):
            pass
        return f


class I2CError(RuntimeError):
    """寫 PCA9685 失敗（OSError：Remote I/O error、接線鬆、掉電）。"""


class MotorController:
    def __init__(self, speed=40, log=None, soft_start=0.1, reverse_pause=0.1):
        self.log = log
        self.soft_start = soft_start        # 起步緩啟動總秒數（0 = 直接全速）
        self.reverse_pause = reverse_pause  # 換方向前先停輪幾秒                      # logger.RunLogger，可以是 None
        self.i2c_errors = 0
        self.cmd_count = 0
        self.last_auto_stop = 0.0           # 看門狗上次自動停車的時間（SAFE-01 用）
        self.lock = threading.Lock()
        self.robot = LOBOROBOT() if HARDWARE else _FakeRobot()
        self.speed = speed
        self.action = "stop"
        self.last_cmd = 0.0
        self.tilt, self.pan = TILT_CENTER, PAN_CENTER
        self.set_camera(self.tilt, self.pan)
        threading.Thread(target=self._watchdog, daemon=True).start()

    # ---------- 車輪 ----------
    def move(self, action):
        if action not in ACTIONS:
            raise ValueError(f"未知動作：{action}")
        with self.lock:
            self.last_cmd = time.time()
            self.cmd_count += 1
            if action == self.action:          # 同一個動作重複送（按住搖桿），只更新看門狗
                return
            prev, self.action = self.action, action
            try:
                self._drive(action, prev)
                self.last_cmd = time.time()    # 緩啟動可能花 0.5 秒，做完再更新一次，看門狗才不會誤判
            except OSError as e:               # I2C 寫失敗：記下來、停車、往上丟
                self.i2c_errors += 1
                self.action = "stop"
                if self.log:
                    self.log.event("i2c_error", f"{action}: {e}")
                try:
                    self._stop_wheels()
                except OSError:
                    pass
                raise I2CError(str(e)) from e

    def _drive(self, action, prev):
        r, s = self.robot, self.speed
        if action == "stop":
            self._stop_wheels()
            return
        fn = {"forward": r.moveforward, "backward": r.movebackward,
              "left": r.turnLeft,              # 原地轉；moveLeft 是橫移，不是轉彎
              "right": r.turnRight}[action]
        if prev != "stop":
            # 換方向（例如前進 → 右轉）：一側馬達會從正轉變反轉，瞬間電流最大，
            # 10/21 實測 T4 前進沒事、一按右就當機。先停輪讓馬達停下來，再緩啟動。
            self._stop_wheels()
            time.sleep(self.reverse_pause)
        self._ramp(fn, s)                      # 所有方向都緩啟動（原本只有前進 / 後退）
        fn(s, 0)

    def _ramp(self, fn, s):
        """在 soft_start 秒內分段加速到 s（每 50ms 一階）。0.1 秒 = 原本的 1/3 → 2/3。"""
        if self.soft_start <= 0:
            return
        n = max(2, round(self.soft_start / 0.05))
        for i in range(1, n + 1):
            fn(int(s * i / (n + 1)), 0)
            time.sleep(self.soft_start / n)

    def set_speed(self, speed):
        speed = max(0, min(100, int(speed)))
        with self.lock:
            self.speed = speed
            current = self.action
            self.action = "stop"               # 讓下一次 move() 用新速度重送
        if current != "stop":
            self.move(current)

    def _stop_wheels(self):
        for m in range(4):                     # 不用 t_stop()：它會連 ch9/ch10 伺服訊號一起關掉
            self.robot.MotorStop(m)

    def _watchdog(self):
        while True:
            time.sleep(0.1)
            if self.action != "stop" and time.time() - self.last_cmd > WATCHDOG_SEC:
                gap = time.time() - self.last_cmd
                with self.lock:
                    if self.action == "stop" or time.time() - self.last_cmd <= WATCHDOG_SEC:
                        continue               # 等鎖的期間有新指令進來（例如剛做完緩啟動），不要誤停
                    gap = time.time() - self.last_cmd
                    self.action = "stop"
                    self.last_auto_stop = time.time()
                    try:
                        self._stop_wheels()
                    except OSError as e:
                        self.i2c_errors += 1
                        print(f"[motor] 看門狗停車時 I2C 錯誤：{e}")
                if self.log:
                    self.log.event("watchdog_stop", f"gap={gap * 1000:.0f}ms")
                print(f"[motor] 看門狗：{gap:.1f} 秒沒收到網頁指令，自動停車（網頁卡住或斷線）")

    # ---------- 鏡頭 ----------
    def set_camera(self, tilt=None, pan=None):
        with self.lock:
            if tilt is not None:
                self.tilt = max(TILT_RANGE[0], min(TILT_RANGE[1], int(tilt)))
                self.robot.set_servo_angle(TILT_CH, self.tilt, 0)
            if pan is not None:
                self.pan = max(PAN_RANGE[0], min(PAN_RANGE[1], int(pan)))
                self.robot.set_servo_angle(PAN_CH, self.pan, 0)

    def nudge_camera(self, direction, step=5):
        d = {"up": (step, 0), "down": (-step, 0), "left": (0, step), "right": (0, -step)}
        if direction == "center":
            self.set_camera(TILT_CENTER, PAN_CENTER)
        elif direction in d:
            dt, dp = d[direction]
            self.set_camera(self.tilt + dt, self.pan + dp)

    def status(self):
        return {"action": self.action, "speed": self.speed, "tilt": self.tilt,
                "pan": self.pan, "hardware": HARDWARE,
                "i2c_errors": self.i2c_errors, "cmd_count": self.cmd_count,
                "last_cmd_ms": int(self.last_cmd * 1000),
                "last_auto_stop_ms": int(self.last_auto_stop * 1000),
                "watchdog_sec": WATCHDOG_SEC}

    def shutdown(self):
        with self.lock:
            self.action = "stop"
            self.robot.t_stop(0)
