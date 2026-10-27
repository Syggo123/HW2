// AI 自走車控制台前端
// 按住搖桿（或方向鍵）時每 150ms 重送一次指令；放開就送 stop。
// 後端有看門狗：0.6 秒沒收到指令會自動停車，所以斷線時車子不會一直跑。

const $ = (id) => document.getElementById(id);
const ACTION_ZH = { forward: "前進", backward: "後退", left: "左轉", right: "右轉", stop: "停止" };
const REPEAT_MS = 150;

async function post(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify(body) });
    return await r.json();
  } catch (e) { return null; }
}

// ---------- 送指令（按住重送）----------
let current = "stop";
let timer = null;
function drive(action) {
  if (action === current) return;
  current = action;
  clearInterval(timer);
  post("/api/move", { action });
  if (action !== "stop") timer = setInterval(() => post("/api/move", { action }), REPEAT_MS);
  document.querySelectorAll(".joy-label").forEach((el) => el.classList.remove("on"));
  const lbl = { forward: "up", backward: "down", left: "left", right: "right" }[action];
  if (lbl) document.querySelector(`.joy-label.${lbl}`).classList.add("on");
}

// ---------- 搖桿 ----------
const joy = $("joy"), knob = $("knob");
function joyMove(e) {
  const r = joy.getBoundingClientRect();
  let dx = e.clientX - (r.left + r.width / 2);
  let dy = e.clientY - (r.top + r.height / 2);
  const max = r.width * 0.33, dist = Math.hypot(dx, dy);
  if (dist > max) { dx = dx * max / dist; dy = dy * max / dist; }
  knob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
  if (dist < max * 0.35) return drive("stop");                       // 中心死區
  drive(Math.abs(dy) >= Math.abs(dx) ? (dy < 0 ? "forward" : "backward")
                                     : (dx < 0 ? "left" : "right"));
}
function joyEnd() {
  joy.classList.remove("active");
  knob.style.transform = "translate(-50%, -50%)";
  drive("stop");
}
joy.addEventListener("pointerdown", (e) => { joy.setPointerCapture(e.pointerId); joy.classList.add("active"); joyMove(e); });
joy.addEventListener("pointermove", (e) => { if (joy.classList.contains("active")) joyMove(e); });
joy.addEventListener("pointerup", joyEnd);
joy.addEventListener("pointercancel", joyEnd);
$("btn-stop").addEventListener("click", () => { current = ""; drive("stop"); });

// ---------- 鍵盤 ----------
const KEYS = { w: "forward", arrowup: "forward", s: "backward", arrowdown: "backward",
               a: "left", arrowleft: "left", d: "right", arrowright: "right" };
const held = new Set();
document.addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); held.clear(); current = ""; return drive("stop"); }
  if (!KEYS[k] || e.target.tagName === "INPUT") return;
  e.preventDefault(); held.add(k); drive(KEYS[k]);
});
document.addEventListener("keyup", (e) => {
  const k = e.key.toLowerCase();
  if (!KEYS[k]) return;
  held.delete(k);
  drive(held.size ? KEYS[[...held].pop()] : "stop");
});
window.addEventListener("blur", () => { held.clear(); drive("stop"); });       // 切到別的視窗就停
document.addEventListener("visibilitychange", () => { if (document.hidden) drive("stop"); });

// ---------- 速度、鏡頭、錄影 ----------
$("speed").addEventListener("input", (e) => { $("speed-val").textContent = e.target.value; });
$("speed").addEventListener("change", (e) => post("/api/speed", { speed: +e.target.value }));
document.querySelectorAll("[data-cam]").forEach((b) =>
  b.addEventListener("click", () => post("/api/camera", { dir: b.dataset.cam })));
$("btn-rec").addEventListener("click", async () => {
  const on = !$("btn-rec").classList.contains("rec-on");
  const r = await post("/api/record", { on });
  if (r && !on && r.path) $("rec-path").textContent = `已存：${r.path}`;
});

// ---------- 狀態輪詢 ----------
let fails = 0;
async function poll() {
  try {
    const s = await (await fetch("/api/status")).json();
    fails = 0;
    const m = s.motor, v = s.vision, lane = v.lane, yolo = v.yolo;
    $("pill-conn").textContent = "已連線"; $("pill-conn").className = "pill ok";
    $("pill-hw").textContent = m.hardware ? "小車硬體 OK" : "模擬馬達";
    $("pill-hw").className = "pill " + (m.hardware ? "ok" : "bad");
    $("clock").textContent = s.time;

    $("st-action").textContent = ACTION_ZH[m.action] || m.action;
    $("st-lane").textContent = lane.state;
    $("st-lane").className = "lane-" + String(lane.state).split(" ")[0];
    $("st-cte").textContent = lane.cte === null ? "--"
      : `${lane.cte > 0 ? "+" : ""}${lane.cte} px（${lane.cte > 5 ? "偏右" : lane.cte < -5 ? "偏左" : "置中"}）`;
    $("st-yolo").textContent = yolo.enabled ? `執行中 · ${yolo.fps} fps` : yolo.msg;
    $("st-cam").textContent = `${v.camera} · ${v.cam_fps} fps`;
    $("st-temp").textContent = s.cpu_temp === null ? "--" : `${s.cpu_temp} °C`;
    $("cam-angle").textContent = `上下 ${m.tilt}° · 左右 ${m.pan}°`;
    $("cap-raw").textContent = `${v.cam_fps} fps`;
    $("cap-yolo").textContent = yolo.enabled ? `${yolo.fps} fps` : "未啟用";
    $("cap-lane").textContent = `${lane.fps} fps`;
    if (document.activeElement !== $("speed")) { $("speed").value = m.speed; $("speed-val").textContent = m.speed; }

    const objs = Object.entries(yolo.objects || {});
    $("objects").innerHTML = objs.length
      ? objs.map(([k, n]) => `<li>${k} × ${n}</li>`).join("")
      : '<li class="muted">尚無</li>';

    $("pill-rec").hidden = !v.recording;
    $("pill-rec").textContent = `● REC ${Math.round(v.rec_sec)}s`;
    $("btn-rec").classList.toggle("rec-on", v.recording);
    $("btn-rec").textContent = v.recording ? "停止錄影" : "開始錄影";
  } catch (e) {
    if (++fails >= 2) { $("pill-conn").textContent = "連線中斷"; $("pill-conn").className = "pill bad"; }
  }
}
setInterval(poll, 500);
poll();
