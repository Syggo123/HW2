"""
W5 作業：用 LOBOROBOT2 寫 RobotControl，測試前進、後退、左轉、右轉與鏡頭伺服馬達

    from LOBOROBOT2 import LOBOROBOT
    class RobotControl

執行（車子要拿在手上，輪子會轉！）：
    python3 robot_control.py
    python3 robot_control.py --speed 40 --sec 1

LOBOROBOT2 的函式（老師提供）：
    moveforward / movebackward(speed, t)   前進 / 後退，speed 0~100
    turnLeft / turnRight(speed, t)         原地左轉 / 右轉（左右輪反轉）
    moveLeft / moveRight(speed, t)         橫移（麥克納姆輪），不是轉彎！
    t_stop(t)                              停止四輪，也會關掉 ch9、ch10 伺服馬達訊號
    set_servo_angle(channel, angle, t)     ch9 = 鏡頭上下、ch10 = 鏡頭左右
"""
import argparse
import time

from LOBOROBOT2 import LOBOROBOT

TILT_CH, PAN_CH = 9, 10
TILT_CENTER, PAN_CENTER = 40, 90      # 鏡頭回中的角度
TILT_UP, TILT_DOWN = 50, 25           # 講義：9 號 25 = 朝下、50 = 朝上
PAN_LEFT, PAN_RIGHT = 110, 70         # 講義：10 號 110 = 左轉、70 = 右轉


class RobotControl:
    def __init__(self, speed=50):
        self.robot = LOBOROBOT()
        self.speed = speed

    # ---- 車輪 ----
    def forward(self, sec):
        self.robot.moveforward(self.speed, sec)
        self.stop()

    def backward(self, sec):
        # 後退緩啟動：講義 Troubleshooting，避免瞬間電流讓 Pi5 不穩
        for s in (self.speed // 3, self.speed * 2 // 3):
            self.robot.movebackward(s, 0.1)
        self.robot.movebackward(self.speed, max(sec - 0.2, 0))
        self.stop()

    def left(self, sec):
        self.robot.turnLeft(self.speed, sec)
        self.stop()

    def right(self, sec):
        self.robot.turnRight(self.speed, sec)
        self.stop()

    def stop(self, sec=0.5):
        for m in range(4):                 # 只停輪子，不用 t_stop()，鏡頭角度才不會跑掉
            self.robot.MotorStop(m)
        time.sleep(sec)

    # ---- 鏡頭 ----
    def camera(self, tilt=None, pan=None, sec=0.8):
        if tilt is not None:
            self.robot.set_servo_angle(TILT_CH, tilt, 0)
        if pan is not None:
            self.robot.set_servo_angle(PAN_CH, pan, 0)
        time.sleep(sec)

    def camera_center(self):
        self.camera(TILT_CENTER, PAN_CENTER)

    def shutdown(self):
        self.robot.t_stop(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=int, default=50)
    ap.add_argument("--sec", type=float, default=1.5, help="每個動作幾秒")
    args = ap.parse_args()

    bot = RobotControl(args.speed)
    steps = [
        ("初始化：鏡頭回中", bot.camera_center),
        ("1. 前進測試", lambda: bot.forward(args.sec)),
        ("2. 後退測試", lambda: bot.backward(args.sec)),
        ("3. 左轉測試", lambda: bot.left(args.sec)),
        ("4. 右轉測試", lambda: bot.right(args.sec)),
        ("5. 鏡頭向上", lambda: bot.camera(tilt=TILT_UP)),
        ("6. 鏡頭向下", lambda: bot.camera(tilt=TILT_DOWN)),
        ("7. 鏡頭向左", lambda: bot.camera(tilt=TILT_CENTER, pan=PAN_LEFT)),
        ("8. 鏡頭向右", lambda: bot.camera(pan=PAN_RIGHT)),
        ("9. 鏡頭回中", bot.camera_center),
    ]
    try:
        for name, fn in steps:
            print(f"=== {name} ===")
            fn()
        print("=== 測試完成 ===")
    except KeyboardInterrupt:
        print("\n中斷")
    finally:
        bot.shutdown()
        print("已停止")


if __name__ == "__main__":
    main()
