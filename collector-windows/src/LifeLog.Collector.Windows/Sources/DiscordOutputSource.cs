using LifeLog.Collector.Windows.Sources.Recording;
using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 디스코드 출력 상시 녹음(2026-11-11). 디스코드가 소리를 내보내는 렌더 장치(가상 케이블 등, discord.json의 output_device)를
/// WASAPI 루프백으로 계속 듣다가 소리 있는 구간만 파일로 남긴다. 구간 규칙은 폰 녹음(collector-android AudioSource.kt)과 같다.
///
/// - 16kHz 모노로 바꿔 50ms 프레임마다 dBFS를 잰다. 소음 바닥은 조용하면 빨리 내려가고 시끄러우면 천천히 오른다.
/// - 바닥+10dB가 200ms 이어지면 시작(앞 1초를 붙임), 바닥+6dB 아래가 5초 이어지면 끝, 30분이면 나눈다.
/// - 루프백은 아무것도 재생되지 않으면 데이터가 오지 않는다. 그동안은 벽시계에 맞춰 무음 프레임을 넣어 끝 판정이 멈추지 않게 한다.
/// - 파일: %USERPROFILE%\SecondBrainRecordings\discord-output\yyyy-MM-dd\yyyyMMdd-HHmmss-xxxxxxxx.m4a(.part → 이름 바꿈, 지우지 않음).
/// - 파일 하나가 끝날 때마다 { start, end, file, bytes, sha256, codec, duration_ms, mean_db, peak_db, floor_db }를 낸다.
/// 장치가 없거나 끊기면 10초마다 다시 붙는다.
/// </summary>
public sealed class DiscordOutputSource : ISource
{
    public string Id => "discord_output";
    public const string Folder = "discord-output";

    const int Frame = M4aWriter.Rate / 20;       // 50ms
    const double StartDb = 10, KeepDb = 6;
    const int StartFrames = 4, StopFrames = 100, PrerollFrames = 20, MaxFrames = 20 * 60 * 30;

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
            try { CaptureOnce(); lastError = null; }
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
        if (string.IsNullOrWhiteSpace(key)) throw new InvalidOperationException("장치가 정해지지 않았습니다. `secondbrain-collector discord-device <이름>`");
        using var en = new MMDeviceEnumerator();
        using var device = FindDevice(en, key);
        using var recorder = new WasapiRecorderBuilder().WithLoopbackCapture().WithDevice(device).Build();
        var buffer = new BufferedWaveProvider(recorder.WaveFormat, TimeSpan.FromSeconds(10)) { DiscardOnBufferOverflow = true, ReadFully = false };
        ISampleProvider samples = buffer.ToSampleProvider();
        if (samples.WaveFormat.Channels > 1) samples = new DownmixToMonoSampleProvider(samples);
        var resampled = new WdlResamplingSampleProvider(samples, M4aWriter.Rate);
        Exception? stopped = null;
        var gotData = 0L;   // 마지막으로 데이터가 온 시각(Environment.TickCount64)
        recorder.DataAvailable += (data, flags, _, _) =>
        {
            try
            {
                if ((flags & AudioClientBufferFlags.Silent) != 0) buffer.AddSamples(new byte[data.Length]);
                else buffer.AddSamples(data);
                Interlocked.Exchange(ref gotData, Environment.TickCount64);
            }
            catch (Exception) { }
        };
        recorder.RecordingStopped += (_, e) => stopped = e.Exception ?? new InvalidOperationException("캡처가 멈췄습니다");
        recorder.StartRecording();
        _log($"[discord-output] 녹음 대기: {device.FriendlyName} (루프백 {recorder.WaveFormat.SampleRate}Hz {recorder.WaveFormat.Channels}ch)");

        var seg = new Segmenter(_emit!, _log);
        var acc = new float[Frame];
        var accN = 0;
        var read = new float[Frame];
        var frame = new short[Frame];
        var needed = recorder.WaveFormat.AverageBytesPerSecond / 20;
        long silentClock = Environment.TickCount64;   // 무음 프레임을 넣은 시각(데이터가 없을 때만 쓴다)
        var settingsTime = DiscordSettings.FileTime;
        long settingsCheck = 0;
        try
        {
            while (!_cts.IsCancellationRequested && stopped is null)
            {
                // 장치를 바꾸면(discord-device) 다시 붙는다
                if (Environment.TickCount64 - settingsCheck > 2000)
                {
                    settingsCheck = Environment.TickCount64;
                    if (DiscordSettings.FileTime != settingsTime)
                    {
                        settingsTime = DiscordSettings.FileTime;
                        if (DiscordSettings.Load().OutputDevice != key) { _log("[discord-output] 장치가 바뀌어 다시 붙습니다"); break; }
                    }
                }
                // 원본 50ms 분량이 쌓였을 때만 읽는다(덜 쌓인 채 읽으면 리샘플러가 끊긴 구간을 그대로 넘긴다. HearingAid와 같은 이유)
                var n = buffer.BufferedBytes >= needed ? resampled.Read(read.AsSpan(0, Frame - accN)) : 0;
                if (n > 0)
                {
                    Array.Copy(read, 0, acc, accN, n);
                    accN += n;
                    if (accN == Frame)
                    {
                        for (int i = 0; i < Frame; i++) frame[i] = M4aWriter.ToShort(acc[i]);
                        seg.Push(frame);
                        accN = 0;
                    }
                    silentClock = Environment.TickCount64;
                    continue;
                }
                // 데이터가 200ms 넘게 없으면 재생이 멈춘 것: 벽시계 50ms마다 무음 프레임
                var now = Environment.TickCount64;
                if (now - Interlocked.Read(ref gotData) > 200 && now - silentClock >= 50)
                {
                    Array.Clear(frame);
                    while (now - silentClock >= 50) { seg.Push(frame); silentClock += 50; }
                }
                Thread.Sleep(10);
            }
        }
        finally
        {
            seg.Close();
            try { recorder.StopRecording(); } catch { }
        }
        if (stopped is not null && !_cts.IsCancellationRequested) throw new InvalidOperationException("캡처 끊김: " + stopped.Message);
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
        var seg = new Segmenter(emit, log, () => t0.AddMilliseconds(frames * 50));
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

    /// <summary>폰 AudioSource와 같은 구간 규칙. 50ms 프레임을 하나씩 받는다.</summary>
    private sealed class Segmenter(Action<Dictionary<string, object?>> emit, Action<string> log, Func<DateTimeOffset>? clock = null)
    {
        private DateTimeOffset Now => clock?.Invoke() ?? DateTimeOffset.Now;
        private readonly Queue<short[]> _preroll = new();
        private double _floor = -60;
        private int _loud, _quiet;
        private Seg? _seg;

        public void Push(short[] frame)
        {
            var db = M4aWriter.Dbfs(frame);
            _floor = db < _floor ? _floor + (db - _floor) * 0.1 : _floor + (db - _floor) * 0.002;
            if (_seg is null)
            {
                _preroll.Enqueue((short[])frame.Clone());
                if (_preroll.Count > PrerollFrames) _preroll.Dequeue();
                _loud = db > _floor + StartDb ? _loud + 1 : 0;
                if (_loud >= StartFrames)
                {
                    try
                    {
                        _seg = new Seg(Now.AddMilliseconds(-50.0 * _preroll.Count), _floor);
                        foreach (var f in _preroll) _seg.Write(f, M4aWriter.Dbfs(f));
                    }
                    catch (Exception e) { log($"[discord-output] 파일을 열지 못함: {e.Message}"); _seg = null; }
                    _preroll.Clear(); _loud = 0; _quiet = 0;
                }
            }
            else
            {
                _seg.Write(frame, db);
                _quiet = db < _floor + KeepDb ? _quiet + 1 : 0;
                if (_quiet >= StopFrames || _seg.Frames >= MaxFrames) { Finish(); _quiet = 0; }
            }
        }

        public void Close() { if (_seg is not null) Finish(); }

        private void Finish()
        {
            var s = _seg!;
            _seg = null;
            try { emit(s.Finish(Now, _floor)); }
            catch (Exception e) { log($"[discord-output] 파일 마무리 실패 {s.Rel}: {e.Message}"); }
        }
    }

    private sealed class Seg
    {
        public readonly DateTimeOffset Start;
        public readonly string Rel;
        private readonly double _floorAtStart;
        private readonly M4aWriter _w;
        public int Frames { get; private set; }
        private double _sumDb, _peak = -120;

        public Seg(DateTimeOffset start, double floor)
        {
            Start = start; _floorAtStart = floor;
            var (abs, rel) = RecordingPaths.New(Folder, start);
            Rel = rel;
            _w = new M4aWriter(abs);
        }

        public void Write(short[] f, double db) { Frames++; _sumDb += db; if (db > _peak) _peak = db; _w.Write(f); }

        public Dictionary<string, object?> Finish(DateTimeOffset end, double floor)
        {
            try
            {
                var (bytes, sha) = _w.Finish();
                return new()
                {
                    ["start"] = Start.UtcDateTime, ["end"] = end.UtcDateTime, ["file"] = Rel, ["bytes"] = bytes, ["sha256"] = sha,
                    ["codec"] = M4aWriter.Codec, ["duration_ms"] = _w.DurationMs,
                    ["mean_db"] = Frames > 0 ? _sumDb / Frames : -120.0, ["peak_db"] = _peak, ["floor_db"] = (_floorAtStart + floor) / 2,
                };
            }
            catch { _w.Abort(); throw; }
        }
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
