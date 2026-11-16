using System.Text.Encodings.Web;
using System.Text.Json;
using LifeLog.Collector.Windows.Sources;

namespace LifeLog.Collector.Windows;

/// <summary>오디오 장치 시험 명령(DB 없이).</summary>
static class AudioDeviceCli
{
    static readonly JsonSerializerOptions Json = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    /// <summary>지금 장치·기본 장치를 찍고, 초가 있으면 그 동안 소스 이벤트를 한 줄 JSON으로 따라 찍는다(--ndjson이면 이벤트만, 시험 스크립트가 플러그인에 먹이는 용도).</summary>
    public static int Test(string[] args)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        var ndjson = args.Contains("--ndjson");
        var secs = args.Select(a => int.TryParse(a, out var n) ? n : -1).FirstOrDefault(n => n >= 0);
        if (!ndjson) Console.WriteLine(JsonSerializer.Serialize(AudioDeviceSource.Snapshot(), new JsonSerializerOptions(Json) { WriteIndented = true }));
        if (secs <= 0) return 0;
        if (!ndjson) Console.WriteLine($"소스 이벤트 {secs}초(장치를 꽂고 빼거나 기본 장치를 바꿔 보세요):");
        var src = new AudioDeviceSource(m => Console.Error.WriteLine(m));
        src.Start(ev => { lock (Json) Console.WriteLine(JsonSerializer.Serialize(ev, Json)); });
        Thread.Sleep(TimeSpan.FromSeconds(secs));
        src.Dispose();
        return 0;
    }
}
