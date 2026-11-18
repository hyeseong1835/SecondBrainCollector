// win-network 플러그인 시험(2026-11-16 네트워크별 거리 구간). 실행: node --test tools/plugin-tests/win-network.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

async function load() {
  const url = new URL("../../plugins/win-network/main.js", import.meta.url);
  return (await import(url.href + "?t=" + Math.random())).default;
}

function host() {
  const spans = []; let handler = null;
  const ctx = {
    on(src, fn) { assert.equal(src, "network"); handler = fn; },
    span: {
      open(o, row) { assert.equal(o, "connection"); spans.push({ ...row }); return spans.length - 1; },
      update(id, p) { Object.assign(spans[id], p); },
      close(id, end) { spans[id].end = end; }
    },
    now: () => "2026-11-16T09:00:00Z"
  };
  return { ctx, spans, send: ev => handler(ev) };
}

const ev = (i, o) => ({ time: `2026-11-16T00:00:${String(i).padStart(2, "0")}Z`, kind: "wifi", net_key: "wifi:Dorm", name: "Dorm", ssid: "Dorm", gateway_mac: "aa:bb", adapter: "Intel", ...o });

test("같은 네트워크는 한 구간, 바뀌면 닫고 새로", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1)); h.send(ev(2, { name: "Dorm2" })); h.send(ev(3, { kind: "ethernet", net_key: "lan:70:5d", ssid: null, name: "이더넷" }));
  assert.equal(h.spans.length, 2);
  assert.equal(h.spans[0].name, "Dorm2");
  assert.equal(h.spans[0].end, "2026-11-16T00:00:03Z");
  assert.equal(h.spans[1].net_key, "lan:70:5d");
  assert.equal(h.spans[1].end, undefined);
});

test("연결 없음은 닫기만, 멈추면 열린 구간 닫음", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1)); h.send(ev(5, { kind: "none", net_key: null }));
  assert.equal(h.spans.length, 1); assert.equal(h.spans[0].end, "2026-11-16T00:00:05Z");
  h.send(ev(7));
  p.stop(h.ctx);
  assert.equal(h.spans.length, 2); assert.equal(h.spans[1].end, "2026-11-16T09:00:00Z");
});

test("SSID를 못 읽은 와이파이(net_key 없음)는 기록하지 않음", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1, { net_key: null, ssid: null }));
  assert.equal(h.spans.length, 0);
});

// ── probe(1.1.0, 랜선 위치 지문) ──
function probeHost(granted = true) {
  const events = []; const handlers = {};
  const ctx = {
    has: cap => granted && cap === "event:lan_probe",
    on(src, fn) { handlers[src] = fn; },
    span: { open() { return 0; }, update() {}, close() {} },
    event: { emit(o, row) { assert.equal(o, "probe"); events.push(row); } },
    now: () => "2026-11-19T09:00:00Z"
  };
  return { ctx, events, handlers };
}

const probeEv = {
  time: "2026-11-19T03:00:00Z", net_key: "lan:70:5d:cc:aa:bb:cc", gateway_mac: "70:5d:cc:aa:bb:cc", target: "example.duckdns.org",
  hops_json: JSON.stringify([{ n: 1, ip: "192.168.0.1", rtt_ms: 0 }, { n: 2, ip: "192.168.219.1", rtt_ms: 1 }, { n: 3, ip: null, rtt_ms: null }, { n: 4, ip: "10.245.86.197", rtt_ms: 7 }]),
  aps_json: JSON.stringify([{ bssid: "0C:96:CD:EF:78:E6", ssid: "U+Net78E7", rssi: -48, freq_mhz: 2462, channel: 11 }, { bssid: "50:46:ae:e0:20:50", ssid: "U+Net2051", rssi: -81, freq_mhz: 2437, channel: 6 }]),
  wifi: "ok"
};

test("lan_probe 이벤트 → probe 행(경로는 2번째 홉부터 요약, AP는 소문자 BSSID)", async () => {
  const p = await load(), h = probeHost();
  p.start(h.ctx);
  h.handlers.lan_probe(probeEv);
  assert.equal(h.events.length, 1);
  const r = h.events[0];
  assert.equal(r.hop_ips, "192.168.219.1>*>10.245.86.197");
  assert.equal(r.ap_count, 2);
  assert.equal(r.aps[0].bssid, "0c:96:cd:ef:78:e6");
  assert.equal(r.hops.length, 4);
  assert.equal(r.wifi, "ok");
  assert.equal(r.time, "2026-11-19T03:00:00Z");
});

test("깨진 JSON·무선 꺼짐도 행은 남긴다(빈 목록)", async () => {
  const p = await load(), h = probeHost();
  p.start(h.ctx);
  h.handlers.lan_probe({ ...probeEv, hops_json: "{", aps_json: "", wifi: "off" });
  assert.deepEqual([h.events[0].hops, h.events[0].aps, h.events[0].hop_ips, h.events[0].ap_count], [[], [], null, 0]);
});

test("lan_probe 권한이 없으면 구독하지 않는다", async () => {
  const p = await load(), h = probeHost(false);
  p.start(h.ctx);
  assert.equal(h.handlers.lan_probe, undefined);
  assert.equal(typeof h.handlers.network, "function");
});
