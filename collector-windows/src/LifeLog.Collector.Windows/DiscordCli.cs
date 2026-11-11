using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using LifeLog.Collector.Windows.Sources;
using LifeLog.Collector.Windows.Sources.Recording;
using LifeLog.Core;
using NAudio.CoreAudioApi;

namespace LifeLog.Collector.Windows;

/// <summary>
/// 디스코드 녹음 설정 명령. 값은 플러그인 설치 설정(DB)과 비밀값 저장소에 쓰고 실행 중인 수집기에 다시 읽기를 요청한다.
/// 같은 값을 데스크톱 설정 › 플러그인 화면에서도 바꿀 수 있다. 토큰·사용자 ID는 화면·로그에 내지 않는다.
/// </summary>
static class DiscordCli
{
    /// <summary>렌더(출력) 장치 목록. --json이면 [{ id, name, default }](데스크톱 설정 화면이 장치 고르기에 쓴다).</summary>
    public static async Task<int> Devices(bool json)
    {
        var list = new List<(string Id, string Name, bool Default)>();
        using (var en = new MMDeviceEnumerator())
        {
            string? defId = null;
            try { using var d = en.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia); defId = d.ID; } catch { }
            foreach (var d in en.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active))
                using (d) list.Add((d.ID, d.FriendlyName, d.ID == defId));
        }
        if (json)
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.WriteLine(JsonSerializer.Serialize(list.Select(x => new { id = x.Id, name = x.Name, @default = x.Default })));
            return 0;
        }
        var cur = await CurrentSettingAsync(DiscordSettings.OutputPlugin, "output_device");
        Console.WriteLine("렌더(출력) 장치:");
        foreach (var (id, name, def) in list)
            Console.WriteLine($"{(id == cur ? "* " : "  ")}{name}{(def ? "  (기본)" : "")}\n    {id}");
        Console.WriteLine(string.IsNullOrEmpty(cur) ? "고른 장치 없음. discord-device <이름 일부|ID>" : $"고른 장치: {cur}");
        return 0;
    }

    public static async Task<int> SetDevice(string key)
    {
        key = key.Trim();
        if (key.Length == 0) throw new ArgumentException("장치 이름 일부나 ID가 필요합니다(discord-devices로 확인)");
        string id, name;
        using (var en = new MMDeviceEnumerator())
        using (var d = DiscordOutputSource.FindDevice(en, key)) { id = d.ID; name = d.FriendlyName; }
        // 이름은 언어·드라이버 갱신으로 바뀔 수 있어 ID를 저장한다
        await SetSettingAsync(DiscordSettings.OutputPlugin, "output_device", JsonValue.Create(id));
        Console.WriteLine($"상시 녹음 장치: {name}\n    {id}");
        return 0;
    }

    /// <summary>입력(마이크) 장치 목록. --json이면 [{ id, name, default }](default = 기본 통신 입력 장치).</summary>
    public static async Task<int> MicDevices(bool json)
    {
        var list = new List<(string Id, string Name, bool Default)>();
        using (var en = new MMDeviceEnumerator())
        {
            string? defId = null;
            try { using var d = en.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications); defId = d.ID; } catch { }
            foreach (var d in en.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active))
                using (d)
                {
                    var name = d.FriendlyName;
                    if (!json)
                        try { var v = d.AudioEndpointVolume; name += $"  [볼륨 {v.MasterVolumeLevelScalar * 100:F0}%{(v.Mute ? ", 음소거" : "")}]"; } catch { }
                    list.Add((d.ID, name, d.ID == defId));
                }
        }
        if (json)
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.WriteLine(JsonSerializer.Serialize(list.Select(x => new { id = x.Id, name = x.Name, @default = x.Default })));
            return 0;
        }
        var cur = await CurrentSettingAsync(LaptopMicSettings.Plugin, "input_device");
        Console.WriteLine("입력(마이크) 장치:");
        foreach (var (id, name, def) in list)
            Console.WriteLine($"{(id == cur ? "* " : "  ")}{name}{(def ? "  (기본 통신)" : "")}\n    {id}");
        Console.WriteLine(string.IsNullOrEmpty(cur) ? "고른 장치 없음: 기본 통신 입력 장치를 씁니다. 바꾸기: mic-device <이름 일부|ID>" : $"고른 장치: {cur}");
        return 0;
    }

    public static async Task<int> SetMicDevice(string key)
    {
        key = key.Trim();
        if (key.Length == 0) throw new ArgumentException("장치 이름 일부나 ID, 또는 default가 필요합니다(mic-devices로 확인)");
        if (key == "default")
        {
            await SetSettingAsync(LaptopMicSettings.Plugin, "input_device", JsonValue.Create(""));
            Console.WriteLine("마이크 녹음 장치: Windows 기본 통신 입력 장치");
            return 0;
        }
        string id, name;
        using (var en = new MMDeviceEnumerator())
        using (var d = LaptopMicSource.FindDevice(en, key)) { id = d.ID; name = d.FriendlyName; }
        await SetSettingAsync(LaptopMicSettings.Plugin, "input_device", JsonValue.Create(id));
        Console.WriteLine($"마이크 녹음 장치: {name}\n    {id}");
        return 0;
    }

    /// <summary>시험용: 노트북 마이크 소스만 N초 돌려 생긴 파일 기록을 찍는다. 행은 내지 않는다(DB·실행 중인 수집기와 무관).
    /// 장치는 --device(없으면 기본 통신 입력). 녹음 위치는 SECONDBRAIN_RECORDINGS(없으면 평소 위치).</summary>
    public static int MicTest(string[] args)
    {
        var secs = args.Length > 0 && int.TryParse(args[0], out var n) ? n : 60;
        var i = Array.IndexOf(args, "--device");
        LaptopMicSettings.Apply(JsonDocument.Parse(JsonSerializer.Serialize(new { input_device = i >= 0 && i + 1 < args.Length ? args[i + 1] : "" })).RootElement);
        Console.WriteLine($"녹음 위치: {Path.Combine(RecordingPaths.Root, LaptopMicSource.Folder)}, {secs}초");
        double max = -120, sum = 0; int cnt = 0; long last = Environment.TickCount64;
        if (args.Contains("--levels"))
            WasapiSegmentCapture.Level = (_, db) =>
            {
                max = Math.Max(max, db); sum += db; cnt++;
                if (Environment.TickCount64 - last < 5000) return;
                Console.WriteLine($"{DateTime.Now:HH:mm:ss} 레벨 5초: 평균 {sum / cnt:F1} dBFS, 최대 {max:F1} dBFS");
                max = -120; sum = 0; cnt = 0; last = Environment.TickCount64;
            };
        var src = new LaptopMicSource(m => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {m}"));
        src.Start(ev => Console.WriteLine(JsonSerializer.Serialize(ev)));
        Thread.Sleep(TimeSpan.FromSeconds(secs));
        src.Dispose();
        return 0;
    }

    public static async Task<int> Token()
    {
        if (Console.IsInputRedirected) throw new InvalidOperationException("터미널에서 직접 실행하세요(표준 입력 리디렉션은 받지 않습니다). 데스크톱 설정 › 플러그인에서도 넣을 수 있습니다");
        Console.WriteLine("디스코드 봇 토큰과 따라갈 사용자 ID를 넣습니다. 입력한 글자는 화면에 보이지 않습니다. 토큰은 DPAPI(이 Windows 사용자)로 암호화해 이 PC에만 저장합니다.");
        var token = ReadHidden("봇 토큰: ").Trim();
        if (token.Length < 20) throw new ArgumentException("토큰이 너무 짧습니다");
        var idText = ReadHidden("따라갈 사용자 ID(숫자): ").Trim();
        if (!ulong.TryParse(idText, out _) || idText.Length < 15) throw new ArgumentException("사용자 ID는 17~20자리 숫자입니다");
        SecretStore.Set(DiscordSettings.BotPlugin, DiscordSettings.TokenKey, token);
        await SetSettingAsync(DiscordSettings.BotPlugin, "target_user_id", JsonValue.Create(idText));
        Console.WriteLine("저장했습니다. 켜기: discord-bot on, 트레이 메뉴 '디스코드 봇 녹음' 또는 데스크톱 설정 › 플러그인");
        return 0;
    }

    public static async Task<int> Bot(string arg)
    {
        if (arg is not ("on" or "off"))
        {
            var on = await CurrentSettingAsync(DiscordSettings.BotPlugin, "enabled");
            Console.WriteLine($"봇 녹음 {(on == "true" ? "켬" : "끔")}, 토큰 {(SecretStore.Has(DiscordSettings.BotPlugin, DiscordSettings.TokenKey) ? "있음" : "없음")}");
            return 0;
        }
        await SetSettingAsync(DiscordSettings.BotPlugin, "enabled", JsonValue.Create(arg == "on"));
        Console.WriteLine(arg == "on" ? "봇 녹음 켬" : "봇 녹음 끔");
        return 0;
    }

    /// <summary>이 기기의 설치 설정 한 칸을 DB에 쓰고 실행 중인 수집기에 다시 읽기를 요청한다.</summary>
    static async Task SetSettingAsync(string plugin, string key, JsonNode value)
    {
        var cfg = Config.Load() ?? throw new InvalidOperationException("setup을 먼저 하세요");
        await using var db = Db.Open(cfg);
        var inst = await db.GetInstallAsync(plugin, cfg.Device) ?? throw new InvalidOperationException($"{plugin}가 이 기기에 설치되지 않았습니다");
        var o = JsonNode.Parse(inst.Settings.ValueKind == JsonValueKind.Object ? inst.Settings.GetRawText() : "{}")!.AsObject();
        o[key] = value;
        await db.SetInstallAsync(plugin, cfg.Device, inst.InstalledVersion, inst.Enabled, o.ToJsonString(), JsonSerializer.Serialize(inst.Granted));
        if (Cli.Reload()) Console.WriteLine("실행 중인 수집기에 다시 읽기를 요청했습니다");
    }

    static async Task<string?> CurrentSettingAsync(string plugin, string key)
    {
        var cfg = Config.Load();
        if (cfg is null) return null;
        await using var db = Db.Open(cfg);
        var inst = await db.GetInstallAsync(plugin, cfg.Device);
        if (inst is null || inst.Settings.ValueKind != JsonValueKind.Object || !inst.Settings.TryGetProperty(key, out var v)) return null;
        return v.ValueKind == JsonValueKind.String ? v.GetString() : v.GetRawText();
    }

    /// <summary>시험용: WAV를 상시 녹음의 구간 규칙에 흘려 어떤 파일이 생기는지 본다. 행은 내지 않는다.</summary>
    public static int Simulate(string wav)
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SECONDBRAIN_RECORDINGS")))
            Environment.SetEnvironmentVariable("SECONDBRAIN_RECORDINGS", Path.Combine(Path.GetTempPath(), "secondbrain-simulate"));
        Console.WriteLine("녹음 위치: " + RecordingPaths.Root);
        DiscordOutputSource.Simulate(wav, ev => Console.WriteLine(JsonSerializer.Serialize(ev)), Console.WriteLine);
        return 0;
    }

    private static string ReadHidden(string prompt)
    {
        Console.Write(prompt);
        var sb = new StringBuilder();
        while (true)
        {
            var k = Console.ReadKey(intercept: true);
            if (k.Key == ConsoleKey.Enter) break;
            if (k.Key == ConsoleKey.Backspace) { if (sb.Length > 0) sb.Length--; continue; }
            if (!char.IsControl(k.KeyChar)) sb.Append(k.KeyChar);
        }
        Console.WriteLine();
        return sb.ToString();
    }
}
