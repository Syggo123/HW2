"""
把 logs/ 的 CSV 變成報告用的圖表 + 指標（W7 講義 p.22、p.23）

在電腦上跑（先用 WinSCP 把 Pi 的 logs/ 整個拉回來）：
  pip install pandas matplotlib
  python tools/analyze.py                     # 分析最新一次 run + 所有 tc01 / safe01 / power
  python tools/analyze.py --run R20261021_141327
輸出：reports/*.png + reports/summary.md（直接貼進 README / HTML 報告）

圖表文字用英文，避免電腦沒有中文字型變成方塊。
"""
import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS, OUT = os.path.join(ROOT, "logs"), os.path.join(ROOT, "reports")
MODE_COLOR = {"BOTH": "#2e7d32", "LEFT": "#1565c0", "RIGHT": "#ef6c00", "NONE": "#c62828"}
md = []


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    md.append(f"![{name}]({name})\n")
    print("  →", os.path.relpath(path, ROOT))


def t_sec(df):
    return (df["timestamp_ms"] - df["timestamp_ms"].iloc[0]) / 1000


def moving_spans(ax, t, action):
    """把馬達在動的時段塗成淡灰色，方便對照「一動就出事」。"""
    moving = (action.fillna("stop") != "stop").to_numpy()
    start = None
    for i, m in enumerate(moving):
        if m and start is None:
            start = t.iloc[i]
        if (not m or i == len(moving) - 1) and start is not None:
            ax.axvspan(start, t.iloc[i], color="0.85", lw=0)
            start = None


def latest_run():
    runs = sorted(glob.glob(os.path.join(LOGS, "R*_frames.csv")))
    return os.path.basename(runs[-1]).replace("_frames.csv", "") if runs else None


def analyze_frames(run):
    p = os.path.join(LOGS, f"{run}_frames.csv")
    if not os.path.exists(p):
        return
    df = pd.read_csv(p)
    if df.empty:
        return
    t = t_sec(df)
    cte = df["cte_px"].dropna()
    mode = df["lane_mode"].value_counts(normalize=True) * 100
    md.append(f"## 車道偵測（{run}）\n")
    md.append("| 指標 | 數值 |\n|---|---|")
    md.append(f"| 影格數 | {len(df)} |")
    md.append(f"| 時間長度 | {t.iloc[-1]:.1f} s |")
    md.append(f"| MAE \\|CTE\\| | {cte.abs().mean():.1f} px |")
    md.append(f"| RMSE CTE | {np.sqrt((cte ** 2).mean()):.1f} px |")
    md.append(f"| 最大 \\|CTE\\| | {cte.abs().max():.1f} px |")
    for k in ("BOTH", "LEFT", "RIGHT", "NONE"):
        md.append(f"| {k} 比例 | {mode.get(k, 0):.1f} % |")
    md.append(f"| 車道 FPS（平均） | {df['lane_fps'].mean():.1f} |")
    md.append(f"| YOLO FPS（平均） | {df['yolo_fps'].mean():.1f} |")
    md.append(f"| frame_age p95 | {df['frame_age_ms'].quantile(.95):.0f} ms |\n")
    md.append("> 註：這是遙控資料，CTE 反映的是「人開得多正」；等加上 PID 自動模式後，同一條路線比較才有意義。\n")

    fig, ax = plt.subplots(figsize=(10, 3.6))
    moving_spans(ax, t, df["action"])
    for k, c in MODE_COLOR.items():
        m = df["lane_mode"] == k
        ax.scatter(t[m], df["cte_px"][m], s=6, color=c, label=k)
    ax.axhline(0, color="k", lw=0.8)
    ax.set(xlabel="time (s)", ylabel="CTE (px)", title=f"CTE over time  (grey = motor moving)  {run}")
    ax.legend(loc="upper right", markerscale=3, ncol=4)
    save(fig, f"{run}_cte_time.png")

    fig, axs = plt.subplots(1, 2, figsize=(10, 3.4))
    axs[0].hist(cte.abs(), bins=30, color="#455a64")
    axs[0].set(xlabel="|CTE| (px)", ylabel="frames", title="|CTE| distribution")
    keys = [k for k in MODE_COLOR if k in mode]
    axs[1].bar(keys, [mode[k] for k in keys], color=[MODE_COLOR[k] for k in keys])
    axs[1].set(ylabel="% of frames", title="lane_mode ratio")
    save(fig, f"{run}_cte_hist_mode.png")

    fig, ax = plt.subplots(figsize=(10, 3))
    for col in ("cam_fps", "lane_fps", "yolo_fps"):
        ax.plot(t, df[col], label=col, lw=1)
    ax.set(xlabel="time (s)", ylabel="FPS", title="Pipeline FPS")
    ax.legend()
    save(fig, f"{run}_fps.png")


def analyze_system(run):
    p = os.path.join(LOGS, f"{run}_system.csv")
    if not os.path.exists(p):
        return
    df = pd.read_csv(p)
    if df.empty:
        return
    plot_power(df, f"{run}_system.png", f"System  {run}", action=df.get("action"))


def plot_power(df, name, title, action=None):
    t = t_sec(df)
    thr = df["throttled"].fillna("0x0").map(lambda s: int(str(s), 16))
    fig, axs = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
    for ax in axs:
        if action is not None:
            moving_spans(ax, t, action)
    axs[0].plot(t, df["cpu_pct"], color="#6a1b9a")
    axs[0].set(ylabel="CPU %", ylim=(0, 105))
    axs[1].plot(t, df["cpu_temp_c"], color="#d84315")
    axs[1].set(ylabel="Temp (°C)")
    if df["ext5v_v"].notna().any():
        axs[2].plot(t, df["ext5v_v"], color="#1565c0", label="EXT5V input")
        axs[2].axhline(4.75, color="r", ls="--", lw=1, label="4.75 V (5 V - 5%)")
        axs[2].legend(loc="lower left")
    axs[2].set(ylabel="5V input (V)", xlabel="time (s)")
    bad = thr & 0x50005
    for ax in axs:
        for x in t[(bad != 0) & (bad.shift(fill_value=0) == 0)]:
            ax.axvline(x, color="r", lw=1.5)
    axs[0].set_title(title + "   (red line = throttled flag appears)")
    save(fig, name)
    first = t[bad != 0]
    md.append(f"- throttled 第一次出現：{'無' if first.empty else f'{first.iloc[0]:.1f} s'}"
              f"；最低 5V：{df['ext5v_v'].min() if df['ext5v_v'].notna().any() else 'N/A'} V"
              f"；最高溫度：{df['cpu_temp_c'].max()} °C\n")


def analyze_tc01():
    files = sorted(glob.glob(os.path.join(LOGS, "tc01_*.csv")))
    if not files:
        return
    md.append("## TC-01 指令可靠度\n")
    md.append("| 檔案 | client | 請求數 | 失敗率 | p50 ms | p95 ms | 最大 ms |\n|---|---|---|---|---|---|---|")
    groups, labels = [], []
    for f in files:
        df = pd.read_csv(f)
        ok = df[df["ok"] == 1]["latency_ms"]
        name = os.path.basename(f).replace(".csv", "")
        md.append(f"| {name} | {df['client'].nunique()} | {len(df)} | {100 * (1 - df['ok'].mean()):.2f}% | "
                  f"{ok.median():.1f} | {ok.quantile(.95):.1f} | {ok.max():.1f} |")
        for act, g in df[df["ok"] == 1].groupby("action"):
            groups.append(g["latency_ms"])
            labels.append(f"{name.split('_')[1]}\n{act}")
    md.append("")
    fig, ax = plt.subplots(figsize=(max(6, len(groups) * 0.9), 3.8))
    ax.boxplot(groups, tick_labels=labels, showfliers=True)
    ax.set(ylabel="round-trip latency (ms)", title="TC-01 /api/move latency (c1 = 1 client, c3 = 3 clients)")
    save(fig, "tc01_latency_box.png")


def analyze_safe01():
    files = sorted(glob.glob(os.path.join(LOGS, "safe01_*.csv")))
    if not files:
        return
    df = pd.read_csv(files[-1])
    md.append("## SAFE-01 失聯停車\n")
    md.append(f"- {df['pass'].sum()}/{len(df)} 次通過；後端停車時間 平均 {df['server_gap_ms'].mean():.0f} ms、"
              f"最大 {df['server_gap_ms'].max():.0f} ms（看門狗 600 ms + 檢查週期 100 ms）\n")
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.bar(df["trial"], df["server_gap_ms"], color="#00796b", label="server: last cmd → auto stop")
    ax.plot(df["trial"], df["client_gap_ms"], "ko-", ms=4, label="client: last send → sees stop")
    ax.axhline(600, color="0.4", ls=":", label="WATCHDOG_SEC 600 ms")
    ax.axhline(750, color="r", ls="--", label="safety limit 750 ms")
    ax.set(xlabel="trial", ylabel="ms", title="SAFE-01 heartbeat loss → stop time", ylim=(0, 900))
    ax.legend(loc="lower right", fontsize=8)
    save(fig, "safe01_stop_time.png")


def analyze_power():
    for f in sorted(glob.glob(os.path.join(LOGS, "power_*.csv"))):
        df = pd.read_csv(f)
        if len(df) < 2:
            continue
        name = os.path.basename(f).replace(".csv", "")
        md.append(f"## 電源監控 {name}\n")
        plot_power(df, f"{name}.png", name)


ap = argparse.ArgumentParser()
ap.add_argument("--run", help="run_id，預設最新一次")
args = ap.parse_args()
os.makedirs(OUT, exist_ok=True)
run = args.run or latest_run()
md.append(f"# 測試結果摘要\n\n自動產生：`python tools/analyze.py{' --run ' + run if args.run else ''}`\n")
if run:
    analyze_frames(run)
    md.append(f"## 系統資源（{run}）\n")
    analyze_system(run)
analyze_tc01()
analyze_safe01()
analyze_power()
with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("  → reports/summary.md")
