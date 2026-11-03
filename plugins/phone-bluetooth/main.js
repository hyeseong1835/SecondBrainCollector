// 블루투스 수집기(안드로이드). bluetooth 소스가 주는 이벤트를 구간으로 나눈다. 원본(raw) 이벤트 테이블은 두지 않는다.
//   connection 기기 하나와 링크(ACL)가 이어져 있던 동안. 끊겼다가 1분 안에 다시 붙으면 같은 구간으로 본다(워치·이어폰의 짧은 재연결)
//              profiles = 그 동안 붙었던 프로필(a2dp·headset·le_audio·hid…), battery_start/end = 기기가 알려 준 배터리 처음·마지막
//   audio      A2DP로 소리가 나가던 동안(connection_id로 연결 구간을 가리킨다)
//   adapter    블루투스 켜짐(on)·꺼짐(off). 빈틈없이 이어진다
// 수집기가 꺼져 있던 동안은 알 수 없으므로, 다시 켜지면 그 때부터 새 구간을 연다(이미 연결된 기기는 소스가 snapshot으로 알려 준다).
const HOLD = 60e3;
const dev = {};       // address → { id, transports: Set, profiles: Set, battery_start, kind, closing }
const audio = {};     // address → id
let adapter = null;   // { id, state }
const t = s => new Date(s).getTime();
const join = set => [...set].sort().join(",") || null;

// BluetoothClass 정수 → 사람이 읽는 종류. 클래스가 없는 LE 기기는 오디오 프로필이 붙으면 audio
const AUDIO = { 0x0404: "headset", 0x0408: "handsfree", 0x0414: "speaker", 0x0418: "headphones", 0x041C: "speaker", 0x0420: "car", 0x0428: "speaker", 0x043C: "tv" };
function kindOf(ev, profiles) {
  const c = ev.device_class;
  if (c != null && AUDIO[c]) return AUDIO[c];
  switch (ev.major_class) {
    case "phone": return "phone";
    case "computer": return "computer";
    case "wearable": return c === 0x0704 ? "watch" : "wearable";
    case "peripheral": return (c & 0xC0) === 0x40 ? "keyboard" : (c & 0xC0) === 0x80 ? "mouse" : "input";
    case "health": return "health";
    case "audio_video": return "audio";
  }
  if (["a2dp", "headset", "le_audio", "hearing_aid"].some(p => profiles.has(p))) return "audio";
  if (profiles.has("hid")) return "input";
  return "other";
}

function closeAudio(ctx, a, at) { if (audio[a]) { ctx.span.close(audio[a], at); delete audio[a]; } }
function closeDev(ctx, a, at) { const d = dev[a]; if (!d) return; closeAudio(ctx, a, at); ctx.span.close(d.id, at); delete dev[a]; }

// 끊긴 지 1분이 지난 기기만 닫는다(끊긴 시각으로)
function settle(ctx, now) { for (const a in dev) { const c = dev[a].closing; if (c && now - t(c) >= HOLD) closeDev(ctx, a, c); } }

function ensure(ctx, ev, keepAddr) {
  let d = dev[ev.address];
  if (d) { d.closing = null; return d; }
  d = dev[ev.address] = { transports: new Set(), profiles: new Set(), battery_start: null, kind: null, closing: null };
  d.kind = kindOf(ev, d.profiles);
  d.id = ctx.span.open("connection", { start: ev.time, address: keepAddr ? ev.address : null, name: ev.name, kind: d.kind, device_type: ev.device_type,
    major_class: ev.major_class, device_class: ev.device_class, transports: null, profiles: null });
  return d;
}
function refresh(ctx, ev, d) {
  d.kind = kindOf(ev, d.profiles);
  ctx.span.update(d.id, { name: ev.name, kind: d.kind, device_type: ev.device_type, transports: join(d.transports), profiles: join(d.profiles) });
}

function onAdapter(ctx, ev) {
  if (ev.state !== "on" && ev.state !== "off") return;    // 켜는 중·끄는 중은 건너뛴다
  if (ev.state === "off") for (const a in dev) closeDev(ctx, a, dev[a].closing || ev.time);
  if (adapter && adapter.state === ev.state) return;
  if (adapter) ctx.span.close(adapter.id, ev.time);
  adapter = { id: ctx.span.open("adapter", { start: ev.time, state: ev.state }), state: ev.state };
}

export default {
  start(ctx) {
    const keepAddr = ctx.settings.store_address !== false;
    ctx.on("bluetooth", ev => {
      settle(ctx, t(ev.time));
      if (ev.kind === "adapter") return onAdapter(ctx, ev);
      const a = ev.address;
      if (!a) return;
      if (ev.kind === "acl" || ev.kind === "profile") {
        if (ev.state === "connected") {
          const d = ensure(ctx, ev, keepAddr);
          if (ev.kind === "acl" && ev.transport) d.transports.add(ev.transport);
          if (ev.kind === "profile") d.profiles.add(ev.profile);
          refresh(ctx, ev, d);
        } else if (ev.kind === "acl" && dev[a]) {
          // 링크가 하나라도 남아 있으면(클래식·LE 둘 다 붙는 기기) 이어 간다. 프로필이 끊기는 것만으로는 닫지 않는다
          const d = dev[a];
          if (ev.transport) d.transports.delete(ev.transport); else d.transports.clear();
          if (d.transports.size === 0) { d.closing = ev.time; closeAudio(ctx, a, ev.time); }
        }
        return;
      }
      if (ev.kind === "playing") {
        if (ev.playing && !audio[a]) {
          const d = ensure(ctx, ev, keepAddr);
          audio[a] = ctx.span.open("audio", { start: ev.time, connection_id: d.id, address: keepAddr ? a : null, name: ev.name });
        } else if (!ev.playing) closeAudio(ctx, a, ev.time);
        return;
      }
      if (ev.kind === "battery" && dev[a]) {
        const d = dev[a];
        if (d.battery_start == null) d.battery_start = ev.level;
        ctx.span.update(d.id, { battery_start: d.battery_start, battery_end: ev.level });
      }
    });
    ctx.every("1m", () => settle(ctx, t(ctx.now())));
  },
  stop(ctx) {
    const now = ctx.now();
    for (const a in dev) closeDev(ctx, a, dev[a].closing || now);
    if (adapter) { ctx.span.close(adapter.id, now); adapter = null; }
  }
};
