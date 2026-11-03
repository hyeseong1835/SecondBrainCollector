// 와이파이 연결 수집기(안드로이드). wifi 소스가 주는 connected / changed / lost 이벤트를
//   event      시점 행: 이벤트 하나를 raw(jsonb)에 통째로
//   connection 구간 행: 한 네트워크(SSID)에 붙어 있던 동안. 같은 SSID 안에서 AP를 옮기면(BSSID 변경) 열만 갱신(roams 증가), SSID가 바뀌면 새 구간
// 수집기가 꺼져 있던 동안은 알 수 없으므로, 다시 켜지면 그 때부터 새 구간을 연다.
const open = {};   // network → { id, ssid, bssid, roams }

function band(mhz) {
  if (!mhz) return null;
  if (mhz < 2500) return "2.4GHz";
  if (mhz < 5925) return "5GHz";
  if (mhz < 7200) return "6GHz";
  return "60GHz";
}

function row(ev) {
  const link = ev.link || {};
  return {
    network: ev.network, ssid: ev.ssid, bssid: ev.bssid, frequency_mhz: ev.frequency_mhz, band: band(ev.frequency_mhz),
    standard: ev.standard, security: ev.security, rssi: ev.rssi, link_speed_mbps: ev.link_speed_mbps,
    validated: ev.validated, metered: ev.metered,
    ip: (link.addresses || []).filter(a => a.indexOf(":") < 0).map(a => a.split("/")[0])[0] || null,
    gateway: (link.gateways || [])[0] || null
  };
}

export default {
  start(ctx) {
    const keepBssid = ctx.settings.store_bssid !== false;
    ctx.on("wifi", ev => {
      if (!keepBssid) ev.bssid = null;
      ctx.event.emit("event", { time: ev.time, kind: ev.kind, network: ev.network, ssid: ev.ssid, bssid: ev.bssid, raw: ev });
      const c = open[ev.network];
      if (ev.kind === "lost") {
        if (!c) return;
        ctx.span.close(c.id, ev.time);
        delete open[ev.network];
        return;
      }
      const r = row(ev);
      if (c && c.ssid === ev.ssid) {
        if (c.bssid !== ev.bssid) { c.roams++; c.bssid = ev.bssid; }
        r.roams = c.roams;
        ctx.span.update(c.id, r);
        return;
      }
      if (c) ctx.span.close(c.id, ev.time);
      r.roams = 0; r.start = ev.time;
      open[ev.network] = { id: ctx.span.open("connection", r), ssid: ev.ssid, bssid: ev.bssid, roams: 0 };
    });
  },
  stop(ctx) { for (const k in open) ctx.span.close(open[k].id, ctx.now()); for (const k in open) delete open[k]; }
};
