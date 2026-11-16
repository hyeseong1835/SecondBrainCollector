// win-bluetooth 플러그인 시험(2026-11-16). 실행: node --test tools/plugin-tests/win-bluetooth.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

async function load() {
  const url = new URL("../../plugins/win-bluetooth/main.js", import.meta.url);
  return (await import(url.href + "?t=" + Math.random())).default;
}

function host(settings = {}) {
  const spans = []; let handler = null, tick = null, nowIso = "2026-11-16T09:00:00Z";
  const ctx = {
    settings,
    on(src, fn) { assert.equal(src, "bluetooth"); handler = fn; },
    every(_i, fn) { tick = fn; },
    span: {
      open(o, row) { spans.push({ output: o, ...row }); return spans.length - 1; },
      update(id, p) { Object.assign(spans[id], p); },
      close(id, end) { spans[id].end = end; }
    },
    now: () => nowIso
  };
  return { ctx, spans, send: ev => handler(ev), tick: iso => { nowIso = iso; tick(); }, setNow: iso => { nowIso = iso; } };
}

const T = s => `2026-11-16T00:${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}Z`;
const ev = (s, o) => ({ time: T(s), kind: "acl", state: "connected", transport: "bredr", address: "88:92:CC:81:70:6A", address_type: "public", name: "ULT WEAR",
  device_type: "classic", major_class: "audio_video", device_class: 0x0404, ...o });

test("연결 구간: 열 때 이름·MAC·클래스, 종류는 클래스에서", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1));
  assert.equal(h.spans.length, 1);
  const s = h.spans[0];
  assert.equal(s.output, "connection");
  assert.equal(s.address, "88:92:CC:81:70:6A");
  assert.equal(s.name, "ULT WEAR");
  assert.equal(s.kind, "headset");
  assert.equal(s.device_class, 0x0404);
  assert.equal(s.transports, "bredr");
  assert.equal(s.profiles, null);
  assert.equal(s.start, T(1));
});

test("끊기고 1분 안에 다시 붙으면 같은 구간, 지나면 끊긴 시각으로 닫힘", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1)); h.send(ev(10, { state: "disconnected" })); h.send(ev(40));
  assert.equal(h.spans.length, 1);
  assert.equal(h.spans[0].end, undefined);
  h.send(ev(50, { state: "disconnected" }));
  h.tick(T(200));
  assert.equal(h.spans[0].end, T(50));
});

test("클래식·LE 둘 다 붙는 기기: 링크가 모두 끊겨야 닫힘", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  const d = { address: "B0:64:E0:17:9D:F3", name: "Buds", device_type: "dual" };
  h.send(ev(1, { ...d })); h.send(ev(2, { ...d, transport: "le" }));
  assert.equal(h.spans[0].transports, "bredr,le");
  h.send(ev(10, { ...d, state: "disconnected" }));
  h.tick(T(300));
  assert.equal(h.spans[0].end, undefined);
  h.send(ev(400, { ...d, transport: "le", state: "disconnected" }));
  h.tick(T(500));
  assert.equal(h.spans[0].end, T(400));
  assert.equal(h.spans[0].device_type, "dual");
});

test("store_address를 끄면 주소는 null, 이름은 남는다", async () => {
  const p = await load(), h = host({ store_address: false });
  p.start(h.ctx);
  h.send(ev(1));
  assert.equal(h.spans[0].address, null);
  assert.equal(h.spans[0].name, "ULT WEAR");
});

test("랜덤 주소 LE 기기는 기본으로 기록하지 않음", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1, { address: "DD:4C:D7:1E:07:00", address_type: "random", transport: "le", name: "MX Master 3S", device_type: "le", major_class: null, device_class: null }));
  h.send(ev(2, { kind: "battery", address: "DD:4C:D7:1E:07:00", address_type: "random", level: 60 }));
  assert.equal(h.spans.length, 0);
});

test("include_random_le를 켜도 랜덤 주소는 저장하지 않는다", async () => {
  const p = await load(), h = host({ include_random_le: true });
  p.start(h.ctx);
  h.send(ev(1, { address: "DD:4C:D7:1E:07:00", address_type: "random", transport: "le", name: "MX Master 3S", device_type: "le", major_class: null, device_class: null }));
  assert.equal(h.spans.length, 1);
  assert.equal(h.spans[0].address, null);
  assert.equal(h.spans[0].name, "MX Master 3S");
  assert.equal(h.spans[0].kind, "other");
});

test("배터리: 처음 값과 마지막 값", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(ev(1));
  h.send(ev(2, { kind: "battery", level: 100 })); h.send(ev(60, { kind: "battery", level: 90 }));
  assert.equal(h.spans[0].battery_start, 100);
  assert.equal(h.spans[0].battery_end, 90);
});

test("어댑터: 켜짐·꺼짐 구간이 이어지고, 꺼지면 연결도 닫힘", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send({ time: T(0), kind: "adapter", state: "on" });
  h.send(ev(1));
  h.send({ time: T(30), kind: "adapter", state: "off" });
  h.send({ time: T(60), kind: "adapter", state: "on" });
  const ad = h.spans.filter(s => s.output === "adapter");
  assert.deepEqual(ad.map(s => [s.state, s.start, s.end]), [["on", T(0), T(30)], ["off", T(30), T(60)], ["on", T(60), undefined]]);
  assert.equal(h.spans.find(s => s.output === "connection").end, T(30));
});

test("같은 상태의 어댑터 이벤트는 구간을 늘리지 않음, 멈추면 열린 구간 닫음", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send({ time: T(0), kind: "adapter", state: "on" }); h.send({ time: T(5), kind: "adapter", state: "on" });
  h.send(ev(6));
  assert.equal(h.spans.length, 2);
  p.stop(h.ctx);
  assert.equal(h.spans[0].end, "2026-11-16T09:00:00Z");
  assert.equal(h.spans[1].end, "2026-11-16T09:00:00Z");
});
