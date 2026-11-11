using System.Text.Json;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 노트북 마이크 녹음 설정(2026-11-11). 원본은 이 기기의 플러그인 설치 설정(DB) win-laptop-mic의 `input_device`(입력 장치 ID 또는 이름 일부).
/// 비어 있으면 Windows 기본 통신 입력 장치를 쓴다. 호스트가 설치 행을 읽은 뒤 <see cref="Apply"/>로 넣는다.
/// </summary>
public static class LaptopMicSettings
{
    public const string Plugin = "win-laptop-mic";

    private static string _device = "";
    private static long _version;

    /// <summary>고른 입력 장치(빈 문자열 = 기본 통신 입력 장치).</summary>
    public static string InputDevice => _device;

    /// <summary>바뀌었는지 비교용.</summary>
    public static long Version => Interlocked.Read(ref _version);

    public static void Apply(JsonElement? settings)
    {
        _device = settings is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty("input_device", out var v) && v.ValueKind == JsonValueKind.String ? (v.GetString() ?? "").Trim() : "";
        Interlocked.Increment(ref _version);
    }
}
