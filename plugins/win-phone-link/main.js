// 폰 링크·거리(Windows). phone_link 소스(노트북 GATT 서버 ← 폰 수집기)가 주는 이벤트를 구간으로 나눈다. 장소 이름은 붙이지 않는다.
//   link       폰과 BLE 링크가 붙어 있던 동안. 끊겼다가 30초 안에 다시 붙으면 같은 구간(reconnects += 1).
//              clock_offset_ms = 폰 시계 − 노트북 시계(왕복이 가장 짧았던 측정), rtt_ms = 그 왕복 시간
//   proximity  거리 구간이 같던 동안. zone = touching(≤1m) | room(≤5m) | near(그 너머, 신호는 있음) | disconnected(링크 없음·60초 동안 RSSI 없음)
//              10초 창 RSSI 중앙값 → d = 10^((ref_rssi_1m − rssi)/(10·n)). 경계에서 1.25배 히스테리시스, 새 구간은 30초 유지돼야 연다
//              (그보다 짧은 흔들림은 앞 구간에 합쳐진다). distance_m·rssi_* = 구간 전체 RSSI 중앙값 기준, confidence = 표준편차·개수로 high|medium|low
//   rssi       저수준 기록: rssi_every초(기본 10초)마다 그 동안 받은 RSSI의 중앙값·최소·최대·개수 한 줄. 0이면 끈다
//              (일단 10초. 하루 8,640줄 ≈ 1.5MB. 기숙사 등에서 보정이 끝나면 늘리거나 끈다)
// 노트북이 잠들거나 수집기가 꺼진 동안은 광고를 끄므로 구간을 닫고 공백으로 둔다(멀리 있다고 기록하지 않는다).
const LINK_HOLD = 30e3, WINDOW = 10e3, SETTLE = 30e3, STALE = 60e3, GRACE = 60e3, HYST = 1.25;
const BANDS = { touching: [0, 1], room: [1, 5], near: [5, Infinity] };
const t = s => new Date(s).getTime();
const iso = ms => new Date(ms).toISOString();

let link = null;      // { id, closing, reconnects, clocks: [{offset, rtt}] }
let prox = null;      // { id, zone, start }
let cand = null;      // { zone, since }
let samples = [];     // 지금 구간 + 후보 동안의 [ms, rssi]
let minute = [];      // rssi 요약 한 줄에 들어갈 값
let lastRssi = 0, advOn = null;
let cfg = { ref: -53, n: 2.5, every: 10 };

function median(a) { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; }
function dist(rssi) { return Math.pow(10, (cfg.ref - rssi) / (10 * cfg.n)); }
function zoneOf(d, cur) {
  const b = BANDS[cur];
  if (b && d >= b[0] / HYST && d <= b[1] * HYST) return cur;    // 지금 구간 근처면 머문다
  return d <= 1 ? "touching" : d <= 5 ? "room" : "near";
}
function confidence(vals) {
  if (vals.length < 3) return "low";
  const m = vals.reduce((a, b) => a + b, 0) / vals.length;
  const sd = Math.sqrt(vals.reduce((a, b) => a + (b - m) * (b - m), 0) / vals.length);
  return sd <= 4 && vals.length >= 10 ? "high" : sd <= 8 ? "medium" : "low";
}

function stats(ctx, until) {
  if (!prox || prox.zone === "disconnected") return;
  const end = until != null ? until : cand ? cand.since : Infinity;
  const vals = samples.filter(s => s[0] >= prox.start && s[0] < end).map(s => s[1]);
  if (!vals.length) return;
  const m = median(vals);
  ctx.span.update(prox.id, { distance_m: Math.round(dist(m) * 10) / 10, rssi_median: m, rssi_min: Math.min(...vals), rssi_max: Math.max(...vals),
    samples: vals.length, confidence: confidence(vals) });
}

function closeProx(ctx, at) {
  if (!prox) return;
  stats(ctx, at);
  ctx.span.close(prox.id, iso(at));
  prox = null; cand = null;
}
function openProx(ctx, zone, at) {
  prox = { id: ctx.span.open("proximity", { start: iso(at), zone }), zone, start: at };
  samples = samples.filter(s => s[0] >= at);
  stats(ctx);
}

function linked() { return link && !link.closing; }

// 지금 있어야 할 구간을 정하고, 30초 넘게 유지되면 바꾼다
function judge(ctx, now) {
  if (advOn == null) return;
  let target;
  if (!linked() || now - lastRssi > STALE) target = "disconnected";
  else {
    const win = samples.filter(s => s[0] > now - WINDOW).map(s => s[1]);
    if (!win.length) return;
    target = zoneOf(dist(median(win)), prox && prox.zone);
  }
  if (!prox) {
    if (target === "disconnected" && now - advOn < GRACE) return;   // 켜진 직후 폰이 붙을 시간을 준다
    openProx(ctx, target, target === "disconnected" ? advOn : now);
    return;
  }
  if (target === prox.zone) { cand = null; return; }
  // 연결 없음에서 신호가 다시 잡히면 기다리지 않고 바로 바꾼다
  if (prox.zone === "disconnected") { closeProx(ctx, now); openProx(ctx, target, now); return; }
  const since = target === "disconnected" ? Math.min(now, link && link.closing ? t(link.closing) : now) : now;
  if (!cand) { cand = { zone: target, since }; return; }
  // 지금 구간이 아닌 상태가 이어지는 동안은 since를 유지한다(후보끼리 오가도 30초 뒤 마지막 후보로 넘어간다). 연결 없음은 끊긴 시각부터
  if (cand.zone !== target) { cand.zone = target; if (target === "disconnected") cand.since = since; }
  if (now - cand.since >= SETTLE) {
    const { zone, since } = cand;
    cand = null;
    closeProx(ctx, since);
    openProx(ctx, zone, since);
  }
}

function settleLink(ctx, now) {
  if (link && link.closing && now - t(link.closing) >= LINK_HOLD) { ctx.span.close(link.id, link.closing); link = null; }
}

function onClock(ctx, ev) {
  if (!link || ev.offset_ms == null) return;
  link.clocks.push({ offset: ev.offset_ms, rtt: ev.rtt_ms });
  if (link.clocks.length > 20) link.clocks.shift();
  const best = link.clocks.reduce((a, b) => (b.rtt < a.rtt ? b : a));
  ctx.span.update(link.id, { clock_offset_ms: best.offset, rtt_ms: best.rtt });
}

function sleep(ctx, at) {
  closeProx(ctx, at);
  if (link) { ctx.span.close(link.id, link.closing || iso(at)); link = null; }
  samples = []; minute = []; advOn = null;
}

export default {
  start(ctx) {
    const s = ctx.settings || {};
    cfg = { ref: typeof s.ref_rssi_1m === "number" ? s.ref_rssi_1m : -53, n: typeof s.path_loss_n === "number" && s.path_loss_n > 0 ? s.path_loss_n : 2.5,
      every: typeof s.rssi_every === "number" && s.rssi_every >= 0 ? Math.round(s.rssi_every) : 10 };
    ctx.on("phone_link", ev => {
      const now = t(ev.time);
      settleLink(ctx, now);
      switch (ev.kind) {
        case "advertising":
          if (ev.state === "on") advOn = now; else sleep(ctx, now);
          return;
        case "link":
          if (ev.state === "connected") {
            if (advOn == null) advOn = now;
            if (link) { if (link.closing) { link.closing = null; link.reconnects++; ctx.span.update(link.id, { reconnects: link.reconnects }); } }
            else link = { id: ctx.span.open("link", { start: ev.time, reconnects: 0 }), closing: null, reconnects: 0, clocks: [] };
          } else if (link && !link.closing) link.closing = ev.time;
          judge(ctx, now);
          return;
        case "rssi":
          if (typeof ev.rssi !== "number" || ev.rssi >= 0) return;   // 0·양수는 읽기 실패
          samples.push([now, ev.rssi]); minute.push(ev.rssi);
          lastRssi = Math.max(lastRssi, now);
          judge(ctx, now);
          return;
        case "clock":
          onClock(ctx, ev);
          return;
      }
    });
    ctx.every("5s", () => { const now = t(ctx.now()); settleLink(ctx, now); judge(ctx, now); });
    ctx.every("1m", () => stats(ctx));
    if (cfg.every > 0) ctx.every(cfg.every + "s", () => {
      if (minute.length)
        ctx.event.emit("rssi", { time: ctx.now(), rssi_median: median(minute), rssi_min: Math.min(...minute), rssi_max: Math.max(...minute), samples: minute.length });
      minute = [];
    });
  },
  stop(ctx) { sleep(ctx, t(ctx.now())); }
};
