// phone-notification 플러그인 시험(2026-11-15 알림 줄이기). 실행: node --test tools/plugin-tests/phone-notification.test.mjs
// 호스트 ctx를 흉내 내 이벤트를 넣고, 쌓일 시점 행(키가 같으면 덮어씀 = 호스트 큐·DB upsert와 같음)을 센다.
import { test } from "node:test";
import assert from "node:assert/strict";

async function load() {
  // 모듈 수준 상태(open)가 시험끼리 섞이지 않게 매번 새로 불러온다
  const url = new URL("../../plugins/phone-notification/main.js", import.meta.url);
  return (await import(url.href + "?t=" + Math.random())).default;
}

function host(settings) {
  const rows = new Map(); let n = 0, spanN = 0;
  const spans = new Map();
  const ctx = {
    settings: null,
    handler: null,
    on(src, fn) { this.handler = fn; },
    event: { emit(o, row, opts) { rows.set(opts && opts.key != null ? "k:" + opts.key : "r:" + n++, row); } },
    span: {
      open(o, row) { const id = "s" + spanN++; spans.set(id, { ...row }); return id; },
      update(id, p) { Object.assign(spans.get(id), p); },
      close(id, end) { spans.get(id).end = end; }
    },
    now: () => new Date()
  };
  let cur = JSON.stringify(settings);
  Object.defineProperty(ctx, "settings", { get: () => JSON.parse(cur) });
  return { ctx, rows, spans, set(s) { cur = JSON.stringify(s); } };
}

const ev = (kind, pkg, key, i) => ({ kind, package: pkg, key, time: `2026-11-15T00:00:${String(i).padStart(2, "0")}Z`, post_time: "2026-11-15T00:00:00Z", notification: { extras: { "android.title": "t" + i } } });

test("모두 저장: 갱신마다 이벤트 행", async () => {
  const p = await load(), h = host({});
  p.start(h.ctx);
  for (let i = 0; i < 5; i++) h.ctx.handler(ev("posted", "a", "k1", i));
  h.ctx.handler(ev("removed", "a", "k1", 9));
  assert.equal(h.rows.size, 6);
  assert.equal([...h.spans.values()][0].posts, 5);
});

test("마지막 하나만: 첫 posted + 마지막 갱신 + removed", async () => {
  const p = await load(), h = host({ latest_only_packages: ["ytm"] });
  p.start(h.ctx);
  for (let i = 0; i < 42; i++) h.ctx.handler(ev("posted", "ytm", "k1", i % 60));
  h.ctx.handler(ev("removed", "ytm", "k1", 59));
  const rows = [...h.rows.values()];
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(r => r.kind), ["posted", "posted", "removed"]);
  assert.equal(rows[1].raw.notification.extras["android.title"], "t41");   // 마지막 갱신 내용
  const span = [...h.spans.values()][0];
  assert.equal(span.posts, 42); assert.equal(span.title, "t41");           // 구간 행은 그대로 최신
  // 같은 key로 새 알림이 뜨면 새 구간·새 마지막 행
  h.ctx.handler(ev("posted", "ytm", "k1", 1)); h.ctx.handler(ev("posted", "ytm", "k1", 2)); h.ctx.handler(ev("posted", "ytm", "k1", 3));
  assert.equal(h.rows.size, 5);
});

test("저장 안 함: 아무것도 남기지 않음, 설정이 바뀌면 바로 따름", async () => {
  const p = await load(), h = host({ exclude_packages: ["spam"] });
  p.start(h.ctx);
  h.ctx.handler(ev("posted", "spam", "k", 1));
  assert.equal(h.rows.size, 0); assert.equal(h.spans.size, 0);
  h.set({ exclude_packages: [] });
  h.ctx.handler(ev("posted", "spam", "k", 2));
  assert.equal(h.rows.size, 1);
  h.set({ latest_only_packages: ["spam"] });
  h.ctx.handler(ev("posted", "spam", "k", 3)); h.ctx.handler(ev("posted", "spam", "k", 4));
  assert.equal(h.rows.size, 2);
});
