using LifeLog.Collector.Windows.Sources.Recording;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 디스코드 출력 녹음(2026-11-11). <b>디스코드 통화 중일 때만</b>(<see cref="DiscordCall.Recording"/>, 봇이 녹음 중이면 쉼) 장치를 연다. 통화가 끝나면 열린 파일을 바로 닫는다.
/// 디스코드가 소리를 내보내는 렌더 장치(가상 케이블 등, 플러그인 win-discord-output 설정 output_device)를
/// WASAPI 루프백으로 계속 듣다가 소리 있는 구간만 파일로 남긴다(<see cref="WasapiSegmentCapture"/>).
/// 구간 규칙은 폰 녹음(collector-android AudioSource.kt)과 같다(<see cref="Segmenter"/>, laptop_mic과 공용).
/// - 루프백은 아무것도 재생되지 않으면 데이터가 오지 않는다. 그동안은 벽시계에 맞춰 무음 프레임을 넣어 끝 판정이 멈추지 않게 한다.
/// - 파일: %USERPROFILE%\SecondBrainRecordings\discord-output\yyyy-MM-dd\yyyyMMdd-HHmmss-xxxxxxxx.m4a(.part → 이름 바꿈, 지우지 않음).
/// - 파일 하나가 끝날 때마다 { start, end, file, bytes, sha256, codec, duration_ms, mean_db, peak_db, floor_db }를 낸다.
/// 장치가 없거나 끊기면 10초마다 다시 붙는다.
/// </summary>
public sealed class DiscordOutputSource : ISource
{
    public string Id => "discord_output";
    public const string Folder = "discord-output";

    const int Frame = Segmenter.Frame;

    private readonly Action<string> _log;
    private Action<Dictionary<string, object?>>? _emit;
    private readonly CancellationTokenSource _cts = new();
    private Thread? _thread;

    public DiscordOutputSource(Action<string> log) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        _thread = new Thread(Run) { IsBackground = true, Name = "discord-output" };
        _thread.Start();
    }

    private void Run()
    {
        string? lastError = null;
        while (!_cts.IsCancellationRequested)
        {
            // 디스코드 통화 중일 때만 장치를 연다(봇이 녹음 중이면 쉰다)
            if (!DiscordCall.WaitForRecording(_cts.Token)) break;
            try { CaptureOnce(); lastError = null; continue; }
            catch (Exception e)
            {
                if (e.Message != lastError) _log($"[discord-output] {e.Message}. 10초 뒤 다시 시도");
                lastError = e.Message;
            }
            _cts.Token.WaitHandle.WaitOne(TimeSpan.FromSeconds(10));
        }
    }

    public static MMDevice FindDevice(MMDeviceEnumerator en, string key)
    {
        var all = en.EnumerateAudioEndPoints(DataFlow.Render, DeviceState.Active).ToList();
        var hit = all.FirstOrDefault(d => d.ID == key) ?? all.FirstOrDefault(d => d.FriendlyName.Contains(key, StringComparison.OrdinalIgnoreCase));
        foreach (var d in all) if (d != hit) d.Dispose();
        return hit ?? throw new InvalidOperationException($"출력 장치 '{key}'가 없습니다(꺼졌거나 이름이 바뀜). `secondbrain-collector discord-devices`로 확인");
    }

    private void CaptureOnce()
    {
        var key = DiscordSettings.Load().OutputDevice;
        if (string.IsNullOrWhiteSpace(key)) throw new InvalidOperationException("장치가 정해지지 않았습니다. 데스크톱 설정 › 플러그인 › 디스코드 출력 녹음에서 고르세요");
        using var en = new MMDeviceEnumerator();
        using var device = FindDevice(en, key);
        var settingsSig = DiscordSettings.Signature;
        WasapiSegmentCapture.Run(device, loopback: true, new Segmenter(Folder, "discord-output", _emit!, _log), "discord-output", _log, _cts.Token, () =>
        {
            if (!DiscordCall.Recording) { _log($"[discord-output] 녹음 멈춤({DiscordCall.Why}). 장치를 닫습니다"); return true; }
            // 장치를 바꾸면(discord-device) 다시 붙는다
            if (DiscordSettings.Signature == settingsSig) return false;
            settingsSig = DiscordSettings.Signature;
            if (DiscordSettings.Load().OutputDevice == key) return false;
            _log("[discord-output] 장치가 바뀌어 다시 붙습니다");
            return true;
        });
    }

    public void Dispose()
    {
        _cts.Cancel();
        _thread?.Join(TimeSpan.FromSeconds(70));   // 열린 파일을 닫을 시간(ffmpeg 마무리)
    }

    /// <summary>
    /// WAV 파일을 실시간 대신 구간 규칙에 흘려 본다(`discord-simulate`, 시험용). 시각은 파일 안 위치로 센다.
    /// 녹음은 SECONDBRAIN_RECORDINGS(없으면 %TEMP%\secondbrain-simulate) 아래에 쓴다.
    /// </summary>
    public static void Simulate(string wav, Action<Dictionary<string, object?>> emit, Action<string> log)
    {
        using var reader = new AudioFileReader(wav);
        ISampleProvider sp = reader;
        if (sp.WaveFormat.Channels > 1) sp = new DownmixToMonoSampleProvider(sp);
        if (sp.WaveFormat.SampleRate != M4aWriter.Rate) sp = new WdlResamplingSampleProvider(sp, M4aWriter.Rate);
        var t0 = DateTimeOffset.Now;
        long frames = 0;
        var seg = new Segmenter(Folder, "discord-output", emit, log, () => t0.AddMilliseconds(frames * 50));
        var buf = new float[Frame];
        var frame = new short[Frame];
        while (sp.Read(buf.AsSpan()) is var n && n > 0)
        {
            for (int i = 0; i < Frame; i++) frame[i] = i < n ? M4aWriter.ToShort(buf[i]) : (short)0;
            frames++;
            seg.Push(frame);
        }
        seg.Close();
    }
}

/// <summary>채널 수와 상관없이 평균으로 모노 다운믹스(HearingAid와 같다. NAudio ToMono는 스테레오 전용).</summary>
sealed class DownmixToMonoSampleProvider : ISampleProvider
{
    private readonly ISampleProvider _src;
    private readonly int _ch;
    private float[] _scratch = [];

    public DownmixToMonoSampleProvider(ISampleProvider src)
    {
        _src = src;
        _ch = Math.Max(1, src.WaveFormat.Channels);
        WaveFormat = WaveFormat.CreateIeeeFloatWaveFormat(src.WaveFormat.SampleRate, 1);
    }

    public WaveFormat WaveFormat { get; }

    public int Read(Span<float> buffer)
    {
        if (_ch == 1) return _src.Read(buffer);
        var need = buffer.Length * _ch;
        if (_scratch.Length < need) _scratch = new float[need];
        var frames = _src.Read(_scratch.AsSpan(0, need)) / _ch;
        for (int i = 0; i < frames; i++)
        {
            float sum = 0;
            for (int c = 0; c < _ch; c++) sum += _scratch[i * _ch + c];
            buffer[i] = sum / _ch;
        }
        return frames;
    }
}
