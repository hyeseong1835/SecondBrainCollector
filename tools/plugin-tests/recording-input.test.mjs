// 녹음 입력 장치 열 시험(2026-11-16). 실행: node --test tools/plugin-tests/recording-input.test.mjs
// phone-audio·win-laptop-mic: 소스 이벤트의 input_* 값이 행으로 가고, 없으면 null. 매니페스트 열 집합과 행 열이 맞는지도 본다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const base = { start: "2026-11-16T00:00:00Z", end: "2026-11-16T00:00:09Z", file: "2026-11-16/a.m4a", bytes: 10, sha256: "ab", codec: "aac", duration_ms: 9000, mean_db: -30, peak_db: -10, floor_db: -50 };

async function run(plugin, source, ev) {
  const url = new URL(`../../plugins/${plugin}/main.js`, import.meta.url);
  const p = (await import(url.href + "?t=" + Math.random())).default;
  const rows = []; let handler = null;
  p.start({ on(src, fn) { assert.equal(src, source); handler = fn; }, span: { put(o, row, opt) { rows.push({ o, row, opt }); } } });
  handler(ev);
  return rows;
}
const manifest = plugin => JSON.parse(readFileSync(new URL(`../../plugins/${plugin}/manifest.json`, import.meta.url), "utf8"));
const columns = plugin => { const o = manifest(plugin).outputs.find(o => o.name === "recording"); return { v: o.schema_version, cols: Object.keys(o.columns) }; };

test("phone-audio: 입력 장치 열이 행으로 간다", async () => {
  const ev = { ...base, input_type: 7, input_type_name: "BLUETOOTH_SCO", input_product: "Galaxy Buds", input_address: "AA:BB:CC:DD:EE:FF", input_id: 42, input_changed: false };
  const [{ row, opt }] = await run("phone-audio", "audio", ev);
  assert.equal(row.input_type, 7); assert.equal(row.input_type_name, "BLUETOOTH_SCO"); assert.equal(row.input_address, "AA:BB:CC:DD:EE:FF");
  assert.equal(row.input_id, 42); assert.equal(row.input_changed, false);
  assert.equal(opt.key, "2026-11-16/a.m4a");
});

test("phone-audio: 경로를 못 읽은 파일(input_* 없음)은 null", async () => {
  const [{ row }] = await run("phone-audio", "audio", base);
  for (const k of ["input_type", "input_type_name", "input_product", "input_address", "input_id", "input_changed"]) assert.equal(row[k], null, k);
  assert.equal(row.file, base.file);
});

test("win-laptop-mic: 입력 장치 열이 행으로 간다", async () => {
  const ev = { ...base, input_device_id: "{0.0.1.00000000}.{abc}", input_device_name: "마이크(K66)", input_device_instance_id: String.raw`USB\VID_0A67&PID_C006&MI_00\8&13FE6D92&0&0000` };
  const [{ row }] = await run("win-laptop-mic", "laptop_mic", ev);
  assert.equal(row.input_device_id, ev.input_device_id); assert.equal(row.input_device_name, "마이크(K66)"); assert.equal(row.input_device_instance_id, ev.input_device_instance_id);
});

test("win-laptop-mic: 인스턴스 ID를 못 읽으면 그 열만 null", async () => {
  const [{ row }] = await run("win-laptop-mic", "laptop_mic", { ...base, input_device_id: "x", input_device_name: "n", input_device_instance_id: null });
  assert.equal(row.input_device_id, "x"); assert.equal(row.input_device_instance_id, null);
});

test("행의 열이 매니페스트 열과 같고, 스키마 버전 2, 옛 열 그대로(기존 행과 호환)", async () => {
  for (const [plugin, source] of [["phone-audio", "audio"], ["win-laptop-mic", "laptop_mic"]]) {
    const { v, cols } = columns(plugin);
    assert.equal(v, 2, plugin);
    for (const old of ["file", "bytes", "sha256", "codec", "duration_ms", "mean_db", "peak_db", "floor_db"]) assert.ok(cols.includes(old), `${plugin} ${old}`);
    const [{ row }] = await run(plugin, source, base);
    assert.deepEqual(Object.keys(row).filter(k => k !== "start" && k !== "end").sort(), [...cols].sort(), plugin);
  }
});
