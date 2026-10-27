# AI 自走車 Flask 控制台（第二次作業）

> 嵌入式 AI 與自駕車系統設計 · 第 4 組 · 411002249 張庭茂 / 411206234 洪奕群

用網頁遙控 Raspberry Pi 5 小車：三個即時視訊框（原始畫面 / YOLOv8 物件辨識 / 車道偵測）、方向搖桿、車況與視訊狀態欄，可直接錄下「YOLO + 車道偵測」並排的繞場影片。

## 專案架構

```
ai_car_project/
├── app.py              # Flask 後端：網頁、MJPEG 串流、控制 API
├── motor_control.py    # 馬達 / 鏡頭伺服控制（I2C 鎖、後退緩啟動、看門狗自動停車）
├── vision.py           # 相機擷取、YOLO 辨識、車道偵測、錄影（三條執行緒）
├── lane_detect.py      # 車道偵測：top-hat → Canny → ROI → Hough → CTE（第一次作業）
├── robot_control.py    # W5 馬達測試：前進、後退、左轉、右轉、鏡頭上下左右
├── logger.py           # W7 測試日誌：每次執行都寫 logs/<run_id>_*.csv
├── tests/
│   ├── test_motor.py   # 單元/整合：I2C 鎖、看門狗、緩啟動、I2C 錯誤（pytest，不用小車）
│   ├── test_lane.py    # 單元：合成車道圖 → 預期 lane_mode / CTE
│   ├── tc01_api.py     # TC-01 指令可靠度（單一 / 併發），量延遲與失敗率
│   ├── safe01_watchdog.py  # SAFE-01 心跳中斷 → 停車時間
│   ├── power_monitor.py    # 電源監控（throttled / 5V / 溫度），當機也留得住資料
│   └── carapi.py       # 測試腳本共用
├── tools/analyze.py    # logs/ CSV → reports/ 圖表 + summary.md
├── logs/               # 測試紀錄（自動產生）
├── reports/            # 圖表（自動產生）
├── LOBOROBOT2.py       # 老師提供的小車驅動程式（自行放入）
├── yolo_model.pt       # YOLOv8n 權重（自行下載）
├── requirements.txt
├── templates/index.html
└── static/
    ├── css/style.css
    └── js/main.js
```

## 安裝（Pi5）

```bash
sudo raspi-config            # Interface Options → I2C → Enable，然後重開機
cd ~/ai_car_project
python -m venv --system-site-packages .venv      # 要用系統的 picamera2，一定要加這個參數
source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
pip install -r requirements.txt --no-cache-dir
wget https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8n.pt -O yolo_model.pt
```

> 先裝 CPU 版 torch：直接 `pip install ultralytics` 會下載幾百 MB 的 NVIDIA CUDA 套件，Pi5 用不到，SD 卡會爆（OSError Errno 28）。

## 執行

```bash
python robot_control.py      # 先測馬達（車子拿在手上！）
python app.py                # 開控制台
```

瀏覽器開 `http://<Pi5 的 IP>:5000`。

| 操作 | 方式 |
|---|---|
| 前進 / 後退 / 左轉 / 右轉 | 拖曳搖桿，或 W A S D / 方向鍵，放開即停 |
| 緊急停止 | 停止按鈕或空白鍵 |
| 速度 | 滑桿 10–100 |
| 鏡頭角度 | ▲▼◀▶，⌂ 回中 |
| 錄影 | 「開始錄影」，影片存在 `recordings/` |

參數：`--speed 40`、`--no-flip`（相機沒裝反時）、`--lane-mode tophat|gray|white|hsv`、`--port 5000`、
`--no-yolo` / `--no-lane` / `--only-raw`（分段除錯）、`--tag S1_straight`（測試標籤）、`--no-log`

## 測試與驗證（W7）

每次執行 `app.py` 都會在 `logs/` 留下一組紀錄（`run_id` 例如 `R20261021_141530_S1_straight`）：

| 檔案 | 內容 |
|---|---|
| `_config.json` | 參數、套件版本、Git commit、板子型號 |
| `_frames.csv` | 每個車道影格：lane_mode（BOTH/LEFT/RIGHT/NONE）、cte_px、各執行緒 FPS、frame_age_ms、馬達動作 |
| `_api.csv` | 每次 `/api/move`：動作、後端處理時間、I2C 錯誤 |
| `_system.csv` | 每秒：CPU %、溫度、throttled、5V 輸入電壓、核心電壓 |
| `_events.csv` | 看門狗停車、I2C 錯誤、throttled 變化、錄影開始/結束 |

### 測試 SOP

| 編號 | 層級 | 前置 | 指令 | 通過條件 |
|---|---|---|---|---|
| UT | 單元/整合 | 電腦即可 | `pytest tests/ -v` | 全部 pass |
| TC-01 | 整合 | 輪子架空、`i2cdetect -y 1` 看得到 40 | `python tests/tc01_api.py --host <IP> --speed 25` | 失敗率 0、I2C 錯誤 0、p95 ≤ 200 ms、結束時停車 |
| TC-01c | 壓力 | 同上 | `python tests/tc01_api.py --host <IP> --clients 3` | 同上 |
| SAFE-01 | 安全 | 輪子架空 | `python tests/safe01_watchdog.py --host <IP>` | 每次 ≤ 750 ms 停車 |
| PWR | 電源 | Pi 上另開視窗 | `python3 tests/power_monitor.py` | 全程 throttled = 0x0 |

測完把 Pi 的 `logs/` 拉回電腦：`python tools/analyze.py` → `reports/` 圖表與 `summary.md`。
門檻為暫定值，依老師公布的驗收標準調整（`--max-fail`、`--max-p95`、`--limit`）。

## 設計重點 / Troubleshooting

| 問題 | 處理 |
|---|---|
| Flask 多執行緒同時寫 I2C，PCA9685 當機 | `motor_control.py` 所有馬達、伺服指令都包在 `threading.Lock` 裡 |
| YOLO 吃滿 CPU，網頁沒反應 | `imgsz=320`、每 3 幀辨識一次；YOLO 在獨立執行緒，網頁只讀最新結果 |
| 後退瞬間電流讓 Pi5 不穩 | 後退分三段加速（1/3 → 2/3 → 全速） |
| 網路斷線 / 手機鎖屏時車子一直跑 | 前端按住時每 150ms 重送指令；後端看門狗 0.6 秒沒收到就停車；切換分頁也會停 |
| `t_stop()` 會連鏡頭伺服訊號一起關掉 | 停車只停四個輪子（`MotorStop`），鏡頭角度不會跑掉 |
| `moveLeft()` 是橫移不是轉彎 | 搖桿左右接 `turnLeft()` / `turnRight()`（原地轉向） |
| 停止錄影時剛好在寫畫面，執行緒當掉 | 錄影器加鎖 |
| OpenCV 5 的 `HoughLinesP` 回傳 (N,4)，OpenCV 4 是 (N,1,4) | `lane_detect.py` 先 `reshape(-1, 4)` |
| `i2cdetect` 報 `/dev/i2c-1` 不存在 | I2C 沒開：`sudo raspi-config nonint do_i2c 0` 後重開機，再確認看得到 `40` |
| `No module named 'vision'` | 不在專案資料夾執行：先 `cd ~/ai_car_project && source .venv/bin/activate` |
| `pip install ultralytics` 後 SD 卡爆（Errno 28） | 先裝 CPU 版 torch，加 `--no-cache-dir` |

### 案例：一按前進 Pi5 就當機（電源不足）

- **現象**：只開相機可以正常遙控；全功能（相機 + 車道 + YOLO）一按前進，PuTTY 和網頁同時斷線。
- **假設**（對照講義故障案例 01）：H1 I2C 同時寫入、H3 供電壓降、H4 YOLO 高負載。
- **排查**：
  1. `i2cdetect -y 1` 看得到 0x40，且程式本來就有 `threading.Lock`，H1 可能性低。
  2. 分段測試：`--only-raw` 正常 → 加車道正常 → 加 YOLO 當機。
  3. 另開視窗每 0.3 秒記錄 `vcgencmd get_throttled`：YOLO 啟動、按前進後，從 `0x0` 變成 `0x50000`（曾經電壓不足、曾經降頻）。
  4. 軟體降耗（YOLO 每秒 3 次、2 核心、起步暫停 YOLO 0.8 秒、前進後退都緩啟動），還是會當機。
  5. 改接外部電源後正常；電池電壓量到約 7.5 V（2S 18650，滿電 8.4 V）。
- **結論**：電池電量不足，加上馬達啟動電流和 YOLO 讓 CPU 全速，電壓瞬間下降，Pi5 掉電重開。屬於硬體供電問題，軟體降耗只能延後發生。
- **處理**：電池充飽再上場；保留軟體降耗設定；`power_monitor.py` 與 `_system.csv` 持續記錄，作為之後回歸測試的證據。

沒有小車或相機的電腦也能跑 `app.py`：會自動改用模擬馬達和測試畫面，方便先調網頁。
