// 네트워크(Windows). network 소스가 바뀔 때(최소 10분마다) 주는 대표 네트워크를 구간으로 기록한다.
//   connection  kind(wifi|ethernet), net_key(wifi:SSID | lan:게이트웨이MAC | lan-ip:IP), name, ssid, gateway_mac, adapter
// 연결 없음(kind none)이면 열린 구간만 닫는다. 같은 net_key면 구간을 이어 간다(이름 등은 갱신).
// zones 설정(네트워크별 거리 구간)은 화면만 쓰고 여기서는 읽지 않는다.
let open = null;   // { id, key }

function close(ctx, at) { if (open) { ctx.span.close(open.id, at); open = null; } }

export default {
  start(ctx) {
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
