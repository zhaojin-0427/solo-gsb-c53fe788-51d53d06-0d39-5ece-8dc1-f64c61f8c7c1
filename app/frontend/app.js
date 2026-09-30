"use strict";

// ---------------- 全局状态 ----------------
const state = {
  scenarioId: null,          // 已落库场景 ID
  payload: null,             // 场景 JSON（flights / 安全参数）
  flightById: new Map(),
  order: [],                 // 架次 ID 顺序
  colors: {},
  earliestConflicts: [],
  solvedConflicts: [],
  result: null,              // 求解结果
  viewMode: "earliest",
  locks: {},                 // 手动锁定
  draftStarts: {},           // 时间轴上手动拖动的临时时刻（求解前）
  bounds: null,              // 各架次窗口/权重
  plans: [],
  hoveredPair: null,
};

const PALETTE = [
  "#42a5f5", "#ffb74d", "#ba68c8", "#4db6ac",
  "#f06292", "#aed581", "#fff176", "#7986cb",
  "#4dd0e1", "#a1887f", "#81c784", "#e57373",
];

const $ = (id) => document.getElementById(id);

function toast(msg, kind = "") {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(method, url, body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const resp = await fetch(url, opt);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join("；") : (data.detail || resp.statusText);
    throw new Error(detail);
  }
  return data;
}

// ---------------- 载入数据 ----------------
function loadPayload(payload, scenarioId = null) {
  state.payload = payload;
  state.scenarioId = scenarioId;
  state.flightById = new Map(payload.flights.map((f) => [f.id, f]));
  state.order = payload.flights.map((f) => f.id);
  state.colors = {};
  state.order.forEach((id, i) => { state.colors[id] = PALETTE[i % PALETTE.length]; });
  state.locks = {};
  state.draftStarts = {};
  state.result = null;
  state.earliestConflicts = [];
  state.solvedConflicts = [];
  $("safetyRadius").value = payload.safetyRadius;
  $("safetyHeight").value = payload.safetyHeight;
  $("flightCount").textContent = state.order.length;
  document.querySelector('input[name=viewMode][value=earliest]').checked = true;
  state.viewMode = "earliest";
  renderAll();
  scheduleEvaluate();
}

async function persistScenario() {
  if (state.scenarioId !== null) return state.scenarioId;
  const created = await api("POST", "/api/scenarios", { payload: state.payload });
  state.scenarioId = created.id;
  await refreshScenarioSelect();
  return created.id;
}

async function loadSample(which) {
  const samples = (await api("GET", "/api/samples")).samples;
  loadPayload(samples[which]);
  toast("已载入示例，可直接求解；保存后进入场景库", "ok");
}

// ---------------- 冲突评估 ----------------
let evalTimer = null;
function scheduleEvaluate() {
  clearTimeout(evalTimer);
  evalTimer = setTimeout(doEvaluate, 250);
}

async function doEvaluate() {
  if (!state.payload) return;
  const radius = parseFloat($("safetyRadius").value);
  const height = parseFloat($("safetyHeight").value);
  if (!(radius > 0) || !(height > 0)) return;
  state.payload.safetyRadius = radius;
  state.payload.safetyHeight = height;
  try {
    // 最早起飞时刻冲突（无 shifts 参数即取窗口下界）
    const earliestOnly = await api("POST", "/api/evaluate", { payload: state.payload });
    state.earliestConflicts = earliestOnly.conflicts;
    state.bounds = earliestOnly.flightBounds;
    // 求解视图：有可行解就按求解时刻评估；手动拖动则按草稿时刻评估
    const hasDraft = Object.keys(state.draftStarts).length > 0;
    if (hasDraft) {
      const cur = await api("POST", "/api/evaluate", { payload: state.payload, shifts: currentStarts() });
      state.solvedConflicts = cur.conflicts;
    } else if (state.result && state.result.feasible) {
      const solved = await api("POST", "/api/evaluate",
        { payload: state.payload, shifts: state.result.starts });
      state.solvedConflicts = solved.conflicts;
    } else {
      state.solvedConflicts = [];
    }
    renderTable();
    renderConflicts();
    renderMap();
    renderTimeline();
  } catch (e) {
    toast("评估失败：" + e.message, "error");
  }
}

function currentStarts() {
  if (state.result && state.result.feasible) return state.result.starts;
  const out = {};
  state.order.forEach((id) => {
    out[id] = id in state.draftStarts ? state.draftStarts[id] : state.flightById.get(id).earliest;
  });
  return out;
}

// ---------------- 求解 ----------------
async function doSolve() {
  if (!state.payload) return;
  const summary = $("solverSummary");
  summary.className = "solver-summary";
  summary.textContent = "求解中…（CP-SAT 加权延误最优 + 架次 ID 字典序择优）";
  try {
    const sid = await persistScenario();
    const radius = parseFloat($("safetyRadius").value);
    const height = parseFloat($("safetyHeight").value);
    const data = await api("POST", "/api/solve", {
      scenarioId: sid, locks: state.locks,
      safetyRadius: radius, safetyHeight: height, timeLimit: 30,
    });
    state.result = data.result;
    state.earliestConflicts = data.earliestConflicts || [];
    state.solvedConflicts = data.solvedConflicts || [];
    state.bounds = data.flightBounds;
    state.draftStarts = {};
    if (data.result.feasible) {
      document.querySelector('input[name=viewMode][value=solved]').checked = true;
      state.viewMode = "solved";
      $("btnSavePlan").disabled = false;
    } else {
      $("btnSavePlan").disabled = true;
    }
    renderAll();
  } catch (e) {
    summary.className = "solver-summary bad";
    summary.textContent = "求解失败：" + e.message;
  }
}

// ---------------- 表格 ----------------
function renderTable() {
  const tb = $("flightTable").querySelector("tbody");
  tb.innerHTML = "";
  const starts = currentStarts();
  for (const id of state.order) {
    const f = state.flightById.get(id);
    const tr = document.createElement("tr");
    if (id in state.locks) tr.classList.add("locked-row");
    const sVal = starts[id];
    const delay = Math.max(0, sVal - f.earliest);
    tr.innerHTML = `
      <td><span class="dot" style="background:${state.colors[id]}"></span></td>
      <td>${escapeHtml(id)}</td>
      <td>${f.weight}</td>
      <td>${f.earliest}–${f.latest}</td>
      <td>${f.earliest}</td>
      <td><input type="number" min="${f.earliest}" max="${f.latest}" step="1" value="${sVal}" data-id="${id}" class="start-input"></td>
      <td class="delay-cell">${delay}</td>
      <td><input type="checkbox" class="lock-check" data-id="${id}" ${id in state.locks ? "checked" : ""}></td>`;
    tb.appendChild(tr);
  }
  tb.querySelectorAll(".start-input").forEach((inp) => {
    inp.addEventListener("change", () => {
      const id = inp.dataset.id;
      const f = state.flightById.get(id);
      let v = parseInt(inp.value, 10);
      if (isNaN(v)) v = f.earliest;
      v = Math.max(f.earliest, Math.min(f.latest, v));
      state.draftStarts[id] = v;
      if (id in state.locks) state.locks[id] = v;
      scheduleEvaluate();
    });
  });
  tb.querySelectorAll(".lock-check").forEach((chk) => {
    chk.addEventListener("change", () => {
      const id = chk.dataset.id;
      if (chk.checked) state.locks[id] = currentStarts()[id];
      else delete state.locks[id];
      renderTable();
      renderTimeline();
    });
  });
  renderSummary();
}

function renderSummary() {
  const el = $("solverSummary");
  if (!state.result) {
    el.className = "solver-summary";
    el.innerHTML = state.earliestConflicts.length
      ? `最早起飞方案存在 <b style="color:#ff8a80">${state.earliestConflicts.length}</b> 段冲突，点击“求解无冲突时刻”。`
      : "最早起飞方案无冲突。";
    return;
  }
  const r = state.result;
  if (r.feasible) {
    el.className = "solver-summary ok";
    const lockTxt = Object.keys(r.locks).length
      ? `；锁定 ${Object.keys(r.locks).map((k) => `${k}=${r.locks[k]}`).join(", ")}` : "";
    el.innerHTML = `✅ 已求得无冲突方案：加权总延误 = <b>${r.weightedDelay}</b>`
      + `（求解器状态 ${r.status}${r.optimal ? "，延误最优" : ""}；按架次 ID 字典序择优${lockTxt}）`;
  } else {
    el.className = "solver-summary bad";
    el.innerHTML = `❌ 当前参数/锁定下无可行解（${r.status}）。共 ${r.mus.length} 个极小不可行架次集合，见下方诊断。`;
  }
}

// ---------------- 冲突列表 ----------------
function renderConflicts() {
  const list = $("conflictList");
  const conflicts = state.viewMode === "earliest" ? state.earliestConflicts : state.solvedConflicts;
  $("viewModeLabel").textContent = state.viewMode === "earliest" ? "最早起飞" : "求解方案";
  list.innerHTML = "";
  if (!conflicts.length) {
    list.innerHTML = `<div style="color:#7fb77f;padding:6px">该方案下无冲突。</div>`;
  }
  for (const c of conflicts) {
    const card = document.createElement("div");
    card.className = "conflict-card";
    const point = c.timeStart === c.timeEnd ? `${c.timeStart}s（端点时刻）` : `${c.timeStart}–${c.timeEnd}s`;
    card.innerHTML = `
      <div class="pair">${escapeHtml(c.pair[0])} ✕ ${escapeHtml(c.pair[1])}</div>
      <div class="meta">时刻 ${point}　水平最近 ${c.minHorizontal}m / R ${state.payload.safetyRadius}m
      ｜垂直最近 ${c.minVertical}m / H ${state.payload.safetyHeight}m</div>`;
    card.addEventListener("mouseenter", () => { state.hoveredPair = c.pair.slice().sort(); renderMap(); });
    card.addEventListener("mouseleave", () => { state.hoveredPair = null; renderMap(); });
    card.addEventListener("click", () => focusConflict(c));
    list.appendChild(card);
  }
  renderMus();
}

function renderMus() {
  const sec = $("musSection");
  if (!state.result || state.result.feasible) { sec.hidden = true; return; }
  sec.hidden = false;
  const box = $("musList");
  box.innerHTML = "";
  state.result.musForbidden.forEach((m, mi) => {
    const card = document.createElement("div");
    card.className = "mus-card";
    let rows = "";
    for (const p of m.pairs) {
      const rng = p.forbiddenRanges.map((r) => r[0] === r[1] ? `${r[0]}` : `${r[0]}…${r[1]}`).join(", ") || "（窗口无重叠）";
      rows += `<tr><td>${escapeHtml(p.pair[0])} / ${escapeHtml(p.pair[1])}</td>
        <td>${escapeHtml(p.difference)}</td><td>${rng}</td><td>[${p.reachableRange[0]}, ${p.reachableRange[1]}]</td></tr>`;
    }
    card.innerHTML = `
      <div class="members">MUS ${mi + 1}：{ ${m.members.map(escapeHtml).join(", ")} }</div>
      <div style="color:#b8a3ad;font-size:11px">这些架次的任意子集都可消解，而整体无可行整数起飞时刻。</div>
      <details><summary>互斥时差（禁止的 s_j − s_i 整数值）</summary>
        <table><thead><tr><th>架次对</th><th>时差</th><th>禁止值区间</th><th>可达范围</th></tr></thead>
        <tbody>${rows}</tbody></table></details>`;
    box.appendChild(card);
  });
  if (state.result.musTruncated) {
    box.insertAdjacentHTML("beforeend",
      `<div style="color:#f0c674">⚠️ MUS 数量较多，结果已在 20 个处截断。</div>`);
  }
}

// ---------------- 俯视图 ----------------
function trackBounds() {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const f of state.payload.flights) {
    for (const wp of f.track) {
      minX = Math.min(minX, wp.x); maxX = Math.max(maxX, wp.x);
      minY = Math.min(minY, wp.y); maxY = Math.max(maxY, wp.y);
    }
  }
  return { minX, maxX, minY, maxY };
}

let mapTransform = null;

function renderMap() {
  const canvas = $("mapCanvas");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!state.payload) return;

  const b = trackBounds();
  const pad = 48;
  const sx = (W - 2 * pad) / Math.max(1, b.maxX - b.minX);
  const sy = (H - 2 * pad) / Math.max(1, b.maxY - b.minY);
  const sc = Math.min(sx, sy);
  const ox = pad - b.minX * sc;
  const oy = pad + b.maxY * sc; // y 轴翻转（北向上）
  mapTransform = { sc, ox, oy };
  const X = (x) => ox + x * sc;
  const Y = (y) => oy - y * sc;

  // 网格
  ctx.strokeStyle = "#1c2640"; ctx.lineWidth = 1;
  ctx.fillStyle = "#5d7196"; ctx.font = "10px sans-serif";
  const gridStep = niceStep((b.maxX - b.minX) / 8);
  for (let gx = Math.ceil(b.minX / gridStep) * gridStep; gx <= b.maxX; gx += gridStep) {
    ctx.beginPath(); ctx.moveTo(X(gx), 0); ctx.lineTo(X(gx), H); ctx.stroke();
    ctx.fillText(`${gx}m`, X(gx) + 2, H - 6);
  }
  for (let gy = Math.ceil(b.minY / gridStep) * gridStep; gy <= b.maxY; gy += gridStep) {
    ctx.beginPath(); ctx.moveTo(0, Y(gy)); ctx.lineTo(W, Y(gy)); ctx.stroke();
    ctx.fillText(`${gy}m`, 4, Y(gy) - 2);
  }

  const radius = state.payload.safetyRadius;
  // 航迹
  for (const id of state.order) {
    const f = state.flightById.get(id);
    const col = state.colors[id];
    ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.beginPath();
    f.track.forEach((wp, i) => {
      const px = X(wp.x), py = Y(wp.y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
    // 航点
    ctx.fillStyle = col;
    f.track.forEach((wp) => {
      ctx.beginPath(); ctx.arc(X(wp.x), Y(wp.y), 2.5, 0, Math.PI * 2); ctx.fill();
    });
    // 起飞点/终点标记
    const st = f.track[0], en = f.track[f.track.length - 1];
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(X(st.x), Y(st.y), 6, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeRect(X(en.x) - 4, Y(en.y) - 4, 8, 8);
    ctx.fillStyle = col; ctx.font = "bold 11px sans-serif";
    ctx.fillText(id, X(st.x) + 8, Y(st.y) - 7);
  }

  // 冲突标记：最早=红，求解视图下无冲突
  const conflicts = state.viewMode === "earliest" ? state.earliestConflicts : state.solvedConflicts;
  for (const c of conflicts) {
    const midT = (c.timeStart + c.timeEnd) / 2;
    const ida = c.pair[0], idb = c.pair[1];
    const pa = c.positions[ida], pb = c.positions[idb];
    const cx = X((pa[0] + pb[0]) / 2);
    const cy = Y((pa[1] + pb[1]) / 2);
    const hovered = state.hoveredPair && state.hoveredPair[0] === ida && state.hoveredPair[1] === idb;
    ctx.strokeStyle = hovered ? "#ff1744" : "rgba(255,82,82,.85)";
    ctx.lineWidth = hovered ? 2.5 : 1.5;
    ctx.beginPath(); ctx.arc(cx, cy, radius * sc, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#ff5252";
    ctx.beginPath(); ctx.arc(cx, cy, hovered ? 6 : 4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#ff9c9c"; ctx.font = "10px sans-serif";
    ctx.fillText(`${c.timeStart}s`, cx + 7, cy - 7);
  }

  // 求解视图：在每个架次航迹中点附近给出绿色“已消解”徽标
  if (state.viewMode === "solved" && state.result && state.result.feasible) {
    ctx.fillStyle = "#4caf50";
    ctx.font = "bold 11px sans-serif";
    ctx.fillText("✓ 求解方案 0 冲突", 12, 16);
  } else if (state.viewMode === "earliest" && state.earliestConflicts.length) {
    ctx.fillStyle = "#ff8a80"; ctx.font = "bold 11px sans-serif";
    ctx.fillText(`最早起飞：${state.earliestConflicts.length} 段冲突`, 12, 16);
  }
}

function niceStep(raw) {
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(1, raw))));
  const n = raw / pow;
  return (n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10) * pow;
}

function focusConflict(c) {
  // 切到对应视图并高亮（简单实现：切到最早视图后滚动地图到顶部）
  const mode = state.solvedConflicts.includes(c) ? "solved" : "earliest";
  document.querySelector(`input[name=viewMode][value=${mode}]`).checked = true;
  state.viewMode = mode;
  state.hoveredPair = c.pair.slice().sort();
  renderAll();
  setTimeout(() => { state.hoveredPair = null; renderMap(); }, 2500);
}

// ---------------- 时间轴 ----------------
let tlGeom = null;

function tlGeometry() {
  const canvas = $("timelineCanvas");
  const starts = currentStarts();
  let tMin = Infinity, tMax = -Infinity;
  for (const id of state.order) {
    const f = state.flightById.get(id);
    const s0 = f.earliest;
    const sv = starts[id];
    tMin = Math.min(tMin, s0, f.earliest);
    tMax = Math.max(tMax, f.latest + f.track[f.track.length - 1].t,
      sv + f.track[f.track.length - 1].t, s0 + f.track[f.track.length - 1].t);
  }
  const padL = 92, padR = 30, padT = 26, rowH = 34;
  const W = canvas.width;
  const sc = (W - padL - padR) / Math.max(1, tMax - tMin);
  return { tMin, tMax, padL, padR, padT, rowH, sc, H: canvas.height, W };
}

function renderTimeline() {
  const canvas = $("timelineCanvas");
  const ctx = canvas.getContext("2d");
  const g = tlGeometry();
  tlGeom = g;
  ctx.clearRect(0, 0, g.W, g.H);
  if (!state.payload) return;
  const X = (t) => g.padL + (t - g.tMin) * g.sc;

  // 时间刻度
  ctx.strokeStyle = "#1c2640"; ctx.fillStyle = "#5d7196"; ctx.font = "10px sans-serif";
  const step = niceStep((g.tMax - g.tMin) / 12);
  for (let t = Math.ceil(g.tMin / step) * step; t <= g.tMax; t += step) {
    ctx.beginPath(); ctx.moveTo(X(t), g.padT - 8); ctx.lineTo(X(t), g.H - 8); ctx.stroke();
    ctx.fillText(`${t}s`, X(t) + 2, g.padT - 10);
  }

  const starts = currentStarts();
  const conflicts = state.viewMode === "earliest" ? state.earliestConflicts : state.solvedConflicts;

  state.order.forEach((id, i) => {
    const f = state.flightById.get(id);
    const y = g.padT + i * g.rowH;
    const dur = f.track[f.track.length - 1].t;
    // 行标签
    ctx.fillStyle = state.colors[id]; ctx.font = "bold 11px sans-serif";
    ctx.fillText(id, 8, y + 14);
    // 窗口范围（浅色底）
    ctx.fillStyle = "rgba(90,106,138,.18)";
    ctx.fillRect(X(f.earliest), y, (f.latest - f.earliest) * g.sc, 22);
    // 最早起飞条（灰蓝）
    ctx.fillStyle = "#5a6a8a";
    ctx.fillRect(X(f.earliest), y + 3, Math.max(2, dur * g.sc), 7);
    // 当前/求解条
    const sv = starts[id];
    ctx.fillStyle = id in state.locks ? "#f0c674" : "#2f7fff";
    const bx = X(sv);
    ctx.fillRect(bx, y + 12, Math.max(2, dur * g.sc), 8);
    // 起点手柄
    ctx.fillStyle = id in state.locks ? "#fff" : "#bcd7ff";
    ctx.fillRect(bx - 2, y + 10, 4, 12);
    ctx.fillStyle = "#9db4d6"; ctx.font = "10px sans-serif";
    ctx.fillText(`${sv}s`, bx + 3, y + 30);
    // 冲突时段红块
    for (const c of conflicts) {
      if (c.pair[0] !== id && c.pair[1] !== id) continue;
      ctx.fillStyle = "rgba(255,82,82,.55)";
      ctx.fillRect(X(c.timeStart), y + 1, Math.max(2, (c.timeEnd - c.timeStart) * g.sc), 20);
    }
  });
}

// 时间轴交互：拖动手柄改时刻，双击行锁定/解锁
function tlHit(px, py) {
  const g = tlGeom;
  if (!g || py < g.padT - 4) return null;
  const i = Math.floor((py - g.padT) / g.rowH);
  if (i < 0 || i >= state.order.length) return null;
  const id = state.order[i];
  const f = state.flightById.get(id);
  const y = g.padT + i * g.rowH;
  const sv = currentStarts()[id];
  const hx = g.padL + (sv - g.tMin) * g.sc;
  if (px >= hx - 8 && px <= hx + 8 && py >= y + 8 && py <= y + 26) return { id, kind: "handle", row: i };
  if (px >= g.padL && py >= y && py <= y + 22) return { id, kind: "row", row: i };
  return null;
}

let drag = null;
$("timelineCanvas").addEventListener("mousedown", (e) => {
  const rect = e.target.getBoundingClientRect();
  const px = (e.clientX - rect.left) * ($("timelineCanvas").width / rect.width);
  const py = (e.clientY - rect.top) * ($("timelineCanvas").height / rect.height);
  const hit = tlHit(px, py);
  if (hit && hit.kind === "handle") drag = hit;
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const rect = $("timelineCanvas").getBoundingClientRect();
  const px = (e.clientX - rect.left) * ($("timelineCanvas").width / rect.width);
  const g = tlGeom;
  const f = state.flightById.get(drag.id);
  let t = Math.round((px - g.padL) / g.sc + g.tMin);
  t = Math.max(f.earliest, Math.min(f.latest, t));
  state.draftStarts[drag.id] = t;
  if (drag.id in state.locks) state.locks[drag.id] = t;
  renderTable(); renderTimeline();
});
window.addEventListener("mouseup", () => {
  if (drag) { drag = null; scheduleEvaluate(); }
});
$("timelineCanvas").addEventListener("dblclick", (e) => {
  const rect = e.target.getBoundingClientRect();
  const px = (e.clientX - rect.left) * ($("timelineCanvas").width / rect.width);
  const py = (e.clientY - rect.top) * ($("timelineCanvas").height / rect.height);
  const hit = tlHit(px, py);
  if (!hit) return;
  if (hit.id in state.locks) delete state.locks[hit.id];
  else state.locks[hit.id] = currentStarts()[hit.id];
  renderTable(); renderTimeline();
  toast(hit.id in state.locks ? `已锁定 ${hit.id} = ${state.locks[hit.id]}s，再次求解将以此重算`
                              : `已解锁 ${hit.id}`, "ok");
});

// ---------------- 方案保存/回看 ----------------
async function savePlan() {
  if (!state.result || !state.result.feasible) return;
  const name = prompt("方案名称：", `方案 ${new Date().toLocaleString("zh-CN")}`);
  if (!name) return;
  try {
    const sid = await persistScenario();
    await api("POST", `/api/scenarios/${sid}/plans`, {
      scenarioId: sid, name, result: state.result,
    });
    toast("方案已保存（含输入快照）", "ok");
  } catch (e) { toast(e.message, "error"); }
}

async function openPlans() {
  const data = await api("GET", "/api/plans");
  state.plans = data.plans;
  const body = $("modalBody");
  if (!state.plans.length) {
    body.innerHTML = `<div style="color:#9db4d6">还没有保存过方案。求解后点击“保存当前方案”。</div>`;
  } else {
    body.innerHTML = "";
    for (const p of state.plans) {
      const div = document.createElement("div");
      div.className = "plan-item";
      div.innerHTML = `<div>
          <b>#${p.id} ${escapeHtml(p.name)}</b>
          <div class="meta">场景 #${p.scenarioId}｜加权延误 ${p.weightedDelay}
          ｜锁定 ${Object.keys(p.locks).length} 架｜${new Date(p.createdAt).toLocaleString("zh-CN")}</div>
        </div><div></div>`;
      const btns = div.lastChild;
      const b1 = document.createElement("button"); b1.textContent = "回看快照";
      b1.onclick = () => replayPlan(p.id);
      const b2 = document.createElement("button"); b2.textContent = "删除";
      b2.onclick = async () => { await api("DELETE", `/api/plans/${p.id}`); openPlans(); };
      btns.append(b1, b2);
      body.appendChild(div);
    }
  }
  showModal("已保存方案（含输入快照，可回看）");
}

async function replayPlan(pid) {
  const plan = await api("GET", `/api/plans/${pid}`);
  const snap = plan.scenarioSnapshot;
  // 快照场景可能已被删除：如不存在则重新入库
  let sid = plan.scenarioId;
  const exists = await api("GET", `/api/scenarios/${sid}`).catch(() => null);
  if (!exists) {
    sid = (await api("POST", "/api/scenarios", { payload: snap })).id;
  }
  loadPayload(snap, sid);
  state.result = plan.result;
  state.locks = { ...plan.locks };
  state.solvedConflicts = [];
  $("btnSavePlan").disabled = false;
  hideModal();
  renderAll();
  toast(`已回看方案 #${pid}：加权延误 ${plan.weightedDelay}`, "ok");
}

// ---------------- 场景下拉 ----------------
async function refreshScenarioSelect() {
  const data = await api("GET", "/api/scenarios");
  const sel = $("scenarioSelect");
  sel.innerHTML = `<option value="">— 场景库 —</option>` +
    data.scenarios.map((s) => `<option value="${s.id}"${s.id === state.scenarioId ? " selected" : ""}>#${s.id} ${escapeHtml(s.name)}</option>`).join("");
}

async function loadSelectedScenario() {
  const sid = parseInt($("scenarioSelect").value, 10);
  if (!sid) return;
  const row = await api("GET", `/api/scenarios/${sid}`);
  loadPayload(row.payload, sid);
  toast(`已载入场景 #${sid}`, "ok");
}

// ---------------- 渲染汇总 ----------------
function renderAll() {
  renderTable();
  renderConflicts();
  renderMap();
  renderTimeline();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

function showModal(title) { $("modalTitle").textContent = title; $("modalBackdrop").hidden = false; }
function hideModal() { $("modalBackdrop").hidden = true; }

// ---------------- 事件绑定 ----------------
$("btnSampleA").onclick = () => loadSample(0);
$("btnSampleB").onclick = () => loadSample(1);
$("btnSolve").onclick = doSolve;
$("btnSavePlan").onclick = savePlan;
$("btnPlans").onclick = openPlans;
$("btnLoadScenario").onclick = loadSelectedScenario;
$("btnClearLocks").onclick = () => {
  state.locks = {}; renderTable(); renderTimeline(); toast("已清除全部锁定");
};
$("btnSaveScenario").onclick = async () => {
  if (!state.payload) return toast("请先导入或选择场景", "error");
  try {
    const sid = await persistScenario();
    toast(`场景已保存（#${sid}）`, "ok");
    refreshScenarioSelect();
  } catch (e) { toast(e.message, "error"); }
};
$("fileInput").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const payload = JSON.parse(reader.result);
      loadPayload(payload);
      toast(`已导入 ${file.name}，可直接求解；点击“保存为场景”持久化`, "ok");
    } catch (err) { toast("JSON 解析失败：" + err.message, "error"); }
  };
  reader.readAsText(file);
  e.target.value = "";
});
$("safetyRadius").addEventListener("change", () => { state.result = null; $("btnSavePlan").disabled = true; scheduleEvaluate(); });
$("safetyHeight").addEventListener("change", () => { state.result = null; $("btnSavePlan").disabled = true; scheduleEvaluate(); });
document.querySelectorAll('input[name=viewMode]').forEach((r) => {
  r.addEventListener("change", () => { state.viewMode = r.value; renderConflicts(); renderMap(); renderTimeline(); });
});
$("modalClose").onclick = hideModal;
$("modalBackdrop").addEventListener("click", (e) => { if (e.target.id === "modalBackdrop") hideModal(); });

// 启动：载入场景库 + 示例 A
(async function init() {
  await refreshScenarioSelect();
  await loadSample(0);
})();
