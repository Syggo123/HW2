"""
單元測試：車道偵測（固定輸入 → 預期輸出，W7 講義 p.05 測試矩陣「單元」）

用程式畫出已知位置的「白膠帶」，所以正確答案（Ground Truth）是已知的：
  - 兩條線對稱 → DOUBLE，CTE ≈ 0
  - 整組車道往右平移 40px → 車子相對偏左，CTE ≈ -40（CTE = xc - x0）
  - 只畫右線 → RIGHT ONLY，中心用 delta_x 推回來
  - 空白地板 → NO LANE，CTE = None
  pytest tests/test_lane.py -v
"""
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lane_detect as ld  # noqa: E402

W, H = ld.WIDTH, ld.HEIGHT
FLOOR = (110, 140, 165)          # 木地板色（BGR）
TAPE = (235, 235, 235)


def road(shift=0, left=True, right=True, thick=18):
    """畫一張俯視透視的車道：底部寬、往上收窄。shift > 0 代表車道往右移。"""
    img = np.full((H, W, 3), FLOOR, np.uint8)
    noise = np.random.default_rng(0).integers(-6, 7, img.shape)
    img = np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)
    cx = W // 2 + shift
    if left:
        cv2.line(img, (cx - 250, H), (cx - 70, int(H * 0.45)), TAPE, thick)
    if right:
        cv2.line(img, (cx + 250, H), (cx + 70, int(H * 0.45)), TAPE, thick)
    return img, cx


@pytest.fixture
def det():
    return ld.LaneDetector("tophat")


def test_double_lane_centered(det):
    img, _ = road()
    _, info = det.process(img)
    assert info["state"] == "DOUBLE"
    assert abs(info["cte"]) < 8


@pytest.mark.parametrize("shift", [-40, 40])
def test_cte_sign_and_size(det, shift):
    img, _ = road(shift)
    _, info = det.process(img)
    assert info["state"] == "DOUBLE"
    assert info["cte"] == pytest.approx(-shift, abs=10)      # CTE = xc - x0


def test_right_only_fallback(det):
    # 先看雙線，讓 delta_x 校正到這個畫面的車道寬
    for _ in range(30):
        det.process(road()[0])
    img, _ = road(right=True, left=False)
    _, info = det.process(img)
    assert info["state"] == "RIGHT ONLY"
    assert abs(info["cte"]) < 25


def test_left_only_fallback(det):
    for _ in range(30):
        det.process(road()[0])
    _, info = det.process(road(left=True, right=False)[0])
    assert info["state"] == "LEFT ONLY"
    assert abs(info["cte"]) < 25


def test_no_lane(det):
    _, info = det.process(road(left=False, right=False)[0])
    assert info["state"] == "NO LANE"
    assert info["cte"] is None


def test_wide_glare_rejected(det):
    """大片亮區（窗戶反光）不是膠帶：top-hat + 厚度過濾後不該變成車道線。"""
    img, _ = road(left=False, right=False)
    cv2.ellipse(img, (W // 2, int(H * 0.8)), (220, 60), 0, 0, 360, (250, 250, 250), -1)
    _, info = det.process(img)
    assert info["state"] == "NO LANE"


def test_hough_shape_opencv4_and_5():
    """OpenCV 4 回傳 (N,1,4)、OpenCV 5 回傳 (N,4)，fit_lanes 兩種都要吃。"""
    segs = np.array([[100, 470, 250, 250], [540, 470, 390, 250]])
    a = ld.fit_lanes(segs, W)
    b = ld.fit_lanes(segs.reshape(-1, 1, 4), W)
    assert a == b and a[0] is not None and a[1] is not None
