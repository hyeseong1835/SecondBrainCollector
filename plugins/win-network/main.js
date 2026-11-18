// 네트워크(Windows). network 소스가 바뀔 때(최소 10분마다) 주는 대표 네트워크를 구간으로 기록한다.
//   connection  kind(wifi|ethernet), net_key(wifi:SSID | lan:게이트웨이MAC | lan-ip:IP), name, ssid, gateway_mac, adapter
// 연결 없음(kind none)이면 열린 구간만 닫는다. 같은 net_key면 구간을 이어 간다(이름 등은 갱신).
// zones 설정(네트워크별 거리 구간)은 화면만 쓰고 여기서는 읽지 않는다.
// probe(1.1.0, 2026-11-19 랜선 위치 지문): lan_probe 소스가 이더넷일 때(연결 때·30분마다) 잰 공유기 너머 경로·주변 AP를 시점 행으로.
//   net_key, gateway_mac, target(경로 목적지 = API 서버), hops [{n, ip, rtt_ms}], aps [{bssid, ssid, rssi, freq_mhz, channel}],
//   hop_ips(2번째 홉부터 "ip>ip>…", * = 응답 없음. 1번째는 들고 다니는 공유기라 늘 같아 뺀다), ap_count, wifi(ok|off|denied|none|error).
//   선택 권한 event:lan_probe가 없으면(옛 호스트·허용 안 함) 재지 않는다.
let open = null;   // { id, key }

function parseList(s) {
  if (typeof s !== "string" || s === "") return [];
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : []; } catch (e) { return []; }
}

/** lan_probe 이벤트 → probe 행. */
export function probeRow(ev) {
  const hops = parseList(ev.hops_json).map(h => ({ n: h.n, ip: h.ip ?? null, rtt_ms: h.rtt_ms ?? null }));
  const aps = parseList(ev.aps_json).filter(a => a && a.bssid).map(a => ({ bssid: String(a.bssid).toLowerCase(), ssid: a.ssid ?? "", rssi: a.rssi, freq_mhz: a.freq_mhz ?? null, channel: a.channel ?? null }));
  const hopIps = hops.filter(h => h.n >= 2).map(h => h.ip || "*").join(">");
  return { time: ev.time, net_key: ev.net_key ?? null, gateway_mac: ev.gateway_mac ?? null, target: ev.target ?? null,
    hops, aps, hop_ips: hopIps || null, ap_count: aps.length, wifi: ev.wifi ?? null };
}

function close(ctx, at) { if (open) { ctx.span.close(open.id, at); open = null; } }

export default {
  start(ctx) {
    if (ctx.has && ctx.has("event:lan_probe")) ctx.on("lan_probe", ev => ctx.event.emit("probe", probeRow(ev)));
    ctx.on("network", ev => {
      const key = ev.kind === "none" ? null : (ev.net_key || null);
      if (open && open.key === key) {
        ctx.span.update(open.id, { name: ev.name ?? null, ssid: ev.ssid ?? null, gateway_mac: ev.gateway_mac ?? null, adapter: ev.adapter ?? null });
        return;
      }
      close(ctx, ev.time);
      if (key == null) return;
      open = {
        key,
        id: ctx.span.open("connection", { start: ev.time, kind: ev.kind, net_key: key, name: ev.name ?? null, ssid: ev.ssid ?? null, gateway_mac: ev.gateway_mac ?? null, adapter: ev.adapter ?? null })
      };
    });
  },
  stop(ctx) { close(ctx, ctx.now()); }
};
