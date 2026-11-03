using System.Text.Json;
using Jint.Native;
using LifeLog.Collector.Windows.Sources;
using LifeLog.Core;

namespace LifeLog.Collector.Windows;

/// <summary>수집기 호스트. 설정·DB·큐·업로더·소스·플러그인을 묶는다(Documents/수집기 호스트 API.md 4절).</summary>
public sealed class Host : IAsyncDisposable
{
    public Config Config { get; }
    public Db Db { get; }
    public Queue Queue { get; }
    public SpanManager Spans { get; }
    public Dispatcher Dispatcher { get; }
    private readonly Uploader _uploader;
    private readonly List<JsPlugin> _plugins = new();
    private readonly Dictionary<string, Db.Install> _installs = new();
    private readonly Dictionary<string, TableMeta> _tables = new();
    private readonly Dictionary<string, ISource> _sources = new();
    private readonly CancellationTokenSource _cts = new();
    private readonly List<System.Threading.Timer> _timers = new();

    public Host(Config config)
    {
        Config = config;
        Db = new Db(config.ConnectionString());
        Queue = new Queue(Config.QueuePath);
        Spans = new SpanManager(Queue, config.Device);
        Dispatcher = new Dispatcher(Log);
        _uploader = new Uploader(Queue, Db, t => _tables.GetValueOrDefault(t), Log);
    }

    public static string LogPath => Path.Combine(Config.Dir, "collector.log");
    private static readonly object LogLock = new();
    public string Status { get; private set; } = "시작 중";

    public void Log(string msg)
    {
        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}";
        Console.WriteLine(line);
        lock (LogLock)
        {
            try { File.AppendAllText(LogPath, line + Environment.NewLine); } catch (IOException) { }
        }
    }
    public DateTimeOffset Now() => Db.Now();

    public async Task RunAsync(CancellationToken stop)
    {
        var ver = await Db.SchemaVersionAsync();
        if (ver > Db.KnownSchemaVersion)
            throw new InvalidOperationException($"DB 코어 스키마가 {ver}로 이 앱({Db.KnownSchemaVersion})보다 높습니다. 앱을 갱신하세요. 쓰기를 하지 않습니다.");
        if (ver < Db.MinSchemaVersion)
            throw new InvalidOperationException($"DB 코어 스키마가 {ver}입니다. 마이그레이션을 먼저 적용하세요(tools/db/migrate.py).");
        await Db.SyncClockAsync();
        Log($"DB 연결. 시계 보정 {Db.ClockOffset.TotalMilliseconds:F0}ms, 큐 대기 {Queue.Count()}건");
        await Db.TouchLastSeenAsync(Config.Device);

        // 플러그인 등록·로드
        if (Directory.Exists(Config.PluginsDir))
        {
            foreach (var dir in Directory.GetDirectories(Config.PluginsDir))
            {
                var mf = Path.Combine(dir, "manifest.json");
                if (!File.Exists(mf)) continue;
                try
                {
                    var m = Manifest.Load(mf);
                    if (m.Type != "collector") continue;
                    var r = await Db.RegisterPluginAsync(m, Config.Device);
                    var created = r.GetProperty("created_tables").EnumerateArray().Select(e => e.GetString()).ToList();
                    if (created.Count > 0) Log($"[{m.Id}] 테이블 생성: {string.Join(", ", created)}");
                    // 옛 schema_version 테이블도: 버전을 올리기 전에 큐에 쌓인 행(stop에서 닫은 구간 등)이 남아 있을 수 있다
                    foreach (var t in await Db.CollectorTablesAsync(m.Id))
                        _tables[t.Table] = t;
                    foreach (var o in m.Outputs)
                        _tables[m.TableName(o)] = new TableMeta(m.TableName(o), o.IsSpan, o.Columns);
                    var inst = await Db.GetInstallAsync(m.Id, Config.Device);
                    if (inst is null) { Log($"[{m.Id}] 설치 행이 없습니다"); continue; }
                    _installs[m.Id] = inst;
                    if (!inst.Enabled) { Log($"[{m.Id}] 꺼져 있음(필수 권한 미허용이거나 사용자가 끔)"); continue; }
                    var missing = m.Capabilities.Where(c => c.Id.StartsWith("event:") || c.Id.StartsWith("read:"))
                        .Select(c => c.Id[(c.Id.IndexOf(':') + 1)..]).Where(s => !KnownSources.Contains(s)).ToList();
                    if (missing.Count > 0) { Log($"[{m.Id}] 이 호스트에 없는 소스: {string.Join(", ", missing)}. 건너뜀"); continue; }
                    var p = new JsPlugin(m, dir, this);
                    p.Load();
                    _plugins.Add(p);
                }
                catch (Exception e) { Log($"플러그인 로드 실패 {dir}: {e.Message}"); }
            }
        }
        if (_plugins.Count == 0) Log("켜진 플러그인이 없습니다. `install`로 플러그인을 넣고 `enable`로 켜세요.");

        // 플러그인 start (디스패처에서)
        foreach (var p in _plugins)
            Dispatcher.Invoke(() => { try { p.Start(); Log($"[{p.Manifest.Id}] 시작"); } catch (Exception e) { Log($"[{p.Manifest.Id}] start 실패: {e.Message}"); } });

        // 구독된 소스만 켠다
        foreach (var src in _plugins.SelectMany(p => p.SubscribedSources).Distinct())
        {
            var s = CreateSource(src);
            if (s is null) { Log($"모르는 소스: {src}"); continue; }
            _sources[src] = s;
            s.Start(ev => Dispatcher.Post(() => { foreach (var p in _plugins) p.Deliver(src, ev); }));
            Log($"소스 켬: {src}");
        }

        // 주기 작업: 열린 구간 upsert(1분), last_seen(5분)
        _timers.Add(new System.Threading.Timer(_ => Dispatcher.Post(() => Spans.Tick(Now())), null, TimeSpan.FromMinutes(1), TimeSpan.FromMinutes(1)));
        _timers.Add(new System.Threading.Timer(_ => _ = Db.TouchLastSeenAsync(Config.Device), null, TimeSpan.FromMinutes(5), TimeSpan.FromMinutes(5)));

        var upload = _uploader.RunAsync(_cts.Token);
        Status = $"수집 중 · 플러그인 {_plugins.Count}개 · 소스 {_sources.Count}개";
        Log("수집 중. Ctrl+C 또는 `lifelog-collector stop`으로 끝냅니다.");
        try { await Task.Delay(Timeout.Infinite, stop); } catch (OperationCanceledException) { }

        Status = "종료 중";
        Log("종료 중...");
        foreach (var s in _sources.Values) s.Dispose();
        Dispatcher.Invoke(() => { foreach (var p in _plugins) p.Stop(); Spans.CloseAll(Now()); });
        foreach (var t in _timers) t.Dispose();
        // 큐를 한 번 비워 보고 끝낸다(실패해도 큐에 남는다)
        await Task.WhenAny(FlushAsync(), Task.Delay(TimeSpan.FromSeconds(10)));
        _cts.Cancel();
        try { await upload; } catch (OperationCanceledException) { }
        Log($"종료. 큐 남은 건수 {Queue.Count()}");
    }

    private async Task FlushAsync()
    {
        while (Queue.Count() > 0 && _uploader.LastError is null) await Task.Delay(500);
    }

    public static readonly HashSet<string> KnownSources = ["window", "idle", "power", "media", "input", "battery"];

    private static ISource? CreateSource(string id) => id switch
    {
        "window" => new WindowSource(),
        "idle" => new IdleSource(),
        "power" => new PowerSource(),
        "media" => new MediaSource(),
        "input" => new InputSource(),
        "battery" => new BatterySource(),
        _ => null,
    };

    // ---- ctx가 쓰는 서비스 ----
    public bool HasCapability(JsPlugin p, string cap) => _installs.TryGetValue(p.Manifest.Id, out var i) && i.Granted.Contains(cap);
    public void RequireCapability(JsPlugin p, string cap)
    {
        if (!HasCapability(p, cap)) throw new InvalidOperationException($"권한이 허용되지 않았습니다: {cap}");
    }
    public JsonElement SettingsOf(JsPlugin p) => _installs[p.Manifest.Id].Settings;
    public IEnumerable<string> GrantedOf(JsPlugin p) => _installs.TryGetValue(p.Manifest.Id, out var i) ? i.Granted : [];

    public JsonElement? StateGet(JsPlugin p, string key) => Db.StateGetAsync(p.Manifest.Id, Config.Device, key).GetAwaiter().GetResult();
    public void StateSet(JsPlugin p, string key, string json) => Db.StateSetAsync(p.Manifest.Id, Config.Device, key, json).GetAwaiter().GetResult();

    public void DisablePlugin(JsPlugin p)
    {
        var inst = _installs[p.Manifest.Id];
        _ = Db.SetInstallAsync(p.Manifest.Id, Config.Device, inst.InstalledVersion, false, null, JsonSerializer.Serialize(inst.Granted));
    }

    /// <summary>JS 행 객체에서 출력·열·시각을 뽑는다. 선언되지 않은 열은 거부.</summary>
    public (Manifest.Output o, Dictionary<string, object?> cols, DateTimeOffset? start, DateTimeOffset? end) ParseRow(
        JsPlugin p, string output, JsValue row, bool needStart = false, bool needEnd = false, bool needTime = false)
    {
        var o = p.Manifest.FindOutput(output) ?? throw new InvalidOperationException("없는 출력: " + output);
        var all = Json.ToCols(row);
        DateTimeOffset? start = null, end = null;
        if (all.Remove("start", out var s)) start = Json.ToTime(s);
        if (all.Remove("end", out var e)) end = Json.ToTime(e);
        if (all.Remove("time", out var t)) start = Json.ToTime(t);
        if ((needStart || needTime) && start is null) throw new InvalidOperationException($"{output}: {(needTime ? "time" : "start")}이 없습니다");
        if (needEnd && end is null) throw new InvalidOperationException($"{output}: end가 없습니다");
        var unknown = all.Keys.Where(k => !o.Columns.ContainsKey(k)).ToList();
        if (unknown.Count > 0) throw new InvalidOperationException($"{output}: 선언되지 않은 열 {string.Join(", ", unknown)}");
        return (o, all, start, end);
    }

    public async ValueTask DisposeAsync()
    {
        Dispatcher.Dispose();
        Queue.Dispose();
        await Db.DisposeAsync();
    }
}
