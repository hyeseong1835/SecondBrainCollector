using System.Text;
using LifeLog.Collector.Windows.Sources;
using LifeLog.Collector.Windows.Sources.Recording;
using NAudio.CoreAudioApi;

namespace LifeLog.Collector.Windows;

/// <summary>디스코드 녹음 설정 명령. 토큰·사용자 ID는 화면·로그에 내지 않는다.</summary>
static class DiscordCli
{
    public static int Devices()
    {
        var cur = DiscordSettings.Load().OutputDevice;
        using var en = new MMDeviceEnumerator();
        string? defId = null;
        try { using var d = en.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia); defId = d.ID; } catch { }
        Console.WriteLine("렌더(출력) 장치:");
        foreach (var d in en.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active))
        {
            using (d)
            {
                var mark = (d.ID == cur || (cur.Length > 0 && d.FriendlyName.Contains(cur, StringComparison.OrdinalIgnoreCase))) ? "* " : "  ";
                Console.WriteLine($"{mark}{d.FriendlyName}{(d.ID == defId ? "  (기본)" : "")}\n    {d.ID}");
            }
        }
        Console.WriteLine(cur.Length == 0 ? "고른 장치 없음. discord-device <이름 일부|ID>" : $"고른 장치: {cur}");
        return 0;
    }

    public static int SetDevice(string key)
    {
        key = key.Trim();
        if (key.Length == 0) throw new ArgumentException("장치 이름 일부나 ID가 필요합니다(discord-devices로 확인)");
        using var en = new MMDeviceEnumerator();
        using var d = DiscordOutputSource.FindDevice(en, key);
        // 이름은 언어·드라이버 갱신으로 바뀔 수 있어 ID를 저장한다
        DiscordSettings.Update(s => s.OutputDevice = d.ID);
        Console.WriteLine($"상시 녹음 장치: {d.FriendlyName}\n    {d.ID}");
        return 0;
    }

    public static int Token()
    {
        if (Console.IsInputRedirected) throw new InvalidOperationException("터미널에서 직접 실행하세요(표준 입력 리디렉션은 받지 않습니다)");
        Console.WriteLine("디스코드 봇 토큰과 따라갈 사용자 ID를 넣습니다. 입력한 글자는 화면에 보이지 않습니다. DPAPI(이 Windows 사용자)로 암호화해 저장합니다.");
        var token = ReadHidden("봇 토큰: ").Trim();
        if (token.Length < 20) throw new ArgumentException("토큰이 너무 짧습니다");
        var idText = ReadHidden("따라갈 사용자 ID(숫자): ").Trim();
        if (!ulong.TryParse(idText, out var id) || idText.Length < 15) throw new ArgumentException("사용자 ID는 17~20자리 숫자입니다");
        DiscordSettings.Update(s => s.SetBotCredentials(token, id));
        Console.WriteLine("저장했습니다. 켜기: discord-bot on 또는 트레이 메뉴 '디스코드 봇 녹음'");
        return 0;
    }

    public static int Bot(string arg)
    {
        if (arg is not ("on" or "off")) { var s = DiscordSettings.Load(); Console.WriteLine($"봇 녹음 {(s.BotEnabled ? "켬" : "끔")}, 토큰 {(s.HasBotCredentials ? "있음" : "없음")}"); return 0; }
        DiscordSettings.Update(s => s.BotEnabled = arg == "on");
        Console.WriteLine(arg == "on" ? "봇 녹음 켬(실행 중인 수집기가 몇 초 안에 반영)" : "봇 녹음 끔");
        return 0;
    }

    /// <summary>시험용: WAV를 상시 녹음의 구간 규칙에 흘려 어떤 파일이 생기는지 본다. 행은 내지 않는다.</summary>
    public static int Simulate(string wav)
    {
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SECONDBRAIN_RECORDINGS")))
            Environment.SetEnvironmentVariable("SECONDBRAIN_RECORDINGS", Path.Combine(Path.GetTempPath(), "secondbrain-simulate"));
        Console.WriteLine("녹음 위치: " + RecordingPaths.Root);
        DiscordOutputSource.Simulate(wav, ev => Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(ev)), Console.WriteLine);
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
