// 설정 › 플러그인 · 수집기 관리(2026-11-11). VS Code의 확장별 설정처럼 수집기 플러그인마다 매니페스트 settings·privacy·x-secrets 선언으로 폼을 만든다.
// - 값은 기기별 설치 설정(DB, 관리 API /api/collector/admin/installs/{기기}/{플러그인})에 쓴다. 이 PC면 수집기에 다시 읽기를 요청하고,
//   다른 기기(휴대폰)는 그 기기의 수집기가 다음에 시작할 때 읽는다.
// - 비밀값(x-secrets)은 이 PC에만(DPAPI) 저장한다. DB·서버로 보내지 않고, 화면에도 저장 여부만 보인다.
// - 설정 키에 "x-picker": "audio-output"이 있으면 이 PC의 오디오 출력 장치 목록에서 고른다.
// - 수집기 관리: 용량(500MB 한도, 400MB 경고), 알림 정리(앱별 양, 기록 안 할 앱, 지우기). 용량이 경고선을 넘으면 대시보드 위에 띠를 띄운다.
// 데스크톱 호스트(SecondBrainDesktop)의 lifelog.admin·secrets·setSecret·audioOutputs가 필요하다. 없으면 이 묶음을 숨긴다.
(function () {
  const PLAT = { windows: "PC", android: "휴대폰" };
  let data = null, loading = null, error = null, unsupported = false, localDevice = "";
  let query = "";
  const secretState = {};      // 플러그인 → { 키: 저장됨 }
  let audio = null;            // [{ id, name, default }]
  const fmtBytes = n => n >= 1048576 ? (n / 1048576).toFixed(1) + "MB" : n >= 1024 ? (n / 1024).toFixed(0) + "KB" : n + "B";
  const fmtTime = t => t ? new Date(t).toLocaleString("ko-KR", { hour12: false }) : "—";
  const devName = id => { const d = (data?.devices || []).find(x => x.id === id); return d ? `${d.name || d.id}` : id; };
  const devLabel = id => { const d = (data?.devices || []).find(x => x.id === id); return d ? `${d.name || d.id}${d.id === localDevice ? " · 이 PC" : ""}` : id; };
  const hasDecls = p => (p.installs || []).some(i => (i.settings || []).length + (i.privacy || []).length + (i.secrets || []).length > 0) || (p.settings || []).length + (p.privacy || []).length + (p.secrets || []).length > 0;

  async function ensure(rerender, fresh = false) {
    if (unsupported) return;
    if (data && !fresh) return;
    if (loading) return loading;
    loading = (async () => {
      try {
        localDevice = await lifelog.device();
        data = await lifelog.admin("GET", "plugin-settings");
        error = null;
        for (const p of data.plugins || []) if ((p.installs || []).some(i => i.device === localDevice && (i.secrets || []).length)) {
          try { secretState[p.id] = await lifelog.secrets(p.id); } catch (_) { secretState[p.id] = {}; }
        }
      } catch (e) {
        if (/모르는 메서드|이 호스트에는/.test(e.message)) unsupported = true;
        error = e.message;
      } finally { loading = null; }
      rerender && rerender();
    })();
    return loading;
  }

  function sections(rerender) {
    if (!data && !error) { ensure(rerender); return [{ id: "plugins-loading", group: "플러그인", name: "불러오는 중…" }]; }
    const out = [];
    if (error) out.push({ id: "plugins-error", group: "플러그인", name: "불러오기 실패", title: error });
    for (const p of (data?.plugins || []).filter(hasDecls))
      out.push({ id: "plugin:" + p.id, group: "플러그인", name: p.name || p.id, title: p.id });
    out.push({ id: "plugins-all", group: "플러그인", name: "설정 없는 플러그인" });
    out.push({ id: "admin-storage", group: "수집기 관리", name: "용량", badge: storageWarn ? "!" : "" });
    out.push({ id: "admin-notifications", group: "수집기 관리", name: "알림 정리" });
    if (query) out.unshift({ id: "search", group: "검색", name: `"${query}" 결과` });
    return out;
  }

  const owns = id => /^(plugin:|plugins-|admin-|search$)/.test(id);

  function searchBox() {
    return `<input id="settingsSearch" class="ssearch" placeholder="설정 검색" value="${esc(query)}">`;
  }
  function bindSearch(page, rerender) {
    const el = page.querySelector("#settingsSearch"); if (!el) return;
    if (bindSearch.refocus) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); bindSearch.refocus = false; }
    el.oninput = () => {
      query = el.value.trim();
      settingsSection = query ? "search" : (settingsSection === "search" ? "general" : settingsSection);
      bindSearch.refocus = true;
      rerender();
    };
  }
  function clearSearch() { query = ""; }

  // ---- 그리기 ----
  function render(c, id) {
    if (id === "plugins-loading") { c.innerHTML = `<h2>플러그인</h2><div class="empty">불러오는 중…</div>`; return; }
    if (id === "plugins-error") { c.innerHTML = `<h2>플러그인</h2><p class="warn">${esc(error)}</p><p class="mute">관리 키가 있는 데스크톱 앱에서만 열 수 있습니다. 설정 › API 키를 확인하세요.</p><button id="spRetry">다시 불러오기</button>`; c.querySelector("#spRetry").onclick = () => { data = null; error = null; ensure(() => renderSettingsPage(), true); }; return; }
    if (id === "admin-storage") return renderStorage(c);
    if (id === "admin-notifications") return renderNotifications(c);
    if (!data) { c.innerHTML = `<div class="empty">불러오는 중…</div>`; return; }
    if (id === "plugins-all") return renderAll(c);
    if (id === "search") return renderSearch(c);
    const p = data.plugins.find(x => "plugin:" + x.id === id);
    if (!p) { c.innerHTML = `<div class="empty">없는 플러그인입니다.</div>`; return; }
    renderPlugin(c, p);
  }

  function renderPlugin(c, p) {
    const installs = p.installs || [];
    const sel = renderPlugin.device[p.id] && installs.some(i => i.device === renderPlugin.device[p.id]) ? renderPlugin.device[p.id]
      : (installs.find(i => i.device === localDevice) || installs[0])?.device;
    const inst = installs.find(i => i.device === sel);
    c.innerHTML = `<h2>${esc(p.name || p.id)} <span class="mute small">${esc(p.id)}</span></h2>
      ${p.description ? `<p class="mute">${esc(p.description)}</p>` : ""}
      ${installs.length ? `<div class="devtabs">${installs.map(i => `<button class="${i.device === sel ? "on" : ""}" data-dev="${esc(i.device)}">${esc(devLabel(i.device))}</button>`).join("")}</div>` : `<p class="warn">어느 기기에도 설치되지 않았습니다. 플러그인 페이지에서 설치하세요.</p>`}
      <div id="spBody"></div>`;
    c.querySelectorAll("[data-dev]").forEach(b => b.onclick = () => { renderPlugin.device[p.id] = b.dataset.dev; renderPlugin(c, p); });
    if (!inst) return;
    const body = c.querySelector("#spBody");
    const outdated = inst.version !== p.latest_version;
    body.innerHTML = `<div class="setting"><div class="st"><b>켜기</b></div>
        <p class="mute">이 기기(${esc(devLabel(inst.device))})에서 이 플러그인을 돌린다. 버전 ${esc(inst.version)}${outdated ? ` <span class="warn">(최신 ${esc(p.latest_version)}: 그 기기에서 다시 설치하면 새 설정이 생긴다)</span>` : ""} · 마지막 접속 ${fmtTime(inst.last_seen)}</p>
        <label class="check"><input type="checkbox" id="spEnabled" ${inst.enabled ? "checked" : ""}> 켜짐</label> <span class="msg" id="spEnabledMsg"></span>
        ${(inst.unmet || []).length ? `<p class="warn">허용되지 않은 권한: ${inst.unmet.map(esc).join(", ")}</p>` : ""}</div>
      ${group("설정", p, inst, inst.settings || [])}
      ${group("사생활", p, inst, inst.privacy || [])}
      ${secretsGroup(p, inst)}
      ${(inst.settings || []).length + (inst.privacy || []).length + (inst.secrets || []).length === 0 ? `<p class="mute">이 플러그인에는 바꿀 설정이 없습니다.</p>` : ""}
      ${inst.device !== localDevice ? `<p class="mute">이 PC가 아닌 기기의 설정은 DB에 저장되고, 그 기기의 수집기가 다음에 시작할 때 읽습니다.</p>` : ""}`;
    body.querySelector("#spEnabled").onchange = async e => {
      const on = e.target.checked, msg = body.querySelector("#spEnabledMsg");
      msg.textContent = "저장 중…";
      try {
        if (inst.device === localDevice) await lifelog.setEnabled(p.id, on);
        else await lifelog.admin("PUT", `installs/${encodeURIComponent(inst.device)}/${encodeURIComponent(p.id)}`, { enabled: on });
        inst.enabled = on; msg.textContent = "저장됨" + (inst.device === localDevice ? " · 수집기가 다시 읽음" : "");
      } catch (err) { e.target.checked = !on; msg.textContent = "실패: " + err.message; }
    };
    bindSettings(body, p, inst);
    bindSecrets(body, p, inst);
  }
  renderPlugin.device = {};

  function group(title, p, inst, decls, withCat = false) {
    if (!decls.length) return "";
    return `<h3>${esc(title)}</h3>` + decls.map(d => settingHtml(p, inst, d, withCat)).join("");
  }

  function valueOf(inst, d) {
    const v = (inst.values || {})[d.key];
    return v === undefined || v === null ? d.default : v;
  }
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function settingHtml(p, inst, d, withCat) {
    const v = valueOf(inst, d), modified = !same(v, d.default);
    const key = `${inst.device}|${p.id}|${d.key}`;
    let ctl;
    if (d["x-picker"] === "audio-output" && inst.device === localDevice) {
      ctl = `<select data-set="${esc(key)}" data-type="audio"><option value="">(녹음 안 함)</option>${(audio || []).map(a => `<option value="${esc(a.id)}" ${a.id === v ? "selected" : ""}>${esc(a.name)}${a.default ? " (기본)" : ""}</option>`).join("")}${v && !(audio || []).some(a => a.id === v) ? `<option value="${esc(v)}" selected>${audio ? "(없는 장치) " : ""}${esc(v)}</option>` : ""}</select>${audio ? "" : ` <span class="mute">장치 목록 불러오는 중…</span>`}`;
    } else switch (d.type) {
      case "boolean": ctl = `<label class="check"><input type="checkbox" data-set="${esc(key)}" data-type="boolean" ${v ? "checked" : ""}> ${esc(d.label)}</label>`; break;
      case "number": ctl = `<input type="number" data-set="${esc(key)}" data-type="number" value="${esc(v)}">`; break;
      case "enum": ctl = `<select data-set="${esc(key)}" data-type="enum">${(d.options || []).map(o => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`; break;
      case "list": ctl = `<textarea data-set="${esc(key)}" data-type="list" rows="${Math.min(12, Math.max(3, (v || []).length + 1))}" placeholder="한 줄에 하나">${esc((v || []).join("\n"))}</textarea>`; break;
      case "duration": ctl = `<input data-set="${esc(key)}" data-type="duration" value="${esc(v)}" placeholder="예: 10s, 5m, 1h" size="10">`; break;
      default: ctl = `<input data-set="${esc(key)}" data-type="string" value="${esc(v)}" size="48">`;
    }
    return `<div class="setting ${modified ? "modified" : ""}" data-row="${esc(key)}">
      <div class="st">${withCat ? `<span class="cat">${esc(p.name || p.id)} (${esc(devName(inst.device))}) › </span>` : ""}<b>${esc(d.label)}</b>
        <button class="reset" data-reset="${esc(key)}" title="기본값으로 (${esc(JSON.stringify(d.default))})" ${modified ? "" : "hidden"}>↺</button></div>
      ${d.description ? `<p class="mute">${esc(d.description)}</p>` : ""}
      <div class="ctl">${ctl} <span class="msg"></span></div>
      <div class="mute small">${esc(d.key)} · ${esc(d.type)}</div></div>`;
  }

  function parse(type, el) {
    switch (type) {
      case "boolean": return el.checked;
      case "number": { const n = Number(el.value); if (el.value.trim() === "" || !isFinite(n)) throw new Error("숫자가 아닙니다"); return n; }
      case "list": return el.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      case "duration": if (!/^[0-9]+(ms|s|m|h|d)$/.test(el.value.trim())) throw new Error("10s, 5m, 1h 꼴로 적습니다"); return el.value.trim();
      default: return el.value;
    }
  }

  function findTarget(key) {
    const [device, pid, k] = key.split("|");
    const p = data.plugins.find(x => x.id === pid); const inst = p && p.installs.find(i => i.device === device);
    const d = inst && [...(inst.settings || []), ...(inst.privacy || [])].find(x => x.key === k);
    return { device, p, inst, d };
  }

  async function save(key, value, row) {
    const { device, p, inst, d } = findTarget(key);
    const msg = row.querySelector(".msg");
    msg.textContent = "저장 중…"; msg.className = "msg";
    try {
      const r = await lifelog.admin("PUT", `installs/${encodeURIComponent(device)}/${encodeURIComponent(p.id)}`, { settings: { [d.key]: value } });
      inst.values = r && r.settings ? r.settings : Object.assign({}, inst.values, { [d.key]: value });
      let reloaded = false;
      if (device === localDevice) { try { reloaded = await lifelog.reloadCollector(); } catch (_) { } }
      msg.textContent = "저장됨" + (device === localDevice ? (reloaded ? " · 수집기가 다시 읽음" : " · 수집기가 꺼져 있음") : " · 그 기기가 다음 시작 때 반영");
      const modified = !same(valueOf(inst, d), d.default);
      row.classList.toggle("modified", modified);
      const rb = row.querySelector(".reset"); if (rb) rb.hidden = !modified;
    } catch (e) { msg.textContent = "실패: " + e.message; msg.className = "msg warn"; }
  }

  function bindSettings(root, ownerP, ownerInst) {
    root.querySelectorAll("[data-set]").forEach(el => {
      const row = el.closest(".setting");
      const go = () => { try { save(el.dataset.set, parse(el.dataset.type === "audio" ? "string" : el.dataset.type, el), row); } catch (e) { const m = row.querySelector(".msg"); m.textContent = e.message; m.className = "msg warn"; } };
      if (el.dataset.type === "boolean" || el.tagName === "SELECT") el.onchange = go;
      else if (el.dataset.type === "list") el.onchange = go;          // 포커스를 잃을 때
      else { el.onchange = go; el.onkeydown = e => { if (e.key === "Enter") el.blur(); }; }
    });
    root.querySelectorAll("[data-reset]").forEach(b => b.onclick = async () => {
      const { d } = findTarget(b.dataset.reset);
      await save(b.dataset.reset, d.default, b.closest(".setting"));
      renderSettingsPage();
    });
    // 오디오 장치 목록은 처음 필요할 때 한 번 받는다
    if (!audio && root.querySelector('[data-type="audio"]')) {
      lifelog.audioOutputs().then(list => { audio = list || []; renderSettingsPage(); }).catch(e => { audio = []; root.querySelectorAll('[data-type="audio"]').forEach(s => s.insertAdjacentHTML("afterend", ` <span class="warn">${esc(e.message)}</span>`)); });
    }
  }

  function secretsGroup(p, inst) {
    const decls = inst.secrets || [];
    if (!decls.length) return "";
    if (inst.device !== localDevice) return `<h3>비밀값</h3><p class="mute">비밀값(${decls.map(d => esc(d.label)).join(", ")})은 그 기기에만 저장되므로 그 기기의 앱에서 넣습니다.</p>`;
    const st = secretState[p.id] || {};
    return `<h3>비밀값</h3><p class="mute">이 PC에만 DPAPI(이 Windows 사용자)로 암호화해 저장합니다. DB·서버로 보내지 않고, 저장한 값은 다시 보여 주지 않습니다.</p>` +
      decls.map(d => `<div class="setting ${st[d.key] ? "modified" : ""}"><div class="st"><b>${esc(d.label)}</b> <span class="chip ${st[d.key] ? "on" : "off"}">${st[d.key] ? "저장됨" : "없음"}</span></div>
        ${d.description ? `<p class="mute">${esc(d.description)}</p>` : ""}
        <div class="ctl"><input type="password" autocomplete="off" data-secret="${esc(d.key)}" placeholder="${st[d.key] ? "바꾸려면 새 값을 넣으세요" : "값을 붙여 넣으세요"}" size="48">
        <button data-secret-save="${esc(d.key)}">저장</button>${st[d.key] ? `<button data-secret-clear="${esc(d.key)}">지우기</button>` : ""} <span class="msg"></span></div>
        <div class="mute small">${esc(d.key)} · secret</div></div>`).join("");
  }

  function bindSecrets(root, p) {
    const put = async (key, value, row) => {
      const msg = row.querySelector(".msg"); msg.textContent = "저장 중…";
      try {
        const r = await lifelog.setSecret(p.id, key, value);
        secretState[p.id] = await lifelog.secrets(p.id);
        renderSettingsPage();
        const m2 = document.querySelector(`[data-secret="${CSS.escape(key)}"]`)?.closest(".setting")?.querySelector(".msg");
        if (m2) m2.textContent = (value ? "저장됨" : "지움") + (r && r.reloaded ? " · 수집기가 다시 읽음" : "");
      } catch (e) { msg.textContent = "실패: " + e.message; msg.className = "msg warn"; }
    };
    root.querySelectorAll("[data-secret-save]").forEach(b => b.onclick = () => {
      const row = b.closest(".setting"), input = row.querySelector("[data-secret]");
      if (!input.value.trim()) { row.querySelector(".msg").textContent = "값이 비었습니다"; return; }
      put(b.dataset.secretSave, input.value, row); input.value = "";
    });
    root.querySelectorAll("[data-secret]").forEach(i => i.onkeydown = e => { if (e.key === "Enter") i.closest(".setting").querySelector("[data-secret-save]").click(); });
    root.querySelectorAll("[data-secret-clear]").forEach(b => b.onclick = () => {
      if (!confirm("이 비밀값을 이 PC에서 지웁니다. 계속할까요?")) return;
      put(b.dataset.secretClear, null, b.closest(".setting"));
    });
  }

  function renderAll(c) {
    const rest = (data.plugins || []).filter(p => !hasDecls(p));
    c.innerHTML = `<h2>설정 없는 플러그인</h2><p class="mute">바꿀 설정이 없는 수집기 플러그인. 켜고 끄기만 할 수 있다(이 PC는 플러그인 페이지, 다른 기기는 아래).</p>
      <table class="unmatched"><thead><tr><th>플러그인</th>${(data.devices || []).map(d => `<th>${esc(devLabel(d.id))}</th>`).join("")}</tr></thead><tbody>
      ${rest.map(p => `<tr><td><b>${esc(p.name || p.id)}</b><br><span class="mute">${esc(p.id)}</span></td>${(data.devices || []).map(d => {
        const i = (p.installs || []).find(x => x.device === d.id);
        return `<td>${i ? `<label class="check"><input type="checkbox" data-en="${esc(d.id)}|${esc(p.id)}" ${i.enabled ? "checked" : ""}> ${esc(i.version)}</label>` : `<span class="mute">—</span>`}</td>`;
      }).join("")}</tr>`).join("")}</tbody></table>`;
    c.querySelectorAll("[data-en]").forEach(el => el.onchange = async () => {
      const [dev, pid] = el.dataset.en.split("|");
      try {
        if (dev === localDevice) await lifelog.setEnabled(pid, el.checked);
        else await lifelog.admin("PUT", `installs/${encodeURIComponent(dev)}/${encodeURIComponent(pid)}`, { enabled: el.checked });
        const i = data.plugins.find(p => p.id === pid).installs.find(x => x.device === dev); i.enabled = el.checked;
      } catch (e) { el.checked = !el.checked; alert(e.message); }
    });
  }

  function renderSearch(c) {
    const q = query.toLowerCase();
    const hit = (...xs) => xs.some(x => String(x || "").toLowerCase().includes(q));
    let html = `<h2>"${esc(query)}" 검색</h2>`, n = 0;
    for (const p of data.plugins || []) for (const inst of p.installs || []) {
      const pHit = hit(p.name, p.id);
      const ds = [...(inst.settings || []), ...(inst.privacy || [])].filter(d => pHit || hit(d.label, d.description, d.key));
      const ss = (inst.secrets || []).filter(d => pHit || hit(d.label, d.description, d.key, "비밀값 secret 토큰"));
      if (!ds.length && !ss.length) continue;
      n += ds.length + ss.length;
      html += ds.map(d => settingHtml(p, inst, d, true)).join("");
      if (ss.length) html += `<div class="setting"><div class="st"><span class="cat">${esc(p.name || p.id)} (${esc(devName(inst.device))}) › </span><b>${ss.map(d => esc(d.label)).join(", ")}</b></div><p class="mute">비밀값은 플러그인 화면에서 넣습니다.</p><button data-open="plugin:${esc(p.id)}">열기</button></div>`;
    }
    if (!n) html += `<div class="empty">맞는 설정이 없습니다.</div>`;
    c.innerHTML = html;
    bindSettings(c);
    c.querySelectorAll("[data-open]").forEach(b => b.onclick = () => { query = ""; settingsSection = b.dataset.open; renderSettingsPage(); });
  }

  // ---- 수집기 관리 ----
  let storageWarn = false;
  async function renderStorage(c) {
    c.innerHTML = `<h2>용량</h2><div class="empty">불러오는 중…</div>`;
    try {
      const s = await lifelog.admin("GET", "storage");
      storageWarn = !!s.warn;
      const pct = Math.min(100, s.databaseBytes / s.limitBytes * 100), warnPct = s.warnBytes / s.limitBytes * 100;
      c.innerHTML = `<h2>용량</h2>
        <p class="${s.warn ? "warn" : "mute"}">DB ${fmtBytes(s.databaseBytes)} / ${fmtBytes(s.limitBytes)} (Supabase 무료 한도). ${s.warn ? `경고선(${fmtBytes(s.warnBytes)})을 넘었습니다. 알림 정리나 보관(SecondBrainApi tools/collector/manage.py archive)으로 줄이세요.` : `경고선은 ${fmtBytes(s.warnBytes)}입니다.`}</p>
        <div class="meter"><div class="fill ${s.warn ? "bad" : ""}" style="width:${pct.toFixed(1)}%"></div><div class="mark" style="left:${warnPct}%" title="경고선"></div></div>
        <h3>표별 크기</h3><table class="unmatched"><thead><tr><th>스키마</th><th>표</th><th>행(추정)</th><th>크기</th></tr></thead><tbody>
        ${s.tables.slice(0, 25).map(t => `<tr><td>${esc(t.schema)}</td><td><code>${esc(t.table)}</code></td><td class="num">${Number(t.rows).toLocaleString("ko-KR")}</td><td class="num">${fmtBytes(t.bytes)}</td></tr>`).join("")}</tbody></table>`;
    } catch (e) { c.innerHTML = `<h2>용량</h2><p class="warn">${esc(e.message)}</p>`; }
  }

  async function renderNotifications(c) {
    const days = renderNotifications.days ?? 30;
    c.innerHTML = `<h2>알림 정리</h2><div class="empty">불러오는 중…</div>`;
    try {
      await ensure(null);
      const phones = (data?.plugins || []).find(p => p.id === "phone-notification")?.installs || [];
      const dev = renderNotifications.device && phones.some(i => i.device === renderNotifications.device) ? renderNotifications.device : phones[0]?.device;
      const inst = phones.find(i => i.device === dev);
      const excludes = new Set((inst?.values?.exclude_packages) || []);
      const rows = await lifelog.admin("GET", `notifications${days ? `?days=${days}` : ""}`);
      const total = rows.reduce((a, r) => a + r.bytes, 0);
      c.innerHTML = `<h2>알림 정리</h2>
        <p class="mute">휴대폰 알림을 많이 남긴 앱 순. <b>기록 안 함</b>은 phone-notification 설정 "기록 안 할 앱"(exclude_packages)에 넣어 앞으로 기록하지 않게 하고(휴대폰 수집기가 다음 시작 때 반영), <b>지우기</b>는 그 앱의 지난 알림 행을 DB에서 지운다(되돌릴 수 없음).</p>
        <div class="row">기간 <select id="nDays"><option value="7">7일</option><option value="30">30일</option><option value="0">전체</option></select>
          ${phones.length > 1 ? `기기 <select id="nDev">${phones.map(i => `<option value="${esc(i.device)}" ${i.device === dev ? "selected" : ""}>${esc(devLabel(i.device))}</option>`).join("")}</select>` : ""}
          <span class="mute">합계 ${fmtBytes(total)} · 기록 안 하는 앱 ${excludes.size}개</span></div>
        <table class="unmatched"><thead><tr><th>앱(package)</th><th>이벤트</th><th>알림</th><th>크기</th><th>마지막</th><th></th></tr></thead><tbody>
        ${rows.map(r => `<tr class="${excludes.has(r.package) ? "excluded" : ""}"><td><code>${esc(r.package)}</code>${excludes.has(r.package) ? ` <span class="chip off">기록 안 함</span>` : ""}</td>
          <td class="num">${Number(r.events).toLocaleString("ko-KR")}</td><td class="num">${Number(r.notifications).toLocaleString("ko-KR")}</td><td class="num">${fmtBytes(r.bytes)}</td><td class="num">${fmtTime(r.last)}</td>
          <td class="tools">${inst ? `<button data-ex="${esc(r.package)}">${excludes.has(r.package) ? "다시 기록" : "기록 안 함"}</button>` : ""}<button data-del="${esc(r.package)}" data-n="${r.events + r.notifications}">지우기</button></td></tr>`).join("")}
        </tbody></table>
        ${excludes.size ? `<h3>기록 안 하는 앱</h3><p>${[...excludes].map(x => `<code>${esc(x)}</code>`).join(" ")}</p>` : ""}`;
      c.querySelector("#nDays").value = String(days);
      c.querySelector("#nDays").onchange = e => { renderNotifications.days = Number(e.target.value); renderNotifications(c); };
      const nd = c.querySelector("#nDev"); if (nd) nd.onchange = e => { renderNotifications.device = e.target.value; renderNotifications(c); };
      c.querySelectorAll("[data-ex]").forEach(b => b.onclick = async () => {
        const pkg = b.dataset.ex, next = new Set(excludes);
        next.has(pkg) ? next.delete(pkg) : next.add(pkg);
        b.disabled = true;
        try {
          const r = await lifelog.admin("PUT", `notification-excludes/${encodeURIComponent(dev)}`, { packages: [...next] });
          inst.values = r && r.settings ? r.settings : Object.assign({}, inst.values, { exclude_packages: [...next] });
          renderNotifications(c);
        } catch (e) { alert(e.message); b.disabled = false; }
      });
      c.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
        if (!confirm(`${b.dataset.del}의 알림 행 약 ${b.dataset.n}개를 DB에서 지웁니다(모든 기간, 모든 기기). 보관 파일을 만들지 않으며 되돌릴 수 없습니다. 계속할까요?`)) return;
        b.disabled = true;
        try { const r = await lifelog.admin("DELETE", `notifications?package=${encodeURIComponent(b.dataset.del)}`); alert(`${r.deleted}행을 지웠습니다.`); renderNotifications(c); }
        catch (e) { alert(e.message); b.disabled = false; }
      });
    } catch (e) { c.innerHTML = `<h2>알림 정리</h2><p class="warn">${esc(e.message)}</p>`; }
  }

  // 대시보드 위 용량 경고 띠: 시작하고 조금 뒤 한 번, 그 뒤 6시간마다 본다
  async function checkStorage() {
    if (unsupported) return;
    try {
      const s = await lifelog.admin("GET", "storage");
      storageWarn = !!s.warn;
      let bar = document.getElementById("storageWarn");
      if (!s.warn) { if (bar) bar.remove(); return; }
      if (!bar) { bar = document.createElement("div"); bar.id = "storageWarn"; bar.className = "sbwarn"; document.body.insertBefore(bar, document.body.firstChild); }
      bar.innerHTML = `DB 용량 ${fmtBytes(s.databaseBytes)} / ${fmtBytes(s.limitBytes)}: 경고선 ${fmtBytes(s.warnBytes)}을 넘었습니다. <button id="storageWarnOpen">용량 보기</button>`;
      bar.querySelector("#storageWarnOpen").onclick = () => { settingsSection = "admin-storage"; showPage("settingsPage"); };
    } catch (e) { if (/모르는 메서드|이 호스트에는/.test(e.message)) unsupported = true; }
  }
  setTimeout(checkStorage, 5000);
  setInterval(checkStorage, 6 * 3600 * 1000);

  window.SettingsPlugins = {
    available: () => !unsupported,
    sections, owns, render, searchBox, bindSearch, clearSearch,
    refresh: () => { data = null; error = null; },
  };
})();
