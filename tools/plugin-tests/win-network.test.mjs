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
