// 알림 수집기(안드로이드). notification 소스가 주는 posted / removed / snapshot 이벤트를
//   event        시점 행: 이벤트 하나를 raw(jsonb)에 통째로. 얻을 수 있는 모든 정보를 원본 그대로 둔다
//   notification 구간 행: 알림 하나(key)가 떠 있던 동안. 다시 posted되면 열만 갱신(posts 증가), removed면 reason을 적고 닫는다
// 수집기가 다시 시작하면 떠 있던 알림이 snapshot으로 다시 오고, post_time이 같으면 같은 구간 행을 이어 쓴다(id는 start+key로 결정).
const open = {};   // key → { id, posts }
const TEXT_KEY = /title|text|message|people|person|history|conversation|lines|ticker|repl|name|info|summary|picture/i;

// store_content가 꺼져 있으면 사람이 쓴 내용(제목·본문·메시지·사람)을 지우고 메타데이터만 남긴다
function strip(ev) {
  const n = ev.notification;
  if (!n) return;
  n.ticker = null;
  for (const k in n.extras) if (TEXT_KEY.test(k)) delete n.extras[k];
  for (const a of n.actions || []) { a.title = null; for (const r of a.remote_inputs || []) { r.label = null; r.choices = []; } }
  if (n.public_version) { n.public_version.ticker = null; for (const k in n.public_version.extras) if (TEXT_KEY.test(k)) delete n.public_version.extras[k]; }
  if (ev.ranking) { ev.ranking.smart_replies = []; ev.ranking.smart_actions = []; ev.ranking.importance_explanation = null; }
}

function summary(ev, content) {
  const n = ev.notification || {}, x = n.extras || {};
  const str = v => (v == null ? null : String(v));
  return {
    package: ev.package, key: ev.key, notif_id: ev.id, tag: ev.tag, channel: n.channel_id, category: n.category,
    title: content ? str(x["android.title"]) : null, text: content ? str(x["android.text"]) : null, sub_text: content ? str(x["android.subText"]) : null,
    ongoing: !!ev.ongoing, group_key: ev.group_key, group_summary: ((n.flags || 0) & 0x200) !== 0,
    importance: ev.ranking ? ev.ranking.importance : null
  };
}

export default {
  start(ctx) {
    const s = ctx.settings;
    const exclude = new Set(s.exclude_packages || []);
    const content = s.store_content !== false;
    ctx.on("notification", ev => {
      if (exclude.has(ev.package)) return;
      if (!content) strip(ev);
      ctx.event.emit("event", { time: ev.time, kind: ev.kind, key: ev.key, package: ev.package, reason: ev.reason, raw: ev });
      const c = open[ev.key];
      if (ev.kind === "removed") {
        if (!c) return;
        ctx.span.update(c.id, { removed_reason: ev.reason });
        ctx.span.close(c.id, ev.time);
        delete open[ev.key];
        return;
      }
      const row = summary(ev, content);
      if (c) { c.posts++; row.posts = c.posts; ctx.span.update(c.id, row); }
      else { row.posts = 1; row.start = ev.post_time; open[ev.key] = { id: ctx.span.open("notification", row), posts: 1 }; }
    });
  },
  stop(ctx) { for (const k in open) ctx.span.close(open[k].id, ctx.now()); for (const k in open) delete open[k]; }
};
