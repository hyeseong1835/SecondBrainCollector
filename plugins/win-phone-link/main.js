// 폰 링크·신호 세기(Windows). phone_link 소스(노트북 GATT 서버 ← 폰 수집기)가 주는 이벤트를 기록한다. 장소 이름은 붙이지 않는다.
//   link   폰과 BLE 링크가 붙어 있던 동안. 끊겼다가 30초 안에 다시 붙으면 같은 구간(reconnects += 1).
//          clock_offset_ms = 폰 시계 − 노트북 시계(왕복이 가장 짧았던 측정), rtt_ms = 그 왕복 시간
//   rssi   rssi_every초(기본 10초)마다 그 동안 받은 RSSI의 중앙값·최소·최대·개수 한 줄. 거리는 이 값으로만 보인다
//          (화면에서 빨강=멀다 → 초록=가깝다 연속 색. 붙어 있음·같은 방 같은 거리 구간은 2026-11-15에 없앴다). 0이면 끈다
// 노트북이 잠들거나 수집기가 꺼진 동안은 광고를 끄므로 링크를 닫고 공백으로 둔다(멀리 있다고 기록하지 않는다).
// 옛 proximity 출력(zone 구간)은 더 쓰지 않는다. 이미 저장된 행은 DB에 그대로 남는다.
const LINK_HOLD = 30e3;
const t = s => new Date(s).getTime();

let link = null;      // { id, closing, reconnects, clocks: [{offset, rtt}] }
let minute = [];      // rssi 요약 한 줄에 들어갈 값
let every = 10;

function median(a) { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; }

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
  if (link) { ctx.span.close(link.id, link.closing || new Date(at).toISOString()); link = null; }
  minute = [];
}

export default {
  start(ctx) {
    const s = ctx.settings || {};
    every = typeof s.rssi_every === "number" && s.rssi_every >= 0 ? Math.round(s.rssi_every) : 10;
    ctx.on("phone_link", ev => {
      const now = t(ev.time);
      settleLink(ctx, now);
      switch (ev.kind) {
        case "advertising":
          if (ev.state !== "on") sleep(ctx, now);
          return;
        case "link":
          if (ev.state === "connected") {
            if (link) { if (link.closing) { link.closing = null; link.reconnects++; ctx.span.update(link.id, { reconnects: link.reconnects }); } }
            else link = { id: ctx.span.open("link", { start: ev.time, reconnects: 0 }), closing: null, reconnects: 0, clocks: [] };
          } else if (link && !link.closing) link.closing = ev.time;
          return;
        case "rssi":
          if (typeof ev.rssi !== "number" || ev.rssi >= 0) return;   // 0·양수는 읽기 실패
          minute.push(ev.rssi);
          return;
        case "clock":
          onClock(ctx, ev);
          return;
      }
    });
    ctx.every("5s", () => settleLink(ctx, t(ctx.now())));
    if (every > 0) ctx.every(every + "s", () => {
      if (minute.length)
        ctx.event.emit("rssi", { time: ctx.now(), rssi_median: median(minute), rssi_min: Math.min(...minute), rssi_max: Math.max(...minute), samples: minute.length });
      minute = [];
    });
  },
  stop(ctx) { sleep(ctx, t(ctx.now())); }
};
