using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using LifeLog.Collector.Windows.Sources;

namespace LifeLog.Collector.Windows;

/// <summary>폰 링크 시험·보정 명령.</summary>
static class PhoneLinkCli
{
    public const string Plugin = "win-phone-link";

    /// <summary>링크 소스만 N초 돌려 이벤트를 찍는다(DB와 무관). 상태 파일도 쓰므로 보정 명령과 같이 쓸 수 있다.</summary>
    public static int Test(string[] args)
    {
        var secs = args.Length > 0 && int.TryParse(args[0], out var n) ? n : 60;
        Console.WriteLine($"폰 링크 소스 {secs}초. 폰 수집기(phone-laptop-link)가 켜져 있어야 붙는다");
        var src = new PhoneLinkSource(m => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {m}"));
        src.Start(ev => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {JsonSerializer.Serialize(ev)}"));
        Thread.Sleep(TimeSpan.FromSeconds(secs));
        src.Dispose();
        return 0;
    }

    /// <summary>실행 중인 수집기(또는 phone-link-test)가 쓰는 상태 파일에서 N초 동안 RSSI를 모아 중앙값으로 기준 RSSI를 정한다.</summary>
    public static async Task<int> Calibrate(string[] args)
    {
        var secs = args.Length > 0 && int.TryParse(args[0], out var n) ? n : 30;
        var i = Array.IndexOf(args, "--at");
        var at = i >= 0 && i + 1 < args.Length ? double.Parse(args[i + 1], CultureInfo.InvariantCulture) : 1.0;
        if (at <= 0) throw new ArgumentException("--at은 0보다 커야 합니다");
        var pathLoss = double.TryParse(await DiscordCli.CurrentSettingAsync(Plugin, "path_loss_n"), NumberStyles.Float, CultureInfo.InvariantCulture, out var pl) ? pl : 2.5;
        Console.WriteLine($"폰을 노트북에서 {at}m에 두세요. {secs}초 동안 모읍니다…");
        var since = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var samples = new Dictionary<long, int>();
        var end = Environment.TickCount64 + secs * 1000L;
        while (Environment.TickCount64 < end)
        {
            await Task.Delay(2000);
            try
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(PhoneLinkSource.StatusPath));
                foreach (var r in doc.RootElement.GetProperty("recent").EnumerateArray())
                {
                    var ms = r[0].GetInt64();
                    if (ms >= since) samples[ms] = r[1].GetInt32();
                }
            }
            catch (FileNotFoundException) { Console.WriteLine("상태 파일이 없습니다. 수집기(win-phone-link 켜짐)나 phone-link-test가 돌고 있어야 합니다"); return 1; }
            catch (Exception e) when (e is IOException or JsonException) { }
        }
        if (samples.Count < 5) { Console.WriteLine($"RSSI가 {samples.Count}개뿐입니다. 폰이 붙어 있는지 확인하세요"); return 1; }
        var sorted = samples.Values.Order().ToList();
        var median = sorted[sorted.Count / 2];
        var ref1m = (int)Math.Round(median + 10 * pathLoss * Math.Log10(at));
        Console.WriteLine($"RSSI {samples.Count}개, 중앙값 {median} dBm (최소 {sorted[0]}, 최대 {sorted[^1]}) → 1m 기준 {ref1m} dBm (n={pathLoss})");
        await DiscordCli.SetSettingAsync(Plugin, "ref_rssi_1m", JsonValue.Create(ref1m));
        Console.WriteLine("설정 ref_rssi_1m 저장");
        return 0;
    }
}
