"""
單元 / 整合測試：馬達控制器（不用小車，電腦上 pytest 就能跑）

  pytest tests/ -v

用一個「假 PCA9685」取代 LOBOROBOT2：每次寫入都故意停一下，同時檢查有沒有兩個執行緒同時在寫。
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_control as mc  # noqa: E402


class SpyRobot:
    """記錄每一次 I2C 寫入；inside > 1 代表有兩個執行緒同時在寫（交錯寫入）。"""
    def __init__(self, delay=0.002, fail_on=None):
        self.calls, self.inside, self.max_inside = [], 0, 0
        self.delay, self.fail_on = delay, fail_on
        self.lock = threading.Lock()

    def _write(self, name, *args):
        with self.lock:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        time.sleep(self.delay)
        with self.lock:
            self.inside -= 1
            self.calls.append((name, args))
        if self.fail_on == name:
            raise OSError(121, "Remote I/O error")

    def __getattr__(self, name):
        return lambda *a: self._write(name, *a)


def make(robot, speed=40):
    m = mc.MotorController(speed)
    m.robot = robot
    robot.calls.clear()
    return m


def test_unknown_action_rejected():
    m = make(SpyRobot())
    with pytest.raises(ValueError):
        m.move("jump")


def test_repeat_same_action_does_not_rewrite_i2c():
    """按住搖桿每 150ms 重送同一個動作：只更新看門狗，不重寫 I2C。"""
    r = SpyRobot()
    m = make(r)
    m.move("left")
    n = len(r.calls)
    for _ in range(10):
        m.move("left")
    assert len(r.calls) == n
    assert m.action == "left"


def test_forward_soft_start_from_stop():
    """從靜止起步：1/3 → 1/2 → 全速。"""
    r = SpyRobot()
    m = make(r, speed=60)
    m.move("forward")
    speeds = [a[0] for name, a in r.calls if name == "moveforward"]
    assert speeds == [20, 40, 60]


def test_stop_only_stops_wheels_not_servos():
    r = SpyRobot()
    m = make(r)
    m.move("forward")
    r.calls.clear()
    m.move("stop")
    assert [n for n, _ in r.calls] == ["MotorStop"] * 4      # 沒有 t_stop（會關掉鏡頭伺服）


def test_concurrent_requests_never_interleave_i2c():
    """TC 整合：5 個執行緒（模擬 5 個瀏覽器）同時狂送指令，I2C 寫入不可交錯。"""
    r = SpyRobot(delay=0.001)
    m = make(r)
    actions = ["forward", "stop", "left", "right", "backward"]

    def spam(k):
        for i in range(40):
            m.move(actions[(k + i) % len(actions)])

    ts = [threading.Thread(target=spam, args=(k,)) for k in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert r.max_inside == 1, "有兩個執行緒同時寫 I2C"
    assert m.cmd_count == 200


def test_without_lock_would_interleave():
    """對照組：同樣的壓力，不經過 MotorController 的鎖直接寫，就會交錯（證明上一個測試有意義）。"""
    r = SpyRobot(delay=0.002)

    def spam():
        for _ in range(20):
            r.moveforward(30, 0)

    ts = [threading.Thread(target=spam) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert r.max_inside > 1


def test_watchdog_stops_after_timeout():
    """SAFE-01 單元版：送一次前進後不再送，WATCHDOG_SEC 後要自動停車。"""
    r = SpyRobot()
    m = make(r)
    m.move("forward")
    time.sleep(mc.WATCHDOG_SEC + 0.25)
    assert m.action == "stop"
    gap = m.last_auto_stop - m.last_cmd
    assert mc.WATCHDOG_SEC <= gap <= mc.WATCHDOG_SEC + 0.15
    assert ("MotorStop", (3,)) in r.calls


def test_watchdog_not_triggered_by_heartbeat():
    r = SpyRobot()
    m = make(r)
    t_end = time.time() + 1.5
    while time.time() < t_end:
        m.move("forward")
        time.sleep(0.15)
    assert m.action == "forward"
    m.move("stop")


def test_i2c_error_is_counted_and_car_stops():
    """I2C 寫失敗（接線鬆 / 掉電）：要計數、往上丟 I2CError、狀態回到 stop。"""
    r = SpyRobot(fail_on="turnLeft")
    m = make(r)
    with pytest.raises(mc.I2CError):
        m.move("left")
    assert m.i2c_errors == 1
    assert m.action == "stop"


def test_camera_angle_is_clamped():
    m = make(SpyRobot())
    m.set_camera(tilt=999, pan=-50)
    assert (m.tilt, m.pan) == (mc.TILT_RANGE[1], mc.PAN_RANGE[0])


def test_turn_has_soft_start():
    """10/21 修正：左右轉也要緩啟動（原本直接全速，T4 一按右就當機）。"""
    r = SpyRobot()
    m = make(r, speed=60)
    m.move("right")
    assert [a[0] for n, a in r.calls if n == "turnRight"] == [20, 40, 60]


def test_direction_change_stops_first():
    """前進 → 右轉：先 MotorStop 四輪、停一下，再緩啟動轉彎，不讓馬達直接反轉。"""
    r = SpyRobot()
    m = make(r, speed=60)
    m.move("forward")
    r.calls.clear()
    t0 = time.time()
    m.move("right")
    names = [n for n, _ in r.calls]
    assert names[:4] == ["MotorStop"] * 4
    assert names[4:] == ["turnRight"] * 3
    assert time.time() - t0 >= m.reverse_pause + m.soft_start - 0.01
