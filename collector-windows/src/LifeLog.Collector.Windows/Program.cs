using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using LifeLog.Core;
using LifeLog.Collector.Windows;
using Npgsql;

// 처리되지 않은 예외로 꺼질 때 로그에 남긴다(2026-11-14: 로그 없이 꺼진 일을 좇다가)
AppDomain.CurrentDomain.UnhandledException += (_, e) =>
    Host.AppendLog($"처리되지 않은 예외로 종료: {e.ExceptionObject}");
TaskScheduler.UnobservedTaskException += (_, e) =>
{
    Host.AppendLog($"관찰되지 않은 작업 예외: {e.Exception.GetBaseException().Message}");
    e.SetObserved();
};
return await Cli.RunAsync(args);

static class Cli
{
    const string Usage = """
        secondbrain-collector <명령>   (SecondBrain Collector. 2026-11-11 lifelog-collector에서 이름 바꿈)

          setup   --device ID --name 이름 --host H --port P --database DB [--no-ssl] [--tenant 프로젝트ref]
                  --tenant: Supabase 풀러는 사용자 이름이 롤.프로젝트ref 꼴이어야 한다. 맨 Postgres면 생략
          setup-reset  같은 인자. 이미 등록된 기기의 비밀번호를 다시 받아 설정한다
                  관리자 접속 문자열은 환경 변수 LIFELOG_ADMIN_DSN 또는 표준 입력으로 받는다(저장하지 않음).
                  기기 롤을 만들고 비밀번호를 DPAPI로 보호해 config.json에 둔다.
          use-api --url URL   DB 직접 연결 대신 SecondBrainApi를 거친다(SecondBrain DB 이전 7단계). 키(종류 collector, 이름 = 기기 id)는
                  환경 변수 LIFELOG_API_KEY 또는 표준 입력으로 받아 DPAPI로 저장한다. --url ""면 다시 DB 직접 연결로
          install <플러그인 폴더 또는 zip> [--grant-all]
                  plugins/<id>로 복사하고 등록한다. --grant-all은 매니페스트의 권한을 전부 허용하고 켠다(개발용).
          enable <id> | disable <id>
          run [--tray]   수집 시작. --tray면 콘솔을 숨기고 트레이 아이콘으로 돈다
          stop           실행 중인 수집기를 정상 종료시킨다(열린 구간을 닫고 큐를 비운다)
          reload             실행 중인 수집기에 플러그인 다시 읽기 요청
          autostart on|off|status   로그온 시 자동 실행(시작프로그램 폴더의 "SecondBrain 수집기" 바로가기)
          status  설정·큐·플러그인 상태

          디스코드 녹음(플러그인 win-discord-output·win-discord-bot. 데스크톱 설정 › 플러그인에서도 바꾼다):
          discord-devices [--json] 렌더(출력) 장치 목록과 지금 고른 장치
          discord-device <이름 일부|ID>   상시 녹음할 장치를 정한다(루프백). 실행 중이면 10초 안에 다시 붙는다
          discord-token            봇 토큰과 따라갈 사용자 ID를 직접 입력해 DPAPI로 저장(화면에 표시하지 않음)
          discord-bot off|on|auto  봇 녹음 꺼짐/켜짐(어디든)/자동(등록한 서버·채널만, 목록은 데스크톱 녹음 화면). 트레이 메뉴와 같다

          discord-call [초]        디스코드 통화 감지 확인(Discord 오디오 세션과 판정을 2초마다)

          노트북 마이크 녹음(플러그인 win-laptop-mic). 디스코드 출력 녹음과 함께 디스코드 통화 중일 때만, 봇이 녹음 중이면 쉰다:
          mic-devices [--json]     입력 장치 목록과 지금 고른 장치
          mic-device <이름 일부|ID|default>   녹음할 입력 장치. default면 Windows 기본 통신 입력 장치를 따른다
          mic-test [초] [--device 이름] [--levels]   시험용: 마이크 소스만 돌려(통화 중일 때만 연다) 생긴 파일 기록을 찍는다(행은 안 냄). --levels는 5초마다 입력 레벨

          폰 링크(플러그인 win-phone-link. 폰 수집기가 BLE로 붙어 RSSI·시계 차이를 보낸다):
          phone-link-test [초]      시험용: 링크 소스만 돌려 이벤트를 찍는다(실행 중인 수집기에 win-phone-link가 켜져 있으면 끄고 할 것)
          phone-link-calibrate [초] [--at 거리m]   폰을 노트북에서 거리m(기본 1)에 두고 실행. 실행 중인 수집기가 받은 RSSI 중앙값으로
                                   기준 RSSI(ref_rssi_1m)를 정한다

        로그: %LOCALAPPDATA%\SecondBrain\collector\collector.log (옛 %LOCALAPPDATA%\LifeLog\collector는 처음 실행 때 옮긴다)
        """;

    const string StopEventName = @"Local\SecondBrain.Collector.Stop";
    const string MutexName = @"Local\SecondBrain.Collector.Running";
    const string ReloadEventName = @"Local\SecondBrain.Collector.Reload";   // 데스크톱(LifeLogBridge)도 이 이름을 쓴다

    public static async Task<int> RunAsync(string[] args)
    {
        if (args.Length == 0) { Console.WriteLine(Usage); return 1; }
        try
        {
            Config.MoveFromOldRoot();
            switch (args[0])
            {
                case "setup": return await SetupAsync(args[1..], reset: false);
                case "setup-reset": return await SetupAsync(args[1..], reset: true);
                case "use-api": return await UseApiAsync(args[1..]);
                case "install": return await InstallAsync(args[1..]);
                case "enable": return await EnableAsync(args[1], true);
                case "disable": return await EnableAsync(args[1], false);
                case "run": return await RunHostAsync(args.Contains("--tray"));
                case "stop": return Stop();
                case "reload": Console.WriteLine(Reload() ? "실행 중인 수집기에 플러그인 다시 읽기를 요청했습니다" : "실행 중인 수집기가 없습니다"); return 0;
                case "autostart": return Autostart.Run(args.Length > 1 ? args[1] : null);
                case "set-tenant": return SetTenant(args.Length > 1 ? args[1] : "");
                case "status": return await StatusAsync();
                case "discord-devices": return await DiscordCli.Devices(args.Contains("--json"));
                case "discord-device": return await DiscordCli.SetDevice(string.Join(' ', args[1..]));
                case "discord-token": return await DiscordCli.Token();
                case "discord-simulate": return DiscordCli.Simulate(args[1]);
                case "discord-bot": return await DiscordCli.Bot(args.Length > 1 ? args[1] : "");
                case "mic-devices": return await DiscordCli.MicDevices(args.Contains("--json"));
                case "mic-device": return await DiscordCli.SetMicDevice(string.Join(' ', args[1..]));
                case "discord-call": return DiscordCli.CallStatus(args[1..]);
                case "mic-test": return DiscordCli.MicTest(args[1..]);
                case "phone-link-test": return PhoneLinkCli.Test(args[1..]);
                case "phone-link-calibrate": return await PhoneLinkCli.Calibrate(args[1..]);
                default: Console.WriteLine(Usage); return 1;
            }
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("오류: " + e.Message);
            // run --tray는 콘솔이 숨겨져 있어 로그에도 남긴다
            if (args[0] == "run") Host.AppendLog($"수집기 종료(오류): {e}");
            return 2;
        }
    }

    static string? Opt(string[] a, string name) { var i = Array.IndexOf(a, name); return i >= 0 && i + 1 < a.Length ? a[i + 1] : null; }

    /// <summary>reset=true면 이미 등록된 기기의 비밀번호를 reset_device_password로 다시 받는다(이관 도구가 먼저 등록한 경우 등).</summary>
    static async Task<int> SetupAsync(string[] a, bool reset)
    {
        var device = Opt(a, "--device") ?? throw new ArgumentException("--device가 필요합니다");
        var name = Opt(a, "--name") ?? device;
        var host = Opt(a, "--host") ?? "localhost";
        var port = int.Parse(Opt(a, "--port") ?? "5432");
        var db = Opt(a, "--database") ?? "postgres";
        var ssl = !a.Contains("--no-ssl");
        var admin = Environment.GetEnvironmentVariable("LIFELOG_ADMIN_DSN");
        if (string.IsNullOrEmpty(admin))
        {
            Console.Write("관리자 접속 문자열(저장하지 않음): ");
            admin = Console.ReadLine();
        }
        if (string.IsNullOrEmpty(admin)) throw new ArgumentException("관리자 접속 문자열이 없습니다");

        string pw;
        await using (var conn = new NpgsqlConnection(admin))
        {
            await conn.OpenAsync();
            await using var cmd = reset
                ? new NpgsqlCommand("SELECT lifelog.reset_device_password($1)", conn)
                : new NpgsqlCommand("SELECT lifelog.register_device($1, $2, $3)", conn);
            cmd.Parameters.Add(new NpgsqlParameter { Value = device });
            if (!reset)
            {
                cmd.Parameters.Add(new NpgsqlParameter { Value = name });
                cmd.Parameters.Add(new NpgsqlParameter { Value = "Windows " + Environment.OSVersion.Version });
            }
            pw = (string)(await cmd.ExecuteScalarAsync())!;
        }
        var cfg = new Config { Device = device, Host = host, Port = port, Database = db, Ssl = ssl, Tenant = Opt(a, "--tenant") ?? "" };
        cfg.SetPassword(pw);
        cfg.Save();
        Console.WriteLine($"기기 {device} 등록 완료. 설정: {Config.Dir}");
        return 0;
    }

    static async Task<int> UseApiAsync(string[] a)
    {
        var cfg = Config.Load() ?? new Config();
        var url = Opt(a, "--url") ?? throw new ArgumentException("--url이 필요합니다");
        if (url.Length == 0)
        {
            cfg.ApiUrl = ""; cfg.ApiKeyProtected = ""; cfg.Save();
            Console.WriteLine("DB 직접 연결로 되돌렸습니다");
            return 0;
        }
        var key = Environment.GetEnvironmentVariable("LIFELOG_API_KEY");
        if (string.IsNullOrEmpty(key)) { Console.Error.Write("API 키(sbk_…): "); key = Console.ReadLine(); }
        if (string.IsNullOrWhiteSpace(key)) throw new ArgumentException("API 키가 없습니다");
        cfg.ApiUrl = url.TrimEnd('/');
        cfg.SetApiKey(key.Trim());
        await using (var db = Db.Open(cfg))
        {
            var v = await db.SchemaVersionAsync();
            Console.WriteLine($"API 연결 확인: lifelog 스키마 {v}");
        }
        cfg.Save();
        Console.WriteLine($"API 모드로 저장했습니다({cfg.ApiUrl}). 실행 중이면 stop 후 다시 run");
        return 0;
    }

    static async Task<int> InstallAsync(string[] a)
    {
        var src = a.Length > 0 ? a[0] : throw new ArgumentException("플러그인 폴더·zip·GitHub URL이 필요합니다");
        var grantAll = a.Contains("--grant-all");
        var cfg = Config.Load() ?? throw new InvalidOperationException("setup을 먼저 하세요");
        await using var db = Db.Open(cfg);
        var r = await PluginInstaller.InstallAsync(db, cfg.Device, Config.PluginsDir, src, grantAll, "collector");
        Console.WriteLine($"등록: {r.Registered}");
        Console.WriteLine(r.Install is { } inst ? $"켬(권한 전부 허용): {inst}" : $"설치됨. 권한을 허용하고 켜려면: enable {r.Manifest.Id}");
        if (Reload()) Console.WriteLine("실행 중인 수집기에 다시 읽기를 요청했습니다");
        return 0;
    }

    static async Task<int> EnableAsync(string id, bool on)
    {
        var cfg = Config.Load() ?? throw new InvalidOperationException("setup을 먼저 하세요");
        var m = Manifest.Load(Path.Combine(Config.PluginsDir, id, "manifest.json"));
        await using var db = Db.Open(cfg);
        var inst = await db.GetInstallAsync(id, cfg.Device) ?? throw new InvalidOperationException("설치되지 않은 플러그인입니다");
        var granted = on ? JsonSerializer.Serialize(m.Capabilities.Select(c => c.Id)) : JsonSerializer.Serialize(inst.Granted);
        var row = await db.SetInstallAsync(id, cfg.Device, inst.InstalledVersion, on, inst.Settings.GetRawText(), granted);
        Console.WriteLine(row);
        return 0;
    }

    static async Task<int> RunHostAsync(bool tray)
    {
        var cfg = Config.Load() ?? throw new InvalidOperationException("setup을 먼저 하세요");
        using var single = new Mutex(true, MutexName, out var first);
        if (!first) { Console.Error.WriteLine("이미 실행 중입니다. `stop`으로 끝내세요."); return 3; }
        Host.AppendLog($"수집기 시작(pid {Environment.ProcessId}{(tray ? ", 트레이" : "")})");
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
        using var stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset, StopEventName);
        using var reloadEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ReloadEventName);
        ThreadPool.RegisterWaitForSingleObject(stopEvent, (_, _) => stop.Cancel(), null, -1, true);
        var status = "시작 중";
        using var trayIcon = tray ? new Tray(() => status, () => stop.Cancel()) : null;
        trayIcon?.Start();
        // reload 신호(install·대시보드)가 오면 호스트만 새로 만든다. 뮤텍스·트레이는 유지.
        // 호스트가 실패하면(부팅 직후 네트워크·API가 아직 없음 등) 끝내지 않고 로그를 남긴 뒤 기다렸다 다시 띄운다.
        // 예전에는 여기서 예외가 나면 숨은 콘솔에만 쓰고 끝나, 재부팅 뒤 자동 실행이 로그 없이 꺼져 있었다(2026-11-14).
        var failures = 0;
        while (!stop.IsCancellationRequested)
        {
            using var round = CancellationTokenSource.CreateLinkedTokenSource(stop.Token);
            var reloadWait = ThreadPool.RegisterWaitForSingleObject(reloadEvent, (_, _) => round.Cancel(), null, -1, true);
            var startedAt = DateTime.UtcNow;
            try
            {
                await using var host = new Host(cfg);
                using var statusTimer = new System.Threading.Timer(_ => status = host.Status, null, 0, 1000);
                await host.RunAsync(round.Token);
                failures = 0;
            }
            catch (Exception e) when (!stop.IsCancellationRequested)
            {
                // 오래 잘 돌다가 난 실패면 처음부터 짧게 기다린다
                if (DateTime.UtcNow - startedAt > TimeSpan.FromMinutes(10)) failures = 0;
                var wait = RetryDelay(failures++);
                status = $"오류 · {wait.TotalSeconds:F0}초 뒤 다시: {e.Message}";
                Host.AppendLog($"수집 실패: {e.GetBaseException().Message}. {wait.TotalSeconds:F0}초 뒤 다시 시도");
                try { await Task.Delay(wait, round.Token); } catch (OperationCanceledException) { }
            }
            finally { reloadWait.Unregister(null); }
            if (!stop.IsCancellationRequested) { Console.WriteLine("플러그인 다시 읽기"); cfg = Config.Load() ?? cfg; }
        }
        return 0;
    }

    /// <summary>호스트 재시도 간격: 15초, 30초, 1분, 2분, 4분, 이후 5분.</summary>
    internal static TimeSpan RetryDelay(int failures) => TimeSpan.FromSeconds(Math.Min(300, 15 * Math.Pow(2, Math.Min(failures, 5))));

    static int SetTenant(string tenant)
    {
        var cfg = Config.Load() ?? throw new InvalidOperationException("setup을 먼저 하세요");
        cfg.Tenant = tenant;
        cfg.Save();
        Console.WriteLine(string.IsNullOrEmpty(tenant) ? "테넌트 접미사 없음" : $"접속 사용자: {cfg.User}");
        return 0;
    }

    /// <summary>실행 중인 수집기에 플러그인 다시 읽기를 요청한다. 없으면 false.</summary>
    internal static bool Reload()
    {
        if (!EventWaitHandle.TryOpenExisting(ReloadEventName, out var ev)) return false;
        using (ev) ev.Set();
        return true;
    }

    static int Stop()
    {
        if (!EventWaitHandle.TryOpenExisting(StopEventName, out var ev)) { Console.WriteLine("실행 중인 수집기가 없습니다"); return 1; }
        ev.Set();
        // 뮤텍스가 풀릴 때까지(= 정상 종료) 기다린다
        for (int i = 0; i < 60; i++)
        {
            if (!Mutex.TryOpenExisting(MutexName, out var m)) { Console.WriteLine("종료됨"); return 0; }
            m.Dispose();
            Thread.Sleep(500);
        }
        Console.WriteLine("30초 안에 끝나지 않았습니다. 로그를 보세요");
        return 2;
    }

    static async Task<int> StatusAsync()
    {
        var cfg = Config.Load();
        if (cfg is null) { Console.WriteLine("설정 없음. setup을 먼저 하세요"); return 1; }
        Console.WriteLine(cfg.UsesApi ? $"기기 {cfg.Device}  API {cfg.ApiUrl}" : $"기기 {cfg.Device}  DB {cfg.Host}:{cfg.Port}/{cfg.Database}  사용자 {cfg.User}");
        using (var q = new Queue(Config.QueuePath)) Console.WriteLine($"큐 대기 {q.Count()}건");
        if (Directory.Exists(Config.PluginsDir))
        {
            await using var db = Db.Open(cfg);
            foreach (var dir in Directory.GetDirectories(Config.PluginsDir))
            {
                var m = Manifest.Load(Path.Combine(dir, "manifest.json"));
                var inst = await db.GetInstallAsync(m.Id, cfg.Device);
                Console.WriteLine($"  {m.Id} {m.Version}  {(inst is null ? "미등록" : inst.Enabled ? "켜짐" : "꺼짐")}  권한 {(inst is null ? "-" : string.Join(",", inst.Granted))}");
                foreach (var o in m.Outputs)
                {
                    var n = await db.CountTodayAsync(m.Id + "/" + o.Name, o.IsSpan, cfg.Device);
                    Console.WriteLine($"      {o.Name}: 오늘 {n}행");
                }
            }
        }
        return 0;
    }
}
