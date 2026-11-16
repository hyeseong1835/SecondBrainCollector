// win-audio-device 플러그인 시험(2026-11-16). 실행: node --test tools/plugin-tests/win-audio-device.test.mjs
// 실제 소스 이벤트로 행 모양을 보려면: secondbrain-collector audio-device-test 10 --ndjson | node tools/plugin-tests/win-audio-device.test.mjs --live
import { test } from "node:test";
import assert from "node:assert/strict";

async function load() {
  const url = new URL("../../plugins/win-audio-device/main.js", import.meta.url);
  return (await import(url.href + "?t=" + Math.random())).default;
}

function host() {
  const spans = []; let handler = null;
  const ctx = {
    on(src, fn) { assert.equal(src, "audio_device"); handler = fn; },
    span: {
      open(o, row) { spans.push({ output: o, ...row }); return spans.length - 1; },
      update(id, p) { Object.assign(spans[id], p); },
      close(id, end) { spans[id].end = end; }
    },
    now: () => "2026-11-16T09:00:00Z"
  };
  return { ctx, spans, send: ev => handler(ev) };
}

const t = i => `2026-11-16T00:00:${String(i).padStart(2, "0")}Z`;
const dev = (i, o) => ({ time: t(i), kind: "device", endpoint_id: "{0.0.0.0}.{a}", name: "헤드폰(Buds)", flow: "render", form_factor: "headphones", state: "active", instance_id: "{1}.BTHENUM/x/7&1&0&AABBCCDDEEFF_C00000000", ...o });

if (process.argv.includes("--live")) {
  const p = await load(), h = host();
  p.start(h.ctx);
  const rl = (await import("node:readline")).createInterface({ input: process.stdin });
  for await (const line of rl) if (line.trim().startsWith("{")) h.send(JSON.parse(line));
  const open = h.spans.length;
  p.stop(h.ctx);
  console.log(`행 ${open}개(stop으로 닫기 전 열린 구간 포함):`);
  for (const s of h.spans) console.log(JSON.stringify(s));
  process.exit(0);
}

test("장치: 같은 state는 한 구간(이름 갱신), state가 바뀌면 닫고 새로", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(dev(1)); h.send(dev(2, { name: "헤드폰(Buds2)" })); h.send(dev(3, { state: "unplugged" }));
  assert.equal(h.spans.length, 2);
  assert.equal(h.spans[0].output, "device");
  assert.equal(h.spans[0].name, "헤드폰(Buds2)");
  assert.equal(h.spans[0].end, t(3));
  assert.equal(h.spans[1].state, "unplugged");
  assert.equal(h.spans[1].end, undefined);
});

test("장치: absent·disabled는 닫기만, 다시 켜지면 새 구간", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(dev(1)); h.send(dev(5, { state: "absent" }));
  assert.equal(h.spans.length, 1); assert.equal(h.spans[0].end, t(5));
  h.send(dev(7, { state: "absent" }));   // 이미 닫힌 장치의 중복 absent
  assert.equal(h.spans.length, 1);
  h.send(dev(9));
  assert.equal(h.spans.length, 2); assert.equal(h.spans[1].start, t(9));
});

test("장치: 인스턴스 ID·endpoint ID는 받은 그대로 저장, 서로 다른 장치는 따로", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(dev(1)); h.send(dev(1, { endpoint_id: "{0.0.1.0}.{b}", flow: "capture", form_factor: "headset", instance_id: null }));
  assert.equal(h.spans.length, 2);
  assert.equal(h.spans[0].instance_id, "{1}.BTHENUM/x/7&1&0&AABBCCDDEEFF_C00000000");
  assert.equal(h.spans[1].instance_id, null);
  assert.equal(h.spans[1].endpoint_id, "{0.0.1.0}.{b}");
});

const def = (i, o) => ({ time: t(i), kind: "default", flow: "render", role: "console", endpoint_id: "{0.0.0.0}.{a}", name: "스피커", ...o });

test("기본 장치: 바뀌면 닫고 새로, 같으면 이어 감, 없음은 닫기만", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(def(1)); h.send(def(2)); h.send(def(3, { endpoint_id: "{0.0.0.0}.{b}", name: "헤드폰" }));
  h.send(def(4, { role: "communications" }));   // 역할이 다르면 따로
  assert.equal(h.spans.length, 3);
  assert.equal(h.spans[0].end, t(3));
  assert.equal(h.spans[1].endpoint_id, "{0.0.0.0}.{b}");
  assert.equal(h.spans[2].role, "communications");
  h.send(def(6, { endpoint_id: null, name: null }));
  assert.equal(h.spans[1].end, t(6));
  assert.equal(h.spans.length, 3);
});

const use = (i, inUse, o) => ({ time: t(i), kind: "usage", endpoint_id: "{0.0.0.0}.{a}", flow: "render", name: "스피커", in_use: inUse, ...o });

test("사용: true로 열고 false로 닫음, 중복은 무시", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(use(1, true)); h.send(use(2, true)); h.send(use(3, false)); h.send(use(4, false)); h.send(use(5, true));
  assert.equal(h.spans.length, 2);
  assert.equal(h.spans[0].end, t(3));
  assert.equal(h.spans[1].end, undefined);
});

test("멈추면 열린 구간을 전부 닫음", async () => {
  const p = await load(), h = host();
  p.start(h.ctx);
  h.send(dev(1)); h.send(def(1)); h.send(use(1, true));
  p.stop(h.ctx);
  assert.equal(h.spans.length, 3);
  for (const s of h.spans) assert.equal(s.end, "2026-11-16T09:00:00Z");
});
