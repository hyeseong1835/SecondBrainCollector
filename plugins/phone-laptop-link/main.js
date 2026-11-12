// 노트북 링크(안드로이드). laptop_link 소스(폰 GATT 클라이언트 → 노트북 수집기)의 연결을 구간으로. 거리 구간은 노트북(win-phone-link)이 기록한다.
//   link  노트북과 BLE 링크가 붙어 있던 동안. 끊겼다가 30초 안에 다시 붙으면 같은 구간(reconnects += 1).
//         rssi_median·samples = 구간 동안 폰이 읽은 RSSI, clock_offset_ms = 폰 시계 − 노트북 시계(왕복이 가장 짧았던 측정)
const HOLD = 30e3;
const t = s => new Date(s).getTime();
let link = null;   // { id, closing, reconnects, rssi: [], clocks: [] }

function median(a) { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; }
function flush(ctx) {
  if (!link || !link.rssi.length) return;
  ctx.span.update(link.id, { rssi_median: median(link.rssi), samples: link.rssi.length });
}
function close(ctx, at) { if (!link) return; flush(ctx); ctx.span.close(link.id, at); link = null; }
function settle(ctx, now) { if (link && link.closing && now - t(link.closing) >= HOLD) close(ctx, link.closing); }

export default {
  start(ctx) {
    ctx.on("laptop_link", ev => {
      settle(ctx, t(ev.time));
      if (ev.kind === "link") {
        if (ev.state === "connected") {
          if (link && link.closing) { link.closing = null; link.reconnects++; ctx.span.update(link.id, { reconnects: link.reconnects }); }
          else if (!link) link = { id: ctx.span.open("link", { start: ev.time, name: ev.name, reconnects: 0 }), closing: null, reconnects: 0, rssi: [], clocks: [] };
        } else if (link && !link.closing) link.closing = ev.time;
      } else if (ev.kind === "rssi" && link && typeof ev.rssi === "number") {
        link.rssi.push(ev.rssi);
        if (link.rssi.length > 20000) link.rssi.splice(0, 10000);
      } else if (ev.kind === "clock" && link) {
        link.clocks.push({ offset: ev.offset_ms, rtt: ev.rtt_ms });
        if (link.clocks.length > 20) link.clocks.shift();
        const best = link.clocks.reduce((a, b) => (b.rtt < a.rtt ? b : a));
        ctx.span.update(link.id, { clock_offset_ms: best.offset, rtt_ms: best.rtt });
      }
    });
    ctx.every("1m", () => { settle(ctx, t(ctx.now())); flush(ctx); });
  },
  stop(ctx) { const now = ctx.now(); if (link) close(ctx, link.closing || now); }
};
