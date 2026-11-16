using System.Text.Encodings.Web;
using System.Text.Json;
using LifeLog.Collector.Windows.Sources;

namespace LifeLog.Collector.Windows;

/// <summary>블루투스 시험 명령.</summary>
static class BluetoothCli
{
    static readonly JsonSerializerOptions Json = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    /// <summary>지금 페어링된 기기와 링크 상태를 찍고, 초를 주면 그만큼 소스를 돌려 이벤트도 찍는다(DB와 무관).</summary>
    public static async Task<int> Test(string[] args)
    {
        var secs = args.Length > 0 && int.TryParse(args[0], out var n) ? n : 0;
        foreach (var s in await BluetoothSource.ReadAsync()) Console.WriteLine(JsonSerializer.Serialize(s, Json));
        if (secs <= 0) return 0;
        Console.WriteLine($"-- 이벤트 {secs}초 (기기를 켜고 끄거나 블루투스를 껐다 켜 보세요)");
        var src = new BluetoothSource(m => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {m}"));
        src.Start(ev => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {JsonSerializer.Serialize(ev, Json)}"));
        await Task.Delay(TimeSpan.FromSeconds(secs));
        src.Dispose();
        return 0;
    }
}
