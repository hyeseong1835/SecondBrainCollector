using LifeLog.Collector.Windows.Sources.Recording;
using NAudio.CoreAudioApi;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 노트북 마이크 상시 녹음(2026-11-11). 디스코드 통화와 상관없이 입력 장치를 WASAPI로 계속 듣다가 소리 있는 구간만 파일로 남긴다.
/// 디스코드 출력 녹음(루프백, 상대 목소리)에는 내 목소리가 없어서, 내 목소리는 이 녹음이 기준이 된다(겹치는 폰 녹음은 분석에서 거른다).
/// - 장치: 플러그인 win-laptop-mic 설정 input_device(ID 또는 이름 일부). 비면 Windows 기본 통신 입력 장치를 쓰고, 기본 장치가 바뀌면 다시 붙는다.
/// - 구간 규칙·기록 형식은 discord_output·폰 녹음과 같다(<see cref="Segmenter"/>, <see cref="WasapiSegmentCapture"/>).
/// - 파일: %USERPROFILE%\SecondBrainRecordings\laptop-mic\yyyy-MM-dd\yyyyMMdd-HHmmss-xxxxxxxx.m4a(지우지 않음).
/// 장치가 없거나 끊기면 10초마다 다시 붙는다.
/// </summary>
public sealed class LaptopMicSource : ISource
{
    public string Id => "laptop_mic";
    public const string Folder = "laptop-mic";
    const string Tag = "laptop-mic";

    private readonly Action<string> _log;
    private Action<Dictionary<string, object?>>? _emit;
    private readonly CancellationTokenSource _cts = new();
    private Thread? _thread;

    public LaptopMicSource(Action<string> log) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        _thread = new Thread(Run) { IsBackground = true, Name = Tag };
        _thread.Start();
    }

    private void Run()
    {
        string? lastError = null;
        while (!_cts.IsCancellationRequested)
        {
            try { CaptureOnce(); lastError = null; }
            catch (Exception e)
            {
                if (e.Message != lastError) _log($"[{Tag}] {e.Message}. 10초 뒤 다시 시도");
                lastError = e.Message;
            }
            _cts.Token.WaitHandle.WaitOne(TimeSpan.FromSeconds(10));
        }
    }

    /// <summary>입력 장치를 ID 또는 이름 일부로 찾는다. 빈 키면 기본 통신 입력 장치.</summary>
    public static MMDevice FindDevice(MMDeviceEnumerator en, string key)
    {
        if (string.IsNullOrWhiteSpace(key))
        {
            try { return en.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications); }
            catch (Exception) { throw new InvalidOperationException("기본 통신 입력 장치(마이크)가 없습니다"); }
        }
        var all = en.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.Active).ToList();
        var hit = all.FirstOrDefault(d => d.ID == key) ?? all.FirstOrDefault(d => d.FriendlyName.Contains(key, StringComparison.OrdinalIgnoreCase));
        foreach (var d in all) if (d != hit) d.Dispose();
        return hit ?? throw new InvalidOperationException($"입력 장치 '{key}'가 없습니다(꺼졌거나 이름이 바뀜). `secondbrain-collector mic-devices`로 확인");
    }

    static string? DefaultId(MMDeviceEnumerator en)
    {
        try { using var d = en.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications); return d.ID; }
        catch { return null; }
    }

    private void CaptureOnce()
    {
        var key = LaptopMicSettings.InputDevice;
        var version = LaptopMicSettings.Version;
        using var en = new MMDeviceEnumerator();
        using var device = FindDevice(en, key);
        var id = device.ID;
        WasapiSegmentCapture.Run(device, loopback: false, new Segmenter(Folder, Tag, _emit!, _log), Tag, _log, _cts.Token, () =>
        {
            if (LaptopMicSettings.Version != version)
            {
                version = LaptopMicSettings.Version;
                if (LaptopMicSettings.InputDevice != key) { _log($"[{Tag}] 장치 설정이 바뀌어 다시 붙습니다"); return true; }
            }
            // 기본 장치를 쓰는 중이면 Windows 기본 통신 입력이 바뀔 때 따라간다
            if (key.Length == 0 && DefaultId(en) is { } now && now != id) { _log($"[{Tag}] 기본 입력 장치가 바뀌어 다시 붙습니다"); return true; }
            return false;
        });
    }

    public void Dispose()
    {
        _cts.Cancel();
        _thread?.Join(TimeSpan.FromSeconds(70));   // 열린 파일을 닫을 시간(ffmpeg 마무리)
    }
}
