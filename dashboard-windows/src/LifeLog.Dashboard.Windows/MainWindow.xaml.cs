using System.IO;
using System.Text.Json;
using System.Windows;
using LifeLog.Core;
using Microsoft.Web.WebView2.Core;

namespace LifeLog.Dashboard.Windows;

/// <summary>
/// 대시보드 창 = WebView2 하나. 셸 페이지(web/index.html)가 날짜 이동·내장 타임라인·렌더러 플러그인(iframe)을 그린다.
/// 호스트는 lifelog 브리지(Bridge.cs)로 query·등록부·설정만 제공한다.
/// </summary>
public partial class MainWindow : Window
{
    private Bridge? _bridge;
    private Db? _db;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += async (_, _) => await InitAsync();
        Closed += (_, _) => _db?.DisposeAsync().AsTask().Wait(2000);
    }

    public static void Log(string msg)
    {
        try { Directory.CreateDirectory(Config.Dir); File.AppendAllText(Path.Combine(Config.Dir, "dashboard.log"), $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {msg}{Environment.NewLine}"); } catch (IOException) { }
    }

    private async Task InitAsync()
    {
        try
        {
            Log("시작");
            var cfg = Config.Load();
            if (cfg is null) { Status.Text = "설정이 없습니다. 수집기 앱의 setup을 먼저 하거나 %LOCALAPPDATA%\\LifeLog\\dashboard\\config.json을 만드세요."; return; }
            _db = new Db(cfg.ConnectionString());
            var ver = await _db.SchemaVersionAsync();
            if (ver > Db.KnownSchemaVersion) { Status.Text = $"DB 코어 스키마({ver})가 이 앱({Db.KnownSchemaVersion})보다 높습니다. 앱을 갱신하세요."; return; }

            var argv = Environment.GetCommandLineArgs();
            if (Array.IndexOf(argv, "--plugins-dir") is var pd && pd >= 0 && pd + 1 < argv.Length) Bridge.PluginsDir = argv[pd + 1];   // 개발용
            if (Array.IndexOf(argv, "--size") is var sz && sz >= 0 && sz + 1 < argv.Length && argv[sz + 1].Split('x') is [var sw, var sh]) { Width = double.Parse(sw); Height = double.Parse(sh); }   // 개발용: 창 크기
            // 개발용: --user-data-dir 경로 → WebView2 프로필 폴더를 바꾼다. 같은 프로필을 쓰는 인스턴스끼리는 브라우저 프로세스를 공유해 가상 호스트 매핑이 섞인다
            var udi = Array.IndexOf(argv, "--user-data-dir");
            var env = await CoreWebView2Environment.CreateAsync(null, udi >= 0 && udi + 1 < argv.Length ? argv[udi + 1] : Path.Combine(Config.Dir, "webview2"));
            await Web.EnsureCoreWebView2Async(env);
            var core = Web.CoreWebView2;
            core.Settings.IsWebMessageEnabled = true;
            core.Settings.AreDevToolsEnabled = true;

            var webDir = Path.Combine(AppContext.BaseDirectory, "web");
            core.SetVirtualHostNameToFolderMapping("app.lifelog", webDir, CoreWebView2HostResourceAccessKind.Allow);
            Bridge.Logger = Log;
            _bridge = new Bridge(_db, cfg, core, this);
            await _bridge.MigrateLocalRulesAsync();
            await _bridge.InstallBundledAsync();
            await _bridge.MapPluginsAsync();
            await core.AddScriptToExecuteOnDocumentCreatedAsync(File.ReadAllText(Path.Combine(webDir, "bridge.js")));
            core.WebMessageReceived += (_, e) => _ = _bridge.HandleAsync(e);
            // 플러그인 iframe의 탐색 실패를 로그로(가상 호스트 매핑 문제 추적)
            core.FrameCreated += (_, fe) =>
            {
                string lastUri = "";
                fe.Frame.NavigationStarting += (_, ns) => lastUri = ns.Uri;
                fe.Frame.NavigationCompleted += (_, ne) =>
                {
                    if (ne.IsSuccess) return;
                    Log($"iframe 탐색 실패: {ne.WebErrorStatus} {lastUri}");
                    // 셸에 알려 그 iframe을 다시 읽게 한다(간헐적 실패 대응)
                    core.PostWebMessageAsJson(JsonSerializer.Serialize(new { lifelog = "frameError", url = lastUri, status = ne.WebErrorStatus.ToString() }));
                };
            };
            Log("WebView2 준비, 브리지 연결");
            Status.Visibility = Visibility.Collapsed;
            // 개발용: --screenshot 경로 → 로드 뒤 4초 후 캡처하고 종료
            var args = Environment.GetCommandLineArgs();
            var si = Array.IndexOf(args, "--screenshot");
            if (si >= 0 && si + 1 < args.Length)
            {
                var path = args[si + 1];
                core.NavigationCompleted += async (_, _) =>
                {
                    var wi = Array.IndexOf(args, "--wait");
                    await Task.Delay(wi >= 0 && wi + 1 < args.Length ? int.Parse(args[wi + 1]) : 4000);
                    // 진단: 매핑된 플러그인 호스트마다 manifest.json을 받아 본다
                    var hosts = string.Join(",", Directory.GetDirectories(Bridge.PluginsDir).Select(d => "'" + Path.GetFileName(d) + "'"));
                    await core.ExecuteScriptAsync($"(async()=>{{const out=[];for(const id of [{hosts}]){{try{{const r=await fetch('https://plugin-'+id+'.lifelog/manifest.json');out.push(id+':'+r.status)}}catch(e){{out.push(id+':ERR '+e.message)}}}}chrome.webview.postMessage({{id:'diag',from:'shell',method:'log',args:['플러그인 호스트 진단: '+out.join(' | ')]}})}})()");
                    await Task.Delay(2000);
                    // 개발용: --jsfile 경로 → 페이지에서 그 스크립트를 실행하고 2초 기다린다(동작 시뮬레이션)
                    var ji = Array.IndexOf(args, "--jsfile");
                    if (ji >= 0 && ji + 1 < args.Length) { await core.ExecuteScriptAsync(File.ReadAllText(args[ji + 1])); await Task.Delay(2500); }
                    if (args.Contains("--scroll-bottom")) { await core.ExecuteScriptAsync("window.scrollTo(0, document.body.scrollHeight)"); await Task.Delay(1500); }
                    await using var fs = File.Create(path);
                    await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, fs);
                    Close();
                };
            }
            // 개발용: --offset N → N일 전 하루를 연다
            var oi = Array.IndexOf(args, "--offset");
            var url = "https://app.lifelog/index.html" + (oi >= 0 && oi + 1 < args.Length ? "?offset=" + Uri.EscapeDataString(args[oi + 1]) : "?offset=0") + (args.Contains("--edit") ? "&edit=1" : "") + (args.Contains("--mobile") ? "&mobile=1" : "") + (Array.IndexOf(args, "--theme") is var ti && ti >= 0 && ti + 1 < args.Length ? "&theme=" + args[ti + 1] : "") + (Array.IndexOf(args, "--page") is var pi && pi >= 0 && pi + 1 < args.Length ? "&page=" + args[pi + 1] : "") + (Array.IndexOf(args, "--sec") is var si2 && si2 >= 0 && si2 + 1 < args.Length ? "&sec=" + args[si2 + 1] : "");
            core.Navigate(url);
        }
        catch (Exception e)
        {
            Status.Text = "시작 실패: " + e.Message;
            Log("시작 실패: " + e);
            if (Environment.GetCommandLineArgs().Contains("--screenshot")) Close();
        }
    }
}
