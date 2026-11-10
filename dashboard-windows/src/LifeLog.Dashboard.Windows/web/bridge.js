// lifelog 브리지. 모든 문서(셸과 플러그인 iframe)에 주입된다.
// 최상위(셸)는 호스트와 직접 통신하고, iframe(플러그인)은 부모(셸)에 중계를 맡긴다.
// 호스트: Windows는 WebView2(chrome.webview), Android는 WebView의 웹 메시지 리스너(LifeLogHost, app.lifelog 출처에서만 보인다).
(function () {
  if (window.lifelog) return;
  const isTop = window === window.top;
  const host = !isTop ? null
    : window.chrome && window.chrome.webview ? { post: m => chrome.webview.postMessage(m), listen: fn => chrome.webview.addEventListener("message", e => fn(e.data)) }
    : window.LifeLogHost ? { post: m => LifeLogHost.postMessage(JSON.stringify(m)), listen: fn => LifeLogHost.addEventListener("message", e => fn(typeof e.data === "string" ? JSON.parse(e.data) : e.data)) }
    : null;
  const pending = new Map();
  let seq = 0;
  const inst = Math.random().toString(36).slice(2, 10);
  const pluginId = (() => {
    const m = /^plugin-([a-z0-9-]+)\.lifelog$/.exec(location.hostname);
    return m ? m[1] : "shell";
  })();

  function call(method, ...args) {
    const id = pluginId + ":" + inst + ":" + (++seq);   // 같은 위젯을 여러 개 놓아도 응답이 섞이지 않게 문서마다 다른 inst
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      const msg = { id, from: pluginId, method, args };
      if (isTop) { if (!host) return reject(new Error("호스트 없음")); host.post(msg); }
      else window.parent.postMessage({ lifelog: "call", msg }, "*");
    });
  }

  function settle(reply) {
    const p = pending.get(reply.id);
    if (!p) return false;
    pending.delete(reply.id);
    reply.error ? p.reject(new Error(reply.error)) : p.resolve(reply.result);
    return true;
  }

  // 스크립트 오류를 호스트 로그(dashboard.log)로. 플러그인 iframe의 오류는 셸을 거친다
  window.addEventListener("error", e => { try { call("log", `${pluginId} ${location.pathname}: ${e.message} (${e.lineno})`); } catch (_) {} });
  window.addEventListener("unhandledrejection", e => { try { call("log", `${pluginId}: ${e.reason && e.reason.message || e.reason}`); } catch (_) {} });
  if (isTop && host) {
    host.listen(reply => {
      // SecondBrain 데스크톱 호스트: 사이드바에서 뷰를 고름 / 할 일을 바꿔서 다시 그림
      if (reply && reply.lifelog === "selectView") { window.dispatchEvent(new CustomEvent("lifelog:selectView", { detail: reply.id })); return; }
      if (reply && reply.lifelog === "refresh") { window.dispatchEvent(new CustomEvent("lifelog:refresh")); return; }
      if (reply && reply.lifelog === "frameError") {   // 호스트가 알린 iframe 탐색 실패 → 최대 3번 다시 읽기
        for (const f of document.querySelectorAll("iframe")) {
          if (!reply.url || !reply.url.startsWith(f.src.split("?")[0])) continue;
          const n = Number(f.dataset.retry || 0); if (n >= 3) continue;
          f.dataset.retry = n + 1;
          setTimeout(() => { f.src = f.src.split("?")[0] + "?retry=" + (n + 1); }, 400 * (n + 1));
        }
        return;
      }
      if (settle(reply)) return;
      // 플러그인 iframe의 호출에 대한 응답: id 접두가 플러그인 id
      const from = reply.id.split(":")[0];
      for (const f of document.querySelectorAll("iframe")) {
        if (f.dataset.plugin === from) f.contentWindow.postMessage({ lifelog: "reply", reply }, "*");
      }
    });
    window.addEventListener("message", e => {
      const d = e.data;
      if (!d || d.lifelog !== "call") return;
      host.post(d.msg);   // 그대로 호스트로. 권한 검사는 호스트가 from으로 한다
    });
  } else {
    // 플러그인 문서의 로드·준비 상태를 호스트 로그에 남긴다(문제 추적용)
    window.addEventListener("load", () => call("log", `${pluginId}: 문서 로드 ${location.href}`));
    window.addEventListener("message", e => {
      const d = e.data;
      if (d && d.lifelog === "reply") settle(d.reply);
      if (d && d.lifelog === "range") { current.range = d.range; listeners.forEach(fn => fn(d.range)); }
      if (d && d.lifelog === "theme") { current.theme = d.theme; setTheme(d.theme); themeListeners.forEach(fn => fn(d.theme)); }
      if (d && d.lifelog === "widget") { current.widget = d.widget; widgetListeners.forEach(fn => fn(d.widget)); }
    });
  }

  const listeners = [], widgetListeners = [], themeListeners = [];
  const current = { range: null, widget: null, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light" };
  // 이 스크립트는 문서가 만들어지는 순간 실행되므로 documentElement가 아직 없을 수 있다
  const setTheme = t => { if (document.documentElement) document.documentElement.dataset.theme = t; };
  if (!isTop) document.addEventListener("DOMContentLoaded", () => setTheme(current.theme));

  window.lifelog = {
    id: pluginId,
    query: (table, opts) => call("query", table, opts),
    outputs: () => call("outputs"),
    renderers: () => call("renderers"),
    settings: () => call("settings"),
    setSettings: s => call("setSettings", s),
    /** 분류 규칙 { pc, phone, shared }. shared면 DB(모든 기기 공유) */
    rules: () => call("rules"),
    setRules: (kind, rules) => call("setRules", kind, rules),
    /** 호스트 플랫폼: windows | android */
    platform: () => call("platform"),
    installPlugin: source => call("installPlugin", source ?? null),
    registry: () => call("registry"),
    setEnabled: (id, on) => call("setEnabled", id, on),
    reloadCollector: () => call("reloadCollector"),
    device: () => call("device"),
    // 설정 › 플러그인·수집기 관리(셸만, 데스크톱 호스트). admin(method, path, body)는 /api/collector/admin/<path>
    admin: (method, path, body) => call("collectorAdmin", method, path, body ?? null),
    secrets: plugin => call("secrets", plugin),
    setSecret: (plugin, key, value) => call("setSecret", plugin, key, value ?? null),
    audioOutputs: () => call("audioOutputs"),
    /**
     * SecondBrain 데이터(SecondBrainDesktop 호스트만). 매니페스트 capabilities에 read:sb_tasks(할 일 읽기)·read:sb_log(트레이 로그 읽기)·
     * read:sb_task_write(상태 바꾸기·편집 창 열기)를 선언한 위젯만 부를 수 있다.
     *   tasks({ view: "doable" | "all" | "closed" | "assignments", state }) · cleanup() · events(date) · focus()
     *   openTask(id) → 편집 창(바꿨으면 true) · move(id, "진행 중" | "대기" | "언젠가" | "보류" | "완료") · start(id) → 지금 하는 것으로
     */
    sb: {
      tasks: opts => call("sbTasks", opts || {}),
      cleanup: () => call("sbCleanup"),
      events: date => call("sbEvents", date || "today"),
      focus: () => call("sbFocus"),
      openTask: id => call("sbOpenTask", id),
      move: (id, state) => call("sbMove", id, state),
      start: id => call("sbStart", id),
    },
    /** 셸 → 호스트: 뷰 목록과 지금 뷰(데스크톱 사이드바용). 호스트가 모르면 무시한다 */
    _hostViews: (views, current) => call("views", views, current).catch(() => {}),
    get range() { return current.range; },
    get theme() { return current.theme; },
    /** 위젯 인스턴스 정보 { id, w, h, settings }. 셸이 iframe에 준다. */
    get widget() { return current.widget; },
    onThemeChange(fn) { themeListeners.push(fn); fn(current.theme); },
    onWidgetChange(fn) { widgetListeners.push(fn); if (current.widget) fn(current.widget); },
    /** 범위와 위젯 설정이 둘 다 준비됐을 때 한 번, 이후 바뀔 때마다 부른다 */
    ready(fn) { const go = () => { if (current.range) fn(current.range, current.widget || { settings: {} }); }; listeners.push(go); widgetListeners.push(go); go(); },
    onRangeChange(fn) { listeners.push(fn); if (current.range) fn(current.range); },
    navigate(range) { if (!isTop) window.parent.postMessage({ lifelog: "navigate", range }, "*"); },
    // 셸이 iframe에 범위를 알릴 때 쓴다
    _broadcastRange(range) { current.range = range; listeners.forEach(fn => fn(range)); for (const f of document.querySelectorAll("iframe")) f.contentWindow.postMessage({ lifelog: "range", range }, "*"); },
  };
})();
