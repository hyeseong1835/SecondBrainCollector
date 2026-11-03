// 네트워크 수집기(안드로이드). network 소스가 주는 이벤트를 망별 구간으로 나눈다. 원본(raw) 이벤트 테이블은 두지 않는다.
//   wifi     와이파이 SSID 하나에 붙어 있던 동안. 같은 SSID 안의 AP 이동은 roams만 올린다
//   cellular 모바일 데이터 연결. 망 종류(rat: lte·nr_nsa…)가 바뀌어 1분 넘게 유지되면 새 구간
//   default  실제 인터넷 경로(wifi·cellular·vpn·none…). 빈틈없이 이어지고 none이 오프라인
//   signal   신호 단계(0~4)가 같은 동안. 단계가 바뀌어 1분 넘게 유지되면 새 구간, 그보다 짧은 흔들림은 지금 구간의 dbm_min/max에만 들어간다
// 수집기가 꺼져 있던 동안은 알 수 없으므로, 다시 켜지면 그 때부터 새 구간을 연다.
const HOLD = 60e3;
const wifi = {};      // network → { id, ssid, bssid, roams }
const cell = {};      // network → { id, rat, pending: { rat, since, row } }
const signal = {};    // transport → { id, level, min, max, conn, pending: { level, since, min, max } }
let route = null;     // { id, key }
const t = s => new Date(s).getTime();
const iso = ms => new Date(ms).toISOString();

function wifiRow(ev, keepBssid) {
  const f = ev.frequency_mhz;
  return {
    network: ev.network, ssid: ev.ssid, bssid: keepBssid ? ev.bssid : null, frequency_mhz: f,
    band: !f ? null : f < 2500 ? "2.4GHz" : f < 5925 ? "5GHz" : f < 7200 ? "6GHz" : "60GHz",
    standard: ev.standard, security: ev.security, link_speed_mbps: ev.link_speed_mbps,
    validated: ev.validated, metered: ev.metered, ip: ev.ip, gateway: ev.gateway
  };
}
const cellRow = ev => ({ network: ev.network, carrier: ev.carrier, sim_slot: ev.sim_slot, rat: ev.rat, roaming: ev.roaming, validated: ev.validated, metered: ev.metered, ip: ev.ip });

function closeSignal(ctx, tr, at) { const s = signal[tr]; if (s) { ctx.span.close(s.id, at); delete signal[tr]; } }
function openSignal(ctx, tr, start, level, min, max, conn) {
  signal[tr] = { id: ctx.span.open("signal", { start, transport: tr, level, dbm_min: min, dbm_max: max, connection_id: conn }), level, min, max, conn, pending: null };
}

// 1분 넘게 유지된 변화만 새 구간으로 나눈다(바뀐 시각에서 나눈다)
function settle(ctx, now) {
  for (const tr in signal) {
    const s = signal[tr], p = s.pending;
    if (!p || now - t(p.since) < HOLD) continue;
    ctx.span.close(s.id, p.since);
    openSignal(ctx, tr, p.since, p.level, p.min, p.max, s.conn);
  }
  for (const n in cell) {
    const c = cell[n], p = c.pending;
    if (!p || now - t(p.since) < HOLD) continue;
    ctx.span.close(c.id, p.since);
    cell[n] = { id: ctx.span.open("cellular", Object.assign({ start: p.since }, p.row)), rat: p.rat, pending: null };
  }
}

function onSignal(ctx, ev) {
  const tr = ev.transport;
  const conn = tr === "wifi" ? (wifi[ev.network] || {}).id || null : (cell[ev.network] || {}).id || null;
  if (tr === "wifi" && !conn) return;   // 끊긴 와이파이의 늦은 신호
  const d = ev.dbm == null ? null : ev.dbm;
  const s = signal[tr];
  if (!s || s.conn !== conn) {          // 처음이거나 다른 연결로 옮겼으면 바로 새 구간
    if (s) ctx.span.close(s.id, ev.time);
    openSignal(ctx, tr, ev.time, ev.level, d, d, conn);
    return;
  }
  // 흔들림(pending)이 1분 안에 원래 단계로 돌아오면 그 동안의 dbm은 지금 구간에 합친다
  const add = (o, v) => { if (v != null) { o.min = o.min == null ? v : Math.min(o.min, v); o.max = o.max == null ? v : Math.max(o.max, v); } };
  if (ev.level === s.level) {
    const before = s.min + "," + s.max;
    if (s.pending) { add(s, s.pending.min); add(s, s.pending.max); s.pending = null; }
    add(s, d);
    if (s.min + "," + s.max !== before) ctx.span.update(s.id, { dbm_min: s.min, dbm_max: s.max });
  } else if (s.pending && s.pending.level === ev.level) add(s.pending, d);
  else { s.pending = { level: ev.level, since: ev.time, min: d, max: d }; }
}

function onWifi(ctx, ev, keepBssid) {
  const c = wifi[ev.network];
  if (ev.kind === "lost") {
    if (!c) return;
    ctx.span.close(c.id, ev.time); delete wifi[ev.network];
    if (signal.wifi && signal.wifi.conn === c.id) closeSignal(ctx, "wifi", ev.time);
    return;
  }
  const r = wifiRow(ev, keepBssid);
  if (c && c.ssid === ev.ssid) {
    if (c.bssid !== r.bssid) { c.roams++; c.bssid = r.bssid; }
    r.roams = c.roams;
    ctx.span.update(c.id, r);
    return;
  }
  if (c) { ctx.span.close(c.id, ev.time); if (signal.wifi && signal.wifi.conn === c.id) closeSignal(ctx, "wifi", ev.time); }
  r.roams = 0; r.start = ev.time;
  wifi[ev.network] = { id: ctx.span.open("wifi", r), ssid: ev.ssid, bssid: r.bssid, roams: 0 };
}

function onCell(ctx, ev) {
  const c = cell[ev.network];
  if (!c && ev.internet === false) return;   // 통화(IMS)·MMS용 모바일 네트워크는 인터넷이 아니다
  if (ev.kind === "lost") {
    if (!c) return;
    ctx.span.close(c.id, ev.time); delete cell[ev.network];
    return;
  }
  const r = cellRow(ev);
  if (!c) { cell[ev.network] = { id: ctx.span.open("cellular", Object.assign({ start: ev.time }, r)), rat: ev.rat, pending: null }; return; }
  // 연결 직후에는 망 종류가 아직 없다(null). 처음 알게 된 종류는 구간을 나누지 않고 채운다
  if (ev.rat === c.rat || c.rat == null) { c.rat = ev.rat; c.pending = null; ctx.span.update(c.id, r); return; }
  if (!c.pending || c.pending.rat !== ev.rat) c.pending = { rat: ev.rat, since: ev.time, row: r };
  else c.pending.row = r;
}

function onDefault(ctx, ev) {
  const key = [ev.transport, ev.network, ev.label].join("|");
  if (route && route.key === key) return;
  if (route) ctx.span.close(route.id, ev.time);
  route = { id: ctx.span.open("default", { start: ev.time, transport: ev.transport, network: ev.network, label: ev.label }), key };
}

export default {
  start(ctx) {
    const keepBssid = ctx.settings.store_bssid !== false;
    ctx.on("network", ev => {
      settle(ctx, t(ev.time));
      if (ev.kind === "signal") return onSignal(ctx, ev);
      if (ev.kind === "default") return onDefault(ctx, ev);
      if (ev.transport === "wifi") onWifi(ctx, ev, keepBssid);
      else if (ev.transport === "cellular") onCell(ctx, ev);
    });
    ctx.every("1m", () => settle(ctx, t(ctx.now())));
  },
  stop(ctx) {
    const now = ctx.now();
    for (const n in wifi) { ctx.span.close(wifi[n].id, now); delete wifi[n]; }
    for (const n in cell) { ctx.span.close(cell[n].id, now); delete cell[n]; }
    for (const tr in signal) closeSignal(ctx, tr, now);
    if (route) { ctx.span.close(route.id, now); route = null; }
  }
};
