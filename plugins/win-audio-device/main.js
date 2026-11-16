// 오디오 장치(Windows). audio_device 소스가 주는 세 종류 이벤트를 구간으로 기록한다.
//   device   endpoint_id, name, flow, form_factor, state(active|unplugged|그 밖은 구간 없음), instance_id
//   default  flow, role, endpoint_id(null이면 기본 없음), name
//   usage    endpoint_id, flow, name, in_use
// 값은 소스가 준 그대로 저장한다(기기 id·종류 판단 없음). 같은 endpoint가 state만 바뀌면 구간을 닫고 새로 연다.
const devices = new Map();   // endpoint_id -> { id, state }
const defaults = new Map();  // "flow/role" -> { id, endpoint }
const usages = new Map();    // endpoint_id -> span id

function onDevice(ctx, ev) {
  const cur = devices.get(ev.endpoint_id);
  const tracked = ev.state === "active" || ev.state === "unplugged";
  if (cur && tracked && cur.state === ev.state) {
    ctx.span.update(cur.id, { name: ev.name ?? null, form_factor: ev.form_factor ?? null, instance_id: ev.instance_id ?? null });
    return;
  }
  if (cur) { ctx.span.close(cur.id, ev.time); devices.delete(ev.endpoint_id); }
  if (!tracked) return;
  devices.set(ev.endpoint_id, {
    state: ev.state,
    id: ctx.span.open("device", {
      start: ev.time, endpoint_id: ev.endpoint_id, name: ev.name ?? null, flow: ev.flow ?? null,
      form_factor: ev.form_factor ?? null, state: ev.state, instance_id: ev.instance_id ?? null
    })
  });
}

function onDefault(ctx, ev) {
  const key = ev.flow + "/" + ev.role;
  const cur = defaults.get(key);
  const endpoint = ev.endpoint_id ?? null;
  if (cur && cur.endpoint === endpoint) { ctx.span.update(cur.id, { name: ev.name ?? null }); return; }
  if (cur) { ctx.span.close(cur.id, ev.time); defaults.delete(key); }
  if (endpoint == null) return;
  defaults.set(key, {
    endpoint,
    id: ctx.span.open("default", { start: ev.time, flow: ev.flow, role: ev.role, endpoint_id: endpoint, name: ev.name ?? null })
  });
}

function onUsage(ctx, ev) {
  const cur = usages.get(ev.endpoint_id);
  if (ev.in_use) {
    if (cur !== undefined) return;
    usages.set(ev.endpoint_id, ctx.span.open("usage", { start: ev.time, endpoint_id: ev.endpoint_id, flow: ev.flow ?? null, name: ev.name ?? null }));
  } else if (cur !== undefined) {
    ctx.span.close(cur, ev.time);
    usages.delete(ev.endpoint_id);
  }
}

export default {
  start(ctx) {
    ctx.on("audio_device", ev => {
      if (ev.kind === "device") onDevice(ctx, ev);
      else if (ev.kind === "default") onDefault(ctx, ev);
      else if (ev.kind === "usage") onUsage(ctx, ev);
    });
  },
  stop(ctx) {
    const at = ctx.now();
    for (const d of devices.values()) ctx.span.close(d.id, at);
    for (const d of defaults.values()) ctx.span.close(d.id, at);
    for (const id of usages.values()) ctx.span.close(id, at);
    devices.clear(); defaults.clear(); usages.clear();
  }
};
