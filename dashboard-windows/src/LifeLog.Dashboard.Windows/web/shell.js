// 셸: 날짜 이동, 뷰 탭, 위젯 보드(칸 좌표 x·y와 크기 w·h. 위치가 고정이라 창 크기가 바뀌어도 위젯이 움직이지 않고 스크롤만 생긴다),
// 편집 모드(추가·삭제·머리띠를 끌어 이동·손잡이로 크기, 칸 단위 스냅), 설정(테마·하루 시작·칸 크기·분류 규칙).
// 뷰·위젯 배치·위젯 설정·테마·하루 시작은 기기 로컬에만 저장하고 다른 기기와 공유하지 않는다. 분류 규칙만 DB(0007)에 두고 모든 기기가 같이 쓴다.
// 휴대폰(platform android 또는 좁은 화면)은 위젯을 한 줄로 쌓는다(칸 높이 h는 그대로, 순서는 y·x). Documents/대시보드 뷰·위젯.md.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const params = new URLSearchParams(location.search);
let settings = { day_start: "04:00", tz: "", theme: "system", grid: { cell: 60, gap: 10 }, views: [], categories: [], phone_categories: [] };
let dayOffset = Number(params.get("offset")) || 0;   // 0 = 오늘(지금이 속한 하루). ?offset=-1은 개발용
let outputs = [];
let renderers = [];
let registry = null;
let viewId = null;
let editing = params.get("edit") === "1";            // ?edit=1은 개발용
let range = null;
let platform = "windows";   // 호스트가 알려 준다(windows | android)
let mobile = false;         // 한 줄 배치
let rulesShared = false;
// SecondBrain 데스크톱 안(?embed=1): 뷰 목록은 데스크톱 사이드바가 보여 주므로 탭은 편집할 때만 보인다. ?view=id로 시작 뷰
const embed = params.get("embed") === "1";
// SecondBrain 위젯(SecondBrainDesktop/widgets)이 있으면 처음 한 번 넣는 뷰. 할 일 화면이 대시보드의 페이지가 된다
const SB_VIEWS = [
  { id: "sb-todo", name: "할 일", widgets: [
    { id: "s1", type: "sb-doable", x: 0, y: 0, w: 10, h: 9, settings: {} },
    { id: "s2", type: "sb-assignments", x: 10, y: 0, w: 10, h: 5, settings: {} },
    { id: "s3", type: "sb-cleanup", x: 10, y: 5, w: 10, h: 4, settings: {} },
    { id: "s4", type: "sb-log", x: 0, y: 9, w: 20, h: 4, settings: {} } ] },
  { id: "sb-all", name: "할 일 목록", widgets: [
    { id: "s5", type: "sb-tasks", x: 0, y: 0, w: 12, h: 11, settings: {} },
    { id: "s6", type: "sb-closed", x: 12, y: 0, w: 8, h: 11, settings: {} } ] },
];    // 분류 규칙이 DB(모든 기기 공유)에 있는지. 0006 DB면 false(이 기기 로컬)

const DEFAULT_WIDGETS = [
  { type: "builtin:timeline", x: 0, y: 0, w: 20, h: 5 },
  { type: "widget-summary", x: 0, y: 5, w: 5, h: 5 },
  { type: "widget-top-apps", x: 5, y: 5, w: 5, h: 5 },
  { type: "widget-categories", x: 10, y: 5, w: 5, h: 5 },
  { type: "widget-media", x: 15, y: 5, w: 5, h: 5 },
  { type: "widget-hourly", x: 0, y: 10, w: 5, h: 5 },
  { type: "widget-location", x: 5, y: 10, w: 5, h: 5 },
  { type: "widget-category-bars", x: 10, y: 10, w: 10, h: 5 },
  { type: "widget-sunburst", x: 0, y: 15, w: 5, h: 5 },
  { type: "widget-category-tree", x: 5, y: 15, w: 5, h: 5 },
  { type: "widget-heart-rate", x: 10, y: 15, w: 5, h: 5 },
  { type: "widget-stress", x: 15, y: 15, w: 5, h: 5 },
  { type: "widget-notifications", x: 0, y: 20, w: 10, h: 6 },
];
const defaultWidgets = () => DEFAULT_WIDGETS.map(w => Object.assign({ id: uid(), settings: {} }, w));
const NETWORK_WIDGETS = [
  { type: "widget-internet", x: 0, y: 0, w: 10, h: 6 },
  { type: "widget-signal", x: 10, y: 0, w: 10, h: 6 },
  { type: "widget-wifi", x: 0, y: 6, w: 10, h: 6 },
  { type: "widget-cellular", x: 10, y: 6, w: 10, h: 6 },
  { type: "widget-bluetooth", x: 0, y: 12, w: 10, h: 6 },
];
const DEFAULT_VIEWS = [
  { id: "default", name: "기본", widgets: DEFAULT_WIDGETS.map((w, i) => Object.assign({ id: "w" + (i + 1), settings: {} }, w)) },
  { id: "network", name: "네트워크", widgets: NETWORK_WIDGETS.map((w, i) => Object.assign({ id: "n" + (i + 1), settings: {} }, w)) },
];
// 휴대폰 첫 화면: 한 줄로 쌓이므로 순서대로 y만 늘린다
const column = list => { let y = 0; return list.map((w, i) => { const o = Object.assign({ id: "m" + (i + 1), x: 0, y, w: 5, settings: {} }, w); y += o.h; return o; }); };
const DEFAULT_MOBILE_VIEWS = [
  { id: "today", name: "오늘", widgets: column([{ type: "widget-summary", h: 4 }, { type: "widget-categories", h: 5 }, { type: "widget-top-apps", h: 5 }, { type: "widget-hourly", h: 4 }, { type: "widget-location", h: 5 }, { type: "widget-media", h: 4 }, { type: "widget-notifications", h: 6 }]) },
  { id: "health", name: "건강", widgets: column([{ type: "widget-shealth", h: 4 }, { type: "widget-sleep", h: 5 }, { type: "widget-steps", h: 4 }, { type: "widget-heart-rate", h: 4 }, { type: "widget-activity", h: 3 }, { type: "widget-energy-score", h: 4 }]) },
  { id: "network", name: "네트워크", widgets: column([{ type: "widget-internet", h: 6 }, { type: "widget-wifi", h: 6 }, { type: "widget-cellular", h: 5 }, { type: "widget-signal", h: 6 }, { type: "widget-bluetooth", h: 6 }]) },
];

function dayRange(offset) {
  const [h, m] = settings.day_start.split(":").map(Number);
  const now = new Date();
  let start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0, 0);
  if (start > now) start.setDate(start.getDate() - 1);
  start.setDate(start.getDate() + offset);
  const end = new Date(start); end.setDate(end.getDate() + 1);
  return { start, end };
}
const fmtDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtTime = d => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
const hashColor = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return `hsl(${Math.abs(h) % 360} 60% 60%)`; };
const uid = () => "w" + Math.random().toString(36).slice(2, 8);

function currentView() { return settings.views.find(v => v.id === viewId) || settings.views[0]; }
async function saveSettings() { await lifelog.setSettings(settings); }   // 저장 결과로 settings를 덮어쓰지 않는다(편집 중인 배열 참조가 끊기지 않게)

// ---- 테마 ----
function effectiveTheme() {
  const t = params.get("theme") || settings.theme || "system";   // ?theme=light는 개발용
  return t === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : t;
}
function applyTheme() {
  const t = effectiveTheme();
  document.documentElement.dataset.theme = t;
  for (const f of document.querySelectorAll("iframe")) f.contentWindow.postMessage({ lifelog: "theme", theme: t }, "*");
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

// ---- 내장 타임라인(등록부의 모든 출력을 레인으로) ----
async function renderTimeline(el, { start, end }) {
  const frag = document.createDocumentFragment();
  const axis = document.createElement("div"); axis.className = "axis";
  for (let i = 0; i <= 24; i += 2) {
    const t = new Date(start.getTime() + i * 3600e3);
    const s = document.createElement("span"); s.style.left = (i / 24 * 100) + "%"; s.textContent = fmtTime(t);
    axis.appendChild(s);
  }
  frag.appendChild(axis);
  const seen = new Set();
  const qrange = { start: start.toISOString(), end: end.toISOString() };
  for (const o of outputs) {
    if (seen.has(o.view)) continue;   // 열 집합 버전이 여럿이어도 뷰는 하나
    seen.add(o.view);
    const lane = document.createElement("div"); lane.className = "lane";
    const name = document.createElement("div"); name.className = "name"; name.textContent = `${o.pluginName} · ${o.output}`; name.title = o.view;
    const track = document.createElement("div"); track.className = "track";
    lane.append(name, track); frag.appendChild(lane);
    try {
      const res = await lifelog.query(o.view, { range: qrange, limit: 5000 });
      if (res.truncated) name.textContent += " (일부)";
      const span = end - start;
      for (const r of res.rows) {
        const t0 = new Date(r.start ?? r.time), t1 = r.end ? new Date(r.end) : null;
        const label = String(r[o.label] ?? "");
        if (t1) {
          const l = Math.max(0, (t0 - start) / span), w = Math.max(0.0005, Math.min(1, (t1 - start) / span) - l);
          const b = document.createElement("div"); b.className = "block";
          b.style.left = (l * 100) + "%"; b.style.width = (w * 100) + "%"; b.style.background = hashColor(label);
          b.textContent = label; b.dataset.tip = `${fmtTime(t0)}~${fmtTime(t1)}  ${label}` + (r.title ? `\n${r.title}` : "");
          track.appendChild(b);
        } else {
          const k = document.createElement("div"); k.className = "tick";
          k.style.left = ((t0 - start) / span * 100) + "%"; k.dataset.tip = `${fmtTime(t0)}  ${label}`;
          track.appendChild(k);
        }
      }
    } catch (e) { name.textContent += " ⚠"; name.title = e.message; }
  }
  el.innerHTML = ""; el.appendChild(frag);
  if (outputs.length === 0) el.innerHTML = '<div class="empty">등록된 수집기가 없습니다.</div>';
}

// ---- 위젯 보드 ----
function gridUnits() {
  const g = settings.grid || {};
  return { cell: Math.max(20, Number(g.cell) || 60), gap: Math.max(0, Number(g.gap ?? 10)) };
}
const px = (n, u) => n * (u.cell + u.gap);                       // 칸 좌표 → px(왼쪽 위)
const size = (n, u) => n * u.cell + Math.max(0, n - 1) * u.gap;   // 칸 수 → px(크기)
function widgetTitle(w) {
  if (w.type === "builtin:timeline") return "타임라인";
  const p = renderers.find(r => r.id === w.type);
  return p ? p.name : w.type;
}
function applyRect(card, w, u) {
  card.style.left = px(w.x || 0, u) + "px"; card.style.top = px(w.y || 0, u) + "px";
  card.style.width = size(Math.max(1, w.w || 1), u) + "px"; card.style.height = size(Math.max(1, w.h || 1), u) + "px";
}
function fitBoard(board, view, u) {
  let mx = 20, my = 6;
  for (const w of view.widgets) { mx = Math.max(mx, (w.x || 0) + (w.w || 1)); my = Math.max(my, (w.y || 0) + (w.h || 1)); }
  board.style.width = px(mx + 1, u) + "px"; board.style.height = px(my + 1, u) + "px";
}
/** 설정 파일에 x·y가 없는(옛 형식) 뷰는 기본 배치로 되돌린다. */
function migrateView(view) {
  if (view.widgets.every(w => Number.isInteger(w.x) && Number.isInteger(w.y))) return false;
  const old = view.widgets;
  view.widgets = defaultWidgets().map(w => { const o = old.find(x => x.type === w.type); if (o && o.settings) w.settings = o.settings; return w; });
  return true;
}

/** 편집 모드: 머리띠를 끌면 이동, 오른쪽 아래 손잡이를 끌면 크기. 칸 단위로 스냅하고 놓으면 저장한다(다시 그리지 않는다).
 * 포인터 캡처를 써서 iframe 위에서 놓아도 끝을 놓치지 않는다. 끄는 동안 body에 dragging을 달아 iframe이 포인터를 못 받게 한다. */
function attachEdit(card, head, w, view) {
  const handle = document.createElement("div"); handle.className = "handle"; handle.title = "끌어서 크기 조절";
  card.appendChild(handle);
  const drag = (target, e, apply) => {
    e.preventDefault();
    const u = gridUnits(), start = { x: e.clientX, y: e.clientY, wx: w.x || 0, wy: w.y || 0, ww: w.w || 1, wh: w.h || 1 };
    const move = ev => { const dx = Math.round((ev.clientX - start.x) / (u.cell + u.gap)), dy = Math.round((ev.clientY - start.y) / (u.cell + u.gap)); apply(start, dx, dy); applyRect(card, w, u); };
    let done = false;
    const up = async () => {
      if (done) return; done = true;
      target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); target.removeEventListener("pointercancel", up); target.removeEventListener("lostpointercapture", up);
      try { target.releasePointerCapture(e.pointerId); } catch (_) {}
      card.classList.remove("resizing"); document.body.classList.remove("dragging");
      fitBoard(card.parentElement, view, u); await saveSettings();
    };
    try { target.setPointerCapture(e.pointerId); } catch (_) {}
    card.classList.add("resizing"); document.body.classList.add("dragging");
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up); target.addEventListener("pointercancel", up); target.addEventListener("lostpointercapture", up);
  };
  handle.addEventListener("pointerdown", e => drag(handle, e, (st, dx, dy) => { w.w = Math.max(1, st.ww + dx); w.h = Math.max(1, st.wh + dy); }));
  head.addEventListener("pointerdown", e => { if (e.target.closest("button") || e.button !== 0) return; drag(head, e, (st, dx, dy) => { w.x = Math.max(0, st.wx + dx); w.y = Math.max(0, st.wy + dy); }); });
  head.classList.add("movable");
}

async function load() {
  const main = $("#main");
  const { start, end } = dayRange(dayOffset);
  range = { start: start.toISOString(), end: end.toISOString() };
  $("#date").textContent = `${fmtDate(start)} ${fmtTime(start)} ~ ${fmtTime(end)}`;
  renderTabs();
  const view = currentView();
  if (!view) { main.innerHTML = '<div class="empty">뷰가 없습니다. 편집에서 뷰를 추가하세요.</div>'; return; }
  const u = gridUnits();
  const grid = document.createElement("div"); grid.className = "board" + (mobile ? " column" : "");
  if (!mobile) fitBoard(grid, view, u);
  const order = mobile ? mobileOrder(view) : view.widgets;
  order.forEach(w => {
    const i = view.widgets.indexOf(w);
    const card = document.createElement("section"); card.className = "widget";
    if (mobile) card.style.height = size(Math.max(1, w.h || 1), u) + "px"; else applyRect(card, w, u);
    const head = document.createElement("div"); head.className = "whead";
    head.innerHTML = `<span class="wtitle">${esc(widgetTitle(w))}</span>` + (editing ? `<span class="wtools">
        ${mobile ? '<button data-act="up" title="위로">↑</button><button data-act="down" title="아래로">↓</button><button data-act="shorter" title="낮게">−</button><button data-act="taller" title="높게">+</button>' : ""}
        ${w.type.startsWith("builtin:") ? "" : '<button data-act="settings" title="설정">⚙</button>'}
        <button data-act="remove" title="지우기">✕</button></span>` : "");
    head.querySelectorAll("[data-act]").forEach(b => b.onclick = () => widgetAction(view, i, b.dataset.act));
    const body = document.createElement("div"); body.className = "wbody";
    card.append(head, body); grid.appendChild(card);
    if (editing && !mobile) attachEdit(card, head, w, view);
    if (w.type === "builtin:timeline") { body.classList.add("timeline"); renderTimeline(body, { start, end }); return; }
    const p = renderers.find(r => r.id === w.type);
    if (!p) { body.innerHTML = `<div class="empty">설치되지 않은 위젯: ${esc(w.type)}</div>`; return; }
    if (!p.enabled) { body.innerHTML = `<div class="empty">꺼진 플러그인: ${esc(p.id)}</div>`; return; }
    const f = document.createElement("iframe"); f.src = p.entry; f.dataset.plugin = p.id; f.dataset.widget = w.id;
    f.addEventListener("load", () => {
      f.contentWindow.postMessage({ lifelog: "theme", theme: effectiveTheme() }, "*");
      f.contentWindow.postMessage({ lifelog: "widget", widget: { id: w.id, w: w.w || 1, h: w.h || 1, settings: w.settings || {} } }, "*");
      f.contentWindow.postMessage({ lifelog: "range", range }, "*");
    });
    body.appendChild(f);
  });
  main.innerHTML = ""; main.appendChild(grid);
  if (editing) {
    const add = document.createElement("div"); add.className = "addbar";
    const opts = [{ id: "builtin:timeline", name: "타임라인 (내장)", ok: true }, ...renderers.filter(r => r.slot === "card").map(r => ({ id: r.id, ok: r.supported !== false,
      name: `${r.name} (${r.id})` + (r.supported === false ? ` · ${platformName(platform)} 미지원` : "") + ((r.requires || []).length ? ` · 필요: ${r.requires.map(x => x.name).join(", ")}` : "") }))];
    add.innerHTML = `<select id="addType">${opts.map(o => `<option value="${esc(o.id)}" ${o.ok ? "" : "disabled"}>${esc(o.name)}</option>`).join("")}</select><button id="addWidget">위젯 추가</button>
      <span class="spacer"></span><button id="resetView" title="위젯 목록과 크기를 기본값으로">뷰 초기화</button><button id="renameView">뷰 이름</button><button id="deleteView">뷰 지우기</button>`;
    main.appendChild(add);
    add.querySelector("#addWidget").onclick = async () => {
      const type = add.querySelector("#addType").value;
      const bottom = view.widgets.reduce((m, x) => Math.max(m, (x.y || 0) + (x.h || 1)), 0);   // 맨 아래 빈 줄에
      view.widgets.push({ id: uid(), type, x: 0, y: bottom, w: type === "builtin:timeline" ? 20 : 5, h: mobile ? 4 : 5, settings: {} });
      await saveSettings(); load();
    };
    add.querySelector("#resetView").onclick = async () => {
      if (!confirm(`뷰 "${view.name}"의 위젯을 기본 구성으로 되돌릴까요?`)) return;
      view.widgets = mobile ? JSON.parse(JSON.stringify((DEFAULT_MOBILE_VIEWS.find(v => v.id === view.id) || DEFAULT_MOBILE_VIEWS[0]).widgets)) : view.id === "network" ? NETWORK_WIDGETS.map(w => Object.assign({ id: uid(), settings: {} }, w)) : defaultWidgets();
      await saveSettings(); load();
    };
    add.querySelector("#renameView").onclick = async () => { const n = prompt("뷰 이름", view.name); if (n) { view.name = n; await saveSettings(); load(); } };
    add.querySelector("#deleteView").onclick = async () => {
      if (!confirm(`뷰 "${view.name}"을 지울까요?`)) return;
      settings.views = settings.views.filter(v => v !== view); viewId = settings.views[0]?.id ?? null; await saveSettings(); load();
    };
  }
  window.lifelog._broadcastRange(range);
}

async function widgetAction(view, i, act) {
  const w = view.widgets[i];
  if (act === "remove") view.widgets.splice(i, 1);
  else if (act === "left" && i > 0) { view.widgets.splice(i, 1); view.widgets.splice(i - 1, 0, w); }
  else if (act === "right" && i < view.widgets.length - 1) { view.widgets.splice(i, 1); view.widgets.splice(i + 1, 0, w); }
  else if (act === "settings") { if (!await editWidgetSettings(w)) return; }
  else if (act === "up" || act === "down") {   // 한 줄 배치: 순서를 바꾸고 y를 위에서부터 다시 매긴다
    const list = mobileOrder(view), k = list.indexOf(w), j = act === "up" ? k - 1 : k + 1;
    if (j < 0 || j >= list.length) return;
    [list[k], list[j]] = [list[j], list[k]]; restack(list);
  }
  else if (act === "shorter" || act === "taller") { w.h = Math.max(2, Math.min(12, (w.h || 4) + (act === "taller" ? 1 : -1))); restack(mobileOrder(view)); }
  await saveSettings(); load();
}
/** 한 줄 배치의 순서: 위에서 아래(y), 같은 줄이면 왼쪽부터(x). */
function mobileOrder(view) { return view.widgets.slice().sort((a, b) => (a.y || 0) - (b.y || 0) || (a.x || 0) - (b.x || 0)); }
function restack(list) { let y = 0; for (const w of list) { w.x = 0; w.y = y; y += w.h || 1; } }
// 휴대폰에서는 머리줄 버튼을 아이콘으로(이름은 title로)
const SHORT = { "플러그인": "🧩", "설정": "⚙", "타임라인": "↩", "새로고침": "⟳", "편집": "✎" };
const setLabel = (el, t) => { if (!el) return; el.textContent = mobile ? (SHORT[t] || t) : t; el.title = t; };
const platformName = p => ({ windows: "Windows", android: "휴대폰", macos: "macOS", linux: "Linux", ios: "iOS" }[p] || p);

// ---- 색 단계 편집기: list 설정 중 key가 colors인 것("시간:색" 목록). 단계마다 시간·색·투명도, 미리보기 띠 ----
const cvx = document.createElement("canvas").getContext("2d");
/** CSS 색 → { hex: "#rrggbb", a: 0~1 }. 모르면 null */
function cssColor(str) {
  const s = String(str || "").trim(); if (!s) return null;
  if (s.toLowerCase() === "transparent") return { hex: "#000000", a: 0 };
  const probe = v => { cvx.fillStyle = v; cvx.fillStyle = s; return cvx.fillStyle; };
  const x = probe("#010203"); if (x !== probe("#040506")) return null;
  let m = /^#([0-9a-f]{6})$/i.exec(x); if (m) return { hex: "#" + m[1].toLowerCase(), a: 1 };
  m = /^rgba?\(([^)]+)\)$/.exec(x); if (!m) return null;
  const p = m[1].split(",").map(Number); return { hex: "#" + p.slice(0, 3).map(v => v.toString(16).padStart(2, "0")).join(""), a: p.length > 3 ? p[3] : 1 };
}
const stopCss = (hex, a) => a >= 1 ? hex : a <= 0 ? "transparent" : `rgba(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)}, ${Math.round(a * 100) / 100})`;
function stopRow(h, c) {
  return `<span class="stop"><input type="number" class="sh" step="0.25" min="0" value="${esc(h)}" title="시간"><span class="mute">시간</span>` +
    `<input type="color" class="sc" value="${esc(c.hex)}" title="색"><input type="range" class="sa" min="0" max="100" value="${Math.round(c.a * 100)}" title="불투명도(0 = 투명)"><span class="sav mute">${Math.round(c.a * 100)}%</span><button type="button" class="sx" title="지우기">✕</button></span>`;
}
function stopsEditor(key, list) {
  const rows = (Array.isArray(list) ? list : []).map(s => { const i = String(s).indexOf(":"); if (i < 0) return null; const m = /^\s*([\d.]+)\s*(h|시간|m|분)?\s*$/i.exec(String(s).slice(0, i)); const c = cssColor(String(s).slice(i + 1)); return m && c ? { h: /^(m|분)$/i.test(m[2] || "") ? +(Number(m[1]) / 60).toFixed(2) : Number(m[1]), c } : null; }).filter(Boolean);
  return `<span class="stops" data-stops="${esc(key)}"><span class="stoprows">${rows.map(r => stopRow(r.h, r.c)).join("")}</span><span class="stopbar"><span class="stopprev"></span></span><span><button type="button" class="sadd">단계 추가</button> <span class="mute">시간 사이는 위젯이 보간한다. 투명은 불투명도 0</span></span></span>`;
}
function wireStops(form) {
  for (const box of form.querySelectorAll("[data-stops]")) {
    const preview = () => {
      const rows = readStops(box).map(s => { const i = s.indexOf(":"); return { h: Number(s.slice(0, i)), c: s.slice(i + 1) }; }).sort((a, b) => a.h - b.h);
      const max = Math.max(0.5, ...rows.map(r => r.h));
      box.querySelector(".stopprev").style.background = rows.length ? `linear-gradient(to right, ${rows.map(r => `${r.c} ${(r.h / max * 100).toFixed(1)}%`).join(", ")})` : "none";
      box.querySelectorAll(".stop").forEach(r => r.querySelector(".sav").textContent = r.querySelector(".sa").value + "%");
    };
    box.addEventListener("input", preview);
    box.addEventListener("click", e => {
      if (e.target.classList.contains("sx")) { e.target.closest(".stop").remove(); preview(); }
      if (e.target.classList.contains("sadd")) {
        const hs = [...box.querySelectorAll(".sh")].map(x => Number(x.value) || 0);
        box.querySelector(".stoprows").insertAdjacentHTML("beforeend", stopRow(hs.length ? Math.max(...hs) + 1 : 0, { hex: "#39d353", a: 1 })); preview();
      }
    });
    preview();
  }
}
const readStops = box => [...box.querySelectorAll(".stop")].map(r => `${Number(r.querySelector(".sh").value) || 0}:${stopCss(r.querySelector(".sc").value, Number(r.querySelector(".sa").value) / 100)}`);

/** 매니페스트 settings 선언으로 작은 폼을 만든다. key가 devices인 list는 등록부의 기기 목록에서 고른다. 저장했으면 true. */
async function editWidgetSettings(w) {
  const p = renderers.find(r => r.id === w.type);
  const decl = (p && Array.isArray(p.decl)) ? p.decl : [];
  if (decl.length === 0) { alert("이 위젯은 설정이 없습니다."); return false; }
  if (!registry) { try { registry = await lifelog.registry(); } catch (e) { registry = { devices: [] }; } }
  const dlg = $("#dlg"); const form = $("#dlgForm");
  form.innerHTML = decl.map(d => {
    const v = (w.settings || {})[d.key] ?? d.default ?? "";
    let input;
    if (d.type === "boolean") input = `<input type="checkbox" name="${esc(d.key)}" ${v ? "checked" : ""}>`;
    else if (d.type === "enum") input = `<select name="${esc(d.key)}">${(d.options || []).map(o => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    else if (d.type === "number") input = `<input type="number" name="${esc(d.key)}" value="${esc(v)}">`;
    else if (d.type === "list" && d.key === "devices") {
      const sel = new Set(Array.isArray(v) ? v : []);
      const devs = (registry.devices || []).filter(x => !x.revoked);
      input = `<span class="checks" data-list="${esc(d.key)}">${devs.map(x => `<label class="chk"><input type="checkbox" value="${esc(x.id)}" ${sel.has(x.id) ? "checked" : ""}> ${esc(x.name)} <span class="mute">${esc(x.id)}</span></label>`).join("")}<span class="mute">(아무것도 안 고르면 전부)</span></span>`;
    }
    else if (d.type === "list" && d.key === "categories") {   // 분류 규칙 이름(상위 이름 포함)에서 고른다
      const sel = new Set(Array.isArray(v) ? v : []), names = new Set();
      for (const r of [...(settings.categories || []), ...(settings.phone_categories || [])]) { if (!r || !r.name) continue; const parts = r.name.split("/"); for (let i = 1; i <= parts.length; i++) names.add(parts.slice(0, i).join("/")); }
      for (const x of sel) names.add(x);   // 지금 규칙에 없는 이름도 고른 그대로 보인다
      input = `<span class="checks" data-list="${esc(d.key)}">${[...names].sort((a, b) => a.localeCompare(b, "ko")).map(x => `<label class="chk"><input type="checkbox" value="${esc(x)}" ${sel.has(x) ? "checked" : ""}> ${"&nbsp;".repeat((x.split("/").length - 1) * 3)}${esc(x.split("/").pop())}</label>`).join("") || '<span class="mute">분류 규칙이 없습니다</span>'}<span class="mute">(아무것도 안 고르면 미분류를 뺀 전부. 상위를 고르면 하위 포함)</span></span>`;
    }
    else if (d.type === "list" && d.key === "colors") input = stopsEditor(d.key, v);
    else if (d.type === "list") input = `<input name="${esc(d.key)}" value="${esc(Array.isArray(v) ? v.join(", ") : v)}" placeholder="쉼표로 구분">`;
    else input = `<input name="${esc(d.key)}" value="${esc(v)}">`;
    return `<label><span>${esc(d.label || d.key)}</span>${input}</label>`;
  }).join("");
  wireStops(form);
  $("#dlgTitle").textContent = `${p.name} 설정`;
  dlg.showModal();
  await new Promise(res => { $("#dlgOk").onclick = () => { dlg.close("ok"); res(); }; $("#dlgCancel").onclick = () => { dlg.close("cancel"); res(); }; });
  if (dlg.returnValue !== "ok") return false;
  const out = {};
  for (const d of decl) {
    const el = form.querySelector(`[name="${CSS.escape(d.key)}"]`);
    if (d.type === "list" && d.key === "colors") out[d.key] = readStops(form.querySelector(`[data-stops="${CSS.escape(d.key)}"]`)).sort((a, b) => parseFloat(a) - parseFloat(b));
    else if (d.type === "list" && (d.key === "devices" || d.key === "categories")) out[d.key] = [...form.querySelectorAll(`[data-list="${CSS.escape(d.key)}"] input:checked`)].map(x => x.value);
    else if (d.type === "boolean") out[d.key] = el.checked;
    else if (d.type === "number") out[d.key] = Number(el.value);
    else if (d.type === "list") out[d.key] = el.value.split(/,(?![^()]*\))/).map(s => s.trim()).filter(Boolean);   // rgba(…) 같은 괄호 안 쉼표는 나누지 않는다
    else out[d.key] = el.value;
  }
  w.settings = out;
  return true;
}

function renderTabs() {
  const tabs = $("#tabs");
  tabs.innerHTML = settings.views.map(v => `<button class="tab ${v.id === viewId ? "on" : ""}" data-view="${esc(v.id)}">${esc(v.name)}</button>`).join("") +
    (editing ? '<button class="tab add" id="addView">+ 뷰</button>' : "");
  tabs.querySelectorAll("[data-view]").forEach(b => {
    b.onclick = () => { viewId = b.dataset.view; showPage("main"); load(); };
    b.title = "더블클릭: 이름 바꾸기";
    b.ondblclick = async () => { const v = settings.views.find(x => x.id === b.dataset.view); const n = prompt("뷰 이름", v.name); if (n && n !== v.name) { v.name = n; await saveSettings(); renderTabs(); } };
  });
  if (embed) {
    const sig = JSON.stringify([settings.views.map(v => [v.id, v.name]), viewId]);
    if (sig !== renderTabs.sent) { renderTabs.sent = sig; lifelog._hostViews(settings.views.map(v => ({ id: v.id, name: v.name })), viewId); }
  }
  const add = $("#addView");
  if (add) add.onclick = async () => { const n = prompt("새 뷰 이름"); if (!n) return; const v = { id: uid(), name: n, widgets: [] }; settings.views.push(v); viewId = v.id; await saveSettings(); load(); };
}

// ---- 설정 자동 저장: 입력이 멈추면 저장하고, 설정 페이지를 떠날 때는 바로 저장한다 ----
let pendingSave = null, pendingTimer = null;
function scheduleSave(fn, delay = 800) {
  pendingSave = fn; clearTimeout(pendingTimer);
  pendingTimer = setTimeout(flushSave, delay);
}
async function flushSave() {
  clearTimeout(pendingTimer); const fn = pendingSave; pendingSave = null;
  if (fn) { try { await fn(); } catch (e) { alert("설정 저장 실패: " + e.message); } }
}

// ---- 설정 페이지: 사이드바(일반 / 분류 규칙 › 컴퓨터·휴대폰) + 내용 ----
let settingsSection = "general";
const SECTIONS = [
  { id: "general", group: "일반", name: "일반" },
  { id: "rules-pc", group: "분류 규칙", name: "컴퓨터" },
  { id: "rules-ph", group: "분류 규칙", name: "휴대폰" },
  { id: "regex", group: "분류 규칙", name: "정규식 도구" },
];
const escRe = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** 미분류 항목의 이름 제안. 창 제목 끝의 앱 이름("… - Visual Studio Code")을 우선, 없으면 exe·package 이름을 다듬는다. */
const KNOWN_NAMES = { msedge: "Edge", chrome: "Chrome", firefox: "Firefox", explorer: "탐색기", lockapp: "잠금 화면", powerpnt: "PowerPoint", winword: "Word", excel: "Excel",
  code: "VS Code", devenv: "Visual Studio", claude: "Claude", discord: "Discord", kakaotalk: "카카오톡", obsidian: "Obsidian", notepad: "메모장", windowsterminal: "터미널",
  youtube: "YouTube", instagram: "Instagram", youtubemusic: "YouTube Music", music: "YouTube Music", talk: "카카오톡", gm: "Gmail" };
function suggestName(key, titles) {
  for (const t of titles || []) {
    const parts = String(t).split(/ [-–|] /); const last = parts[parts.length - 1].trim();
    if (parts.length > 1 && last.length >= 2 && last.length <= 30 && !/^\d+$/.test(last)) return last;
  }
  const isExe = /\.exe$/i.test(key);
  // package면 뒤에서부터 의미 있는 조각(android·app 같은 일반어는 건너뜀)
  const GENERIC = new Set(["android", "app", "apps", "mobile", "client", "main", "free", "pro", "lite", "kr", "com", "net", "org", "co", "io", "ui", "activity", "phone"]);
  const base = !isExe && String(key).includes(".") ? (String(key).split(".").reverse().find(x => !GENERIC.has(x.toLowerCase())) || String(key).split(".").pop()) : String(key).replace(/\.exe$/i, "");
  const k = base.toLowerCase().replace(/\s+/g, "");
  if (KNOWN_NAMES[k]) return KNOWN_NAMES[k];
  return base.charAt(0).toUpperCase() + base.slice(1);
}

function renderSettingsPage() {
  const page = $("#settingsPage");
  page.innerHTML = `<div class="settings"><nav class="side">${(() => {
    let html = "", g = null;
    for (const s of SECTIONS) { if (s.group !== g) { g = s.group; html += `<div class="group">${esc(g)}</div>`; } html += `<button class="item ${s.id === settingsSection ? "on" : ""}" data-sec="${s.id}">${esc(s.name)}</button>`; }
    return html;
  })()}</nav><div class="content" id="settingsContent"></div></div>`;
  page.querySelectorAll("[data-sec]").forEach(b => b.onclick = () => { settingsSection = b.dataset.sec; renderSettingsPage(); });
  const c = $("#settingsContent");
  if (settingsSection === "general") renderGeneral(c);
  else if (settingsSection === "regex") renderRegexTool(c);
  else renderRules(c, settingsSection === "rules-pc" ? "pc" : "ph");
}

function renderGeneral(c) {
  const u = gridUnits();
  c.innerHTML = `<h2>일반</h2>
    <div class="field"><b>테마</b><p class="mute">대시보드와 위젯의 색. 시스템 따름은 Windows 설정을 따른다.</p>
      <select id="theme"><option value="system">시스템 따름</option><option value="dark">다크</option><option value="light">라이트</option></select></div>
    <div class="field"><b>하루 시작</b><p class="mute">이 시각부터 다음 날 같은 시각까지가 하루. 기기별 설정.</p><input id="dayStart" value="${esc(settings.day_start)}" size="6"></div>
    <div class="field"><b>보드 칸 크기</b><p class="mute">위젯 위치·크기의 단위(px). 바꾸면 모든 위젯이 같은 비율로 커지거나 작아진다.</p>
      <label>한 칸 <input id="gridCell" type="number" value="${u.cell}" size="5"></label> <label>간격 <input id="gridGap" type="number" value="${u.gap}" size="4"></label></div>
    <div class="row"><button id="saveGeneral">저장</button><span id="generalMsg" class="mute"></span></div>`;
  $("#theme").value = settings.theme || "system";
  const saveGeneral = async () => {
    settings.theme = $("#theme").value;
    settings.day_start = $("#dayStart").value.trim() || "04:00";
    settings.grid = { cell: Number($("#gridCell").value) || 60, gap: Number($("#gridGap").value) || 0 };
    await saveSettings(); applyTheme(); load();
    const msg = $("#generalMsg"); if (msg) msg.textContent = "저장됨";
  };
  $("#theme").onchange = () => { settings.theme = $("#theme").value; applyTheme(); scheduleSave(saveGeneral, 100); };
  for (const id of ["dayStart", "gridCell", "gridGap"]) $("#" + id).onchange = () => scheduleSave(saveGeneral, 300);
  $("#saveGeneral").onclick = async () => { pendingSave = null; clearTimeout(pendingTimer); await saveGeneral(); };
}

/** 옛 트리 규칙을 평탄하게: 자식은 부모 바로 앞에(구체적인 것이 먼저 맞게) 꺼내고, 이름은 "부모/자식", 조건은 부모와 합친다. */
function flattenRuleTree(rules, parent = null) {
  const out = [];
  for (const r of rules || []) {
    if (!r) continue;
    const name = parent ? parent.name + "/" + r.name : r.name;
    const both = (a, b) => b || a || "";   // 자식이 같은 칸을 가지면 자식 것(더 구체적)을 쓴다
    const flat = { name, app: parent ? both(parent.app, r.app) : (r.app || ""), title: parent ? both(parent.title, r.title) : (r.title || "") };
    if (r.color) flat.color = r.color;
    if (r.children && r.children.length) out.push(...flattenRuleTree(r.children, flat));
    out.push(flat);
  }
  return out;
}
function migrateRules() {
  const changed = [];
  for (const key of ["categories", "phone_categories"]) {
    const rules = settings[key];
    if (Array.isArray(rules) && rules.some(r => r && r.children && r.children.length)) { settings[key] = flattenRuleTree(rules); changed.push(key); }
  }
  return changed;
}

/** 분류 규칙 편집(평탄 목록) + 최근 7일 미분류 목록(사용 시간 순). kind: pc(app·title) | ph(package) */
function renderRules(c, kind) {
  const key = kind === "pc" ? "categories" : "phone_categories";
  if (!Array.isArray(settings[key])) settings[key] = [];
  const R = () => settings[key];   // 저장하면 settings가 새 객체로 바뀌므로 배열을 붙들지 않는다
  const rules = R();
  const cols = kind === "pc" ? [["app", "app (exe 이름)"], ["title", "title (창 제목)"]] : [["app", "package"]];
  LifeLogSdk.useRuleColors(settings.categories, settings.phone_categories);
  const autoColor = name => LifeLogSdk.categoryColor(name);
  const hex = col => { // hsl(…)·#rgb → #rrggbb (color 입력은 #rrggbb만 받는다)
    const m = /hsl\((\d+) (\d+)% (\d+)%\)/.exec(col);
    if (!m) return /^#[0-9a-f]{6}$/i.test(col) ? col : "#888888";
    const h = Number(m[1]) / 360, sa = Number(m[2]) / 100, l = Number(m[3]) / 100, f = n => { const k = (n + h * 12) % 12, a = sa * Math.min(l, 1 - l); return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))); };
    return "#" + [f(0), f(8), f(4)].map(x => x.toString(16).padStart(2, "0")).join("");
  };
  const parseColor = txt => {
    const t = String(txt).trim();
    let m;
    if ((m = /^#?([0-9a-f]{6})$/i.exec(t))) return "#" + m[1].toLowerCase();
    if ((m = /^#?([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(t))) return "#" + (m[1] + m[1] + m[2] + m[2] + m[3] + m[3]).toLowerCase();
    if ((m = /^(?:rgba?\s*\(\s*)?(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})(?:\s*[,/]\s*[\d.]+%?)?\s*\)?$/i.exec(t))) {
      const c = [m[1], m[2], m[3]].map(x => Math.max(0, Math.min(255, Number(x)))); return "#" + c.map(x => x.toString(16).padStart(2, "0")).join("");
    }
    if ((m = /^hsla?\s*\(\s*(\d+)\s*[, ]\s*(\d+)%\s*[, ]\s*(\d+)%/i.exec(t))) return hex(`hsl(${m[1]} ${m[2]}% ${m[3]}%)`);
    return null;
  };
  c.innerHTML = `<h2>분류 규칙 › ${kind === "pc" ? "컴퓨터" : "휴대폰"}</h2>
    <p class="${rulesShared ? "mute" : "warn"}">${rulesShared ? "규칙은 DB에 저장되어 모든 기기(컴퓨터·휴대폰 대시보드)가 같이 씁니다." : "DB가 0007 이전 버전이라 규칙이 이 기기에만 저장됩니다. 마이그레이션(tools/db/first-run.ps1 -SkipImport -SkipSetup)을 적용하면 DB로 옮겨집니다."}</p>
    <p class="mute">위에서부터 처음 맞는 규칙을 쓴다. 정규식, 대소문자 무시. 이름에 /를 넣으면 계층("개발/VSCode")이고 위젯은 마지막 조각("VSCode")을 보여 준다. 구체적인 규칙을 위에 둔다. <b>색</b>은 비우면 자동이고, "개발/VSCode"처럼 하위 이름은 "개발"의 색을 물려받는다.
    ${kind === "pc" ? "app은 exe 이름(예: ^msedge\\.exe$), title은 창 제목(예: YouTube)." : "app 칸에 package 이름(예: ^com\\.google\\.android\\.youtube$)."}</p>
    <table class="rules"><thead><tr><th style="width:28px"></th><th>이름</th>${cols.map(x => `<th>${esc(x[1])}</th>`).join("")}<th style="width:170px">색</th><th style="width:90px"></th></tr></thead><tbody>
    ${rules.map((r, i) => `<tr><td class="mute">${i + 1}</td><td><input data-p="${i}" data-k="name" value="${esc(r.name)}" placeholder="이름 (계층은 /)"></td>${cols.map(x => `<td><input data-p="${i}" data-k="${x[0]}" value="${esc(r[x[0]] || "")}"></td>`).join("")}<td class="color"><input type="color" data-p="${i}" data-k="color" value="${hex(r.color || autoColor(r.name || "?"))}" title="${r.color ? "지정한 색" : "자동(비우려면 ×)"}"><input class="ctext" data-ctext="${i}" value="${r.color ? esc(r.color) : ""}" placeholder="#hex / r, g, b" title="#rrggbb, rgb(r, g, b), r, g, b 를 붙여 넣을 수 있다"><button data-clear="${i}" title="자동 색으로" ${r.color ? "" : "disabled"}>×</button></td><td class="tools"><button data-up="${i}" title="위로">↑</button><button data-down="${i}" title="아래로">↓</button><button data-del="${i}" title="지우기">✕</button></td></tr>`).join("")}
    </tbody></table>
    <div class="row"><button id="addRule">규칙 추가</button><button id="saveRules">지금 저장</button><span id="ruleMsg" class="mute"></span></div>
    <h3>최근 7일 미분류 <span class="mute">어느 규칙에도 안 맞는 ${kind === "pc" ? "앱" : "package"}, 사용 시간 순. "추가"를 누르면 그 ${kind === "pc" ? "앱" : "package"}으로 새 규칙 줄을 만들고 이름을 제안한다</span></h3>
    <div id="unmatched" class="mute">불러오는 중…</div>`;
  const ruleAt = (list, p) => list[Number(p)];
  const collect = () => c.querySelectorAll("input[data-p]").forEach(el => {
    const r = ruleAt(R(), el.dataset.p); if (!r) return;
    if (el.dataset.k === "color") { if (el.dataset.touched === "1" || r.color) r.color = el.value; }   // 손대지 않은 자동 색은 저장하지 않는다
    else r[el.dataset.k] = el.value;
  });
  c.querySelectorAll('input[type="color"]').forEach(el => el.oninput = () => { el.dataset.touched = "1"; el.nextElementSibling.value = el.value; el.nextElementSibling.nextElementSibling.disabled = false; });
  c.querySelectorAll("input[data-ctext]").forEach(el => {
    const apply = () => {
      const v = parseColor(el.value); if (!v) { el.classList.toggle("bad", el.value.trim() !== ""); return; }
      el.classList.remove("bad"); const picker = el.previousElementSibling; picker.value = v; picker.dataset.touched = "1"; el.value = v; el.nextElementSibling.disabled = false;
    };
    el.onchange = apply; el.onpaste = () => setTimeout(apply, 0);
  });
  // 저장: 편집 중인 배열은 그대로 두고(이름 없는 줄도 유지) 정리한 사본을 파일에 쓴다. 위젯은 파일을 읽으므로 다시 그린다.
  const saveNow = async () => {
    if (!document.body.contains(c)) return;
    collect();
    await lifelog.setRules(kind === "pc" ? "pc" : "phone", pruneRules(R()));   // 규칙은 DB(모든 기기 공유). 0006 DB면 호스트가 로컬 파일에 쓴다
    load();
    const msg = $("#ruleMsg"); if (msg) { msg.textContent = "저장됨 " + new Date().toLocaleTimeString("ko-KR", { hour12: false }); }
    const um = c.querySelector("#unmatched"); if (um) renderUnmatched(um, kind, R, collect);   // 실제 배열을 넘겨야 "추가"가 거기에 들어간다
  };
  c._saveNow = saveNow;
  c.querySelectorAll("input[data-p], input[data-ctext]").forEach(el => el.addEventListener("input", () => scheduleSave(saveNow)));
  c.querySelectorAll("input[data-ctext]").forEach(el => el.addEventListener("change", () => scheduleSave(saveNow, 100)));
  const rerenderAndSave = () => { renderSettingsPage(); scheduleSave(async () => { const cc = $("#settingsContent"); if (cc) await cc._saveNow?.(); }, 200); };
  c.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { collect(); R().splice(Number(b.dataset.del), 1); rerenderAndSave(); });
  c.querySelectorAll("[data-up]").forEach(b => b.onclick = () => { collect(); const i = Number(b.dataset.up); if (i === 0) return; const r = R().splice(i, 1)[0]; R().splice(i - 1, 0, r); rerenderAndSave(); });
  c.querySelectorAll("[data-down]").forEach(b => b.onclick = () => { collect(); const i = Number(b.dataset.down); if (i >= R().length - 1) return; const r = R().splice(i, 1)[0]; R().splice(i + 1, 0, r); rerenderAndSave(); });
  c.querySelectorAll("[data-clear]").forEach(b => b.onclick = () => { collect(); const r = ruleAt(R(), b.dataset.clear); if (r) delete r.color; rerenderAndSave(); });
  $("#addRule").onclick = () => { collect(); R().push({ name: "", app: "", title: "" }); renderSettingsPage(); };
  $("#saveRules").onclick = async () => { pendingSave = null; clearTimeout(pendingTimer); await saveNow(); };
  renderUnmatched(c.querySelector("#unmatched"), kind, R, collect);
}
/** 저장 전 정리: 이름 없는 규칙은 버린다. */
function pruneRules(rules) {
  return (rules || []).filter(r => r && r.name).map(r => { const o = { name: r.name, app: r.app || "", title: r.title || "" }; if (r.color) o.color = r.color; return o; });
}

// ---- 정규식 도구: 최근 데이터에 app·title 정규식을 대 보고 일치한 부분을 강조한다 ----
const regexTool = { kind: "pc", app: "", title: "", days: 7, only: true, cache: {} };
function renderRegexTool(c) {
  const t = regexTool;
  c.innerHTML = `<h2>정규식 도구</h2>
    <p class="mute">규칙에 쓸 정규식을 실제 기록(최근 ${t.days}일, 시간 많은 순)에 대 봅니다. 대소문자 무시. 일치한 부분은 <mark>이렇게</mark> 표시됩니다. app·title 둘 다 적으면 둘 다 맞아야 일치입니다.</p>
    <div class="row">
      <label>대상 <select id="rxKind"><option value="pc" ${t.kind === "pc" ? "selected" : ""}>컴퓨터(창)</option><option value="ph" ${t.kind === "ph" ? "selected" : ""}>휴대폰(package)</option></select></label>
      <label>기간 <select id="rxDays">${[1, 7, 30].map(d => `<option value="${d}" ${t.days === d ? "selected" : ""}>${d}일</option>`).join("")}</select></label>
      <label><input type="checkbox" id="rxOnly" ${t.only ? "checked" : ""}> 일치한 것만</label>
    </div>
    <div class="row rx">
      <label>${t.kind === "pc" ? "app (exe 이름)" : "package"} <input id="rxApp" value="${esc(t.app)}" placeholder="예: ^msedge\\.exe$" size="28"></label>
      ${t.kind === "pc" ? `<label>title (창 제목) <input id="rxTitle" value="${esc(t.title)}" placeholder="예: YouTube|유튜브" size="40"></label>` : ""}
      <button id="rxAdd" title="이 조건으로 규칙 줄을 만든다">규칙으로 추가</button>
    </div>
    <div id="rxMsg" class="mute"></div>
    <div id="rxOut" class="mute">불러오는 중…</div>`;
  const rerun = () => { t.app = $("#rxApp").value; t.title = $("#rxTitle") ? $("#rxTitle").value : ""; runRegexTool(c); };
  $("#rxKind").onchange = () => { t.kind = $("#rxKind").value; t.title = ""; renderRegexTool(c); };
  $("#rxDays").onchange = () => { t.days = Number($("#rxDays").value); runRegexTool(c, true); };
  $("#rxOnly").onchange = () => { t.only = $("#rxOnly").checked; runRegexTool(c); };
  let timer = null;
  for (const id of ["rxApp", "rxTitle"]) { const el = $("#" + id); if (el) el.oninput = () => { clearTimeout(timer); timer = setTimeout(rerun, 250); }; }
  $("#rxAdd").onclick = () => {
    const key = t.kind === "pc" ? "categories" : "phone_categories";
    if (!Array.isArray(settings[key])) settings[key] = [];
    settings[key].push({ name: "", app: $("#rxApp").value, title: $("#rxTitle") ? $("#rxTitle").value : "" });
    settingsSection = t.kind === "pc" ? "rules-pc" : "rules-ph"; renderSettingsPage();
    const inputs = $("#settingsContent").querySelectorAll('input[data-k="name"]'); inputs[inputs.length - 1]?.focus();
  };
  runRegexTool(c, true);
}
async function runRegexTool(c, reload = false) {
  const t = regexTool, out = c.querySelector("#rxOut"), msg = c.querySelector("#rxMsg");
  if (!out) return;
  const cacheKey = t.kind + ":" + t.days;
  if (reload || !t.cache[cacheKey]) {
    out.className = "mute"; out.textContent = "불러오는 중…";
    const end = new Date(), start = new Date(end.getTime() - t.days * 86400e3), range = { start: start.toISOString(), end: end.toISOString() };
    try {
      const res = t.kind === "pc"
        ? await lifelog.query("win-window/window", { range, group: { by: ["app", "title"] }, agg: { dur: "duration" }, limit: 20000 })
        : await lifelog.query("phone-app/app", { range, group: { by: ["package"] }, agg: { dur: "duration" }, limit: 20000 });
      t.cache[cacheKey] = res.rows.map(r => ({ app: r.app ?? r.package ?? "", title: r.title ?? "", sec: Number(r.dur) || 0 })).sort((a, b) => b.sec - a.sec);
    } catch (e) { out.textContent = "불러오지 못함: " + e.message; return; }
  }
  const rows = t.cache[cacheKey];
  let reApp = null, reTitle = null;
  try { if (t.app) reApp = new RegExp(t.app, "gi"); if (t.title) reTitle = new RegExp(t.title, "gi"); }
  catch (e) { msg.innerHTML = `<span class="warn">정규식 오류: ${esc(e.message)}</span>`; return; }
  const hl = (text, re) => {   // 일치한 부분을 <mark>로
    if (!re) return esc(text);
    let outs = "", last = 0, m; re.lastIndex = 0;
    while ((m = re.exec(text)) !== null) { if (m[0] === "") { re.lastIndex++; continue; } outs += esc(text.slice(last, m.index)) + "<mark>" + esc(m[0]) + "</mark>"; last = m.index + m[0].length; }
    return outs + esc(text.slice(last));
  };
  const test = (re, text) => { if (!re) return true; re.lastIndex = 0; return re.test(text); };
  let matched = 0, matchedSec = 0, totalSec = 0;
  const lines = [];
  for (const r of rows) {
    const ok = (reApp || reTitle) ? (test(reApp, r.app) && test(reTitle, r.title)) : false;
    totalSec += r.sec; if (ok) { matched++; matchedSec += r.sec; }
    if (t.only && !ok) continue;
    if (lines.length < 200) lines.push(`<tr class="${ok ? "ok" : ""}"><td class="num">${ok ? "✓" : ""}</td><td><code>${hl(r.app, reApp)}</code></td>${t.kind === "pc" ? `<td class="ex">${hl(r.title, reTitle)}</td>` : ""}<td class="num">${LifeLogSdk.fmtDur(r.sec * 1000)}</td></tr>`);
  }
  msg.innerHTML = (reApp || reTitle) ? `일치 <b>${matched}</b> / ${rows.length}개 · ${LifeLogSdk.fmtDur(matchedSec * 1000)} / ${LifeLogSdk.fmtDur(totalSec * 1000)}` : `정규식을 입력하면 ${rows.length}개 항목에 대 봅니다.`;
  out.className = "";
  out.innerHTML = lines.length ? `<table class="unmatched rx"><thead><tr><th></th><th>${t.kind === "pc" ? "앱" : "package"}</th>${t.kind === "pc" ? "<th>창 제목</th>" : ""}<th>시간</th></tr></thead><tbody>${lines.join("")}</tbody></table>${rows.length > 200 && !t.only ? '<div class="mute">… 200개까지만 표시</div>' : ""}`
    : `<div class="mute">${t.only && (reApp || reTitle) ? "일치하는 항목이 없습니다." : ""}</div>`;
}

async function renderUnmatched(el, kind, R, collect) {
  const rules = R();
  const end = new Date(), start = new Date(end.getTime() - 7 * 86400e3);
  const range = { start: start.toISOString(), end: end.toISOString() };
  try {
    let items;
    if (kind === "pc") {
      const res = await lifelog.query("win-window/window", { range, group: { by: ["app", "title"] }, agg: { dur: "duration" }, limit: 20000 });
      const byApp = new Map();
      for (const r of res.rows) {
        if (LifeLogSdk.matchRules(rules, r.app, r.title)) continue;
        const a = byApp.get(r.app) || { key: r.app, sec: 0, titles: [] };
        a.sec += Number(r.dur) || 0; a.titles.push({ t: r.title, sec: Number(r.dur) || 0 }); byApp.set(r.app, a);
      }
      items = [...byApp.values()].map(a => ({ key: a.key, sec: a.sec, sub: a.titles.sort((x, y) => y.sec - x.sec).slice(0, 3).map(x => x.t).filter(Boolean) }));
    } else {
      const res = await lifelog.query("phone-app/app", { range, group: { by: ["package"] }, agg: { dur: "duration" }, limit: 20000 });
      items = res.rows.filter(r => !LifeLogSdk.matchRules(rules, r.package, "")).map(r => ({ key: r.package, sec: Number(r.dur) || 0, sub: [] }));
    }
    items.sort((a, b) => b.sec - a.sec);
    if (items.length === 0) { el.textContent = "미분류가 없습니다."; return; }
    el.className = "";
    el.innerHTML = `<table class="unmatched"><thead><tr><th>${kind === "pc" ? "앱" : "package"}</th><th>시간</th><th>예(창 제목)</th><th></th></tr></thead><tbody>` +
      items.slice(0, 40).map(it => `<tr><td><code>${esc(it.key)}</code></td><td class="num">${LifeLogSdk.fmtDur(it.sec * 1000)}</td><td class="mute ex">${it.sub.map(esc).join("<br>")}</td><td><button data-mk="${esc(it.key)}" data-name="${esc(suggestName(it.key, it.sub))}">추가</button></td></tr>`).join("") + "</tbody></table>" +
      (items.length > 40 ? `<div class="mute">… ${items.length - 40}개 더</div>` : "");
    el.querySelectorAll("[data-mk]").forEach(b => b.onclick = () => {
      // 입력 중인 값을 잃지 않게 모으고, 그 앱으로 규칙 줄 추가
      collect();
      R().push({ name: b.dataset.name || "", app: "^" + escRe(b.dataset.mk) + "$", title: "" });
      renderSettingsPage();
      scheduleSave(async () => { const cc = $("#settingsContent"); if (cc) await cc._saveNow?.(); }, 200);
      const inputs = $("#settingsContent").querySelectorAll('input[data-k="name"]'); const last = inputs[inputs.length - 1]; if (last) { last.focus(); last.select(); }
    });
  } catch (e) { el.textContent = "불러오지 못함: " + e.message; }
}

async function init() {
  try {
    try { platform = await lifelog.platform(); } catch (_) { platform = "windows"; }
    mobile = platform === "android" || params.get("mobile") === "1" || matchMedia("(max-width: 700px)").matches;   // ?mobile=1은 개발용
    document.body.classList.toggle("mobile", mobile);
    document.body.classList.toggle("embed", embed);
    for (const [id, t] of [["edit", "편집"], ["settings", "설정"], ["plugins", "플러그인"], ["reload", "새로고침"]]) setLabel($("#" + id), t);
    settings = Object.assign(settings, await lifelog.settings());
    rulesShared = !!settings.rules_shared;
    if (!Array.isArray(settings.views) || settings.views.length === 0) { settings.views = JSON.parse(JSON.stringify(platform === "android" ? DEFAULT_MOBILE_VIEWS : DEFAULT_VIEWS)); await saveSettings(); }
    if (!Array.isArray(settings.categories)) settings.categories = [];
    if (!Array.isArray(settings.phone_categories)) settings.phone_categories = [];
    if (settings.views.map(migrateView).some(Boolean)) await saveSettings();
    for (const key of migrateRules()) await lifelog.setRules(key === "categories" ? "pc" : "phone", pruneRules(settings[key]));   // 옛 자식 규칙을 부모 밖으로
    if (!settings.grid || !settings.grid.cell) { settings.grid = { cell: 60, gap: 10 }; await saveSettings(); }
    applyTheme();
    outputs = await lifelog.outputs();
    renderers = await lifelog.renderers();
    if (!settings.sb_views && platform === "windows" && renderers.some(r => r.id === "sb-doable")) {
      settings.views = JSON.parse(JSON.stringify(SB_VIEWS)).filter(v => !settings.views.some(x => x.id === v.id)).concat(settings.views);
      settings.sb_views = 1; await saveSettings();
    }
    if (!viewId) viewId = (settings.views.find(v => v.id === params.get("view")) || settings.views[0]).id;
    $("#info").textContent = `기기 ${await lifelog.device()}` + (mobile ? "" : ` · ${platformName(platform)}`);
    await load();
  } catch (e) {
    $("#main").innerHTML = `<div class="empty">오류: ${esc(e.message)}</div>`;
  }
}

function showPage(name) {
  if (name === "main" && $("#main").style.display !== "none") return;
  if (name !== "settingsPage" && pendingSave) flushSave();   // 설정 페이지를 떠나면 미뤄 둔 저장을 바로
  for (const id of ["main", "pluginsPage", "settingsPage"]) $("#" + id).style.display = id === name ? "block" : "none";
  setLabel($("#plugins"), name === "pluginsPage" ? "타임라인" : "플러그인");
  setLabel($("#settings"), name === "settingsPage" ? "타임라인" : "설정");
  if (name === "settingsPage") renderSettingsPage();
}
window.showPage = showPage;

$("#prev").onclick = () => { dayOffset--; showPage("main"); load(); };
$("#next").onclick = () => { dayOffset++; showPage("main"); load(); };
$("#today").onclick = () => { dayOffset = 0; showPage("main"); load(); };
$("#reload").onclick = init;
$("#edit").onclick = () => { editing = !editing; $("#edit").classList.toggle("on", editing); document.body.classList.toggle("editing", editing); load(); };
window.addEventListener("lifelog:selectView", e => { if (e.detail === viewId && $("#main").style.display !== "none") return; if (!settings.views.some(v => v.id === e.detail)) return; viewId = e.detail; showPage("main"); load(); });
window.addEventListener("lifelog:refresh", () => { if ($("#main").style.display !== "none") load(); });
$("#settings").onclick = () => showPage($("#settingsPage").style.display === "block" ? "main" : "settingsPage");
window.addEventListener("message", e => {
  const d = e.data;
  if (d && d.lifelog === "navigate" && d.range) { /* 플러그인의 이동 요청은 아직 날짜 단위만 */ }
});
if (!matchMedia("(pointer: coarse)").matches) document.addEventListener("mousemove", e => {
  const t = e.target.closest("[data-tip]"); const tip = $("#tip");
  if (!t) { tip.style.display = "none"; return; }
  tip.textContent = t.dataset.tip; tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 12, innerWidth - 300) + "px"; tip.style.top = (e.clientY + 14) + "px";
});
if (params.get("page") === "settings") { settingsSection = params.get("sec") || "general"; setTimeout(() => showPage("settingsPage"), 1500); }   // 개발용
init();
