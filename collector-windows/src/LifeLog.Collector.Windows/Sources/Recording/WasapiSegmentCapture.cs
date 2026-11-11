using NAudio.CoreAudioApi;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// WASAPI로 장치 하나를 듣고 16kHz 모노 50ms 프레임으로 바꿔 <see cref="Segmenter"/>에 흘린다(2026-11-11 DiscordOutputSource에서 뺌).
/// - loopback=true: 렌더 장치 루프백(discord_output). 아무것도 재생되지 않으면 데이터가 오지 않으므로, 200ms 넘게 비면
///   벽시계 50ms마다 무음 프레임을 넣어 끝 판정이 멈추지 않게 한다.
/// - loopback=false: 입력 장치(laptop_mic). 데이터가 계속 온다(같은 무음 보충은 끊겼을 때만 돈다).
/// 취소되거나 <paramref name="reattach"/>가 true면(2초마다 묻는다) 열린 파일을 닫고 돌아온다. 캡처가 끊기면 예외.
/// </summary>
public static class WasapiSegmentCapture
{
    const int Frame = Segmenter.Frame;

    /// <summary>시험용(mic-test): 프레임마다 (태그, dBFS). 평소에는 null.</summary>
    public static Action<string, double>? Level { get; set; }

    public static void Run(MMDevice device, bool loopback, Segmenter seg, string tag, Action<string> log, CancellationToken ct, Func<bool> reattach)
    {
        var builder = new WasapiRecorderBuilder();
        if (loopback) builder = builder.WithLoopbackCapture();
        using var recorder = builder.WithDevice(device).Build();
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
        log($"[{tag}] 녹음 대기: {device.FriendlyName} ({(loopback ? "루프백" : "입력")} {recorder.WaveFormat.SampleRate}Hz {recorder.WaveFormat.Channels}ch)");

        var acc = new float[Frame];
        var accN = 0;
        var read = new float[Frame];
        var frame = new short[Frame];
        var needed = recorder.WaveFormat.AverageBytesPerSecond / 20;
        long silentClock = Environment.TickCount64;   // 무음 프레임을 넣은 시각(데이터가 없을 때만 쓴다)
        long settingsCheck = 0;
        try
        {
            while (!ct.IsCancellationRequested && stopped is null)
            {
                if (Environment.TickCount64 - settingsCheck > 2000)
                {
                    settingsCheck = Environment.TickCount64;
                    if (reattach()) break;
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
                        Level?.Invoke(tag, M4aWriter.Dbfs(frame));
                        accN = 0;
                    }
                    silentClock = Environment.TickCount64;
                    continue;
                }
                // 데이터가 200ms 넘게 없으면(루프백: 재생이 멈춤): 벽시계 50ms마다 무음 프레임
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
        if (stopped is not null && !ct.IsCancellationRequested) throw new InvalidOperationException("캡처 끊김: " + stopped.Message);
    }
}
