namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 소리 있는 구간만 파일로 남기는 규칙(폰 녹음 collector-android AudioSource.kt와 같다). 16kHz 모노 50ms 프레임을 하나씩 받는다.
/// discord_output(루프백)·laptop_mic(마이크)가 같이 쓴다(2026-11-11 DiscordOutputSource에서 뺌, 동작은 그대로).
///
/// - 프레임마다 dBFS를 잰다. 소음 바닥은 조용하면 빨리 내려가고 시끄러우면 천천히 오른다.
/// - 바닥+10dB가 200ms 이어지면 시작(앞 1초를 붙임), 바닥+6dB 아래가 5초 이어지면 끝, 30분이면 나눈다.
/// - 파일: <see cref="RecordingPaths"/> 아래 &lt;folder&gt;\yyyy-MM-dd\yyyyMMdd-HHmmss-xxxxxxxx.m4a(.part → 이름 바꿈, 지우지 않음).
/// - 파일 하나가 끝날 때마다 { start, end, file, bytes, sha256, codec, duration_ms, mean_db, peak_db, floor_db }를 낸다.
/// </summary>
public sealed class Segmenter(string folder, string tag, Action<Dictionary<string, object?>> emit, Action<string> log, Func<DateTimeOffset>? clock = null)
{
    public const int Frame = M4aWriter.Rate / 20;       // 50ms
    const double StartDb = 10, KeepDb = 6;
    const int StartFrames = 4, StopFrames = 100, PrerollFrames = 20, MaxFrames = 20 * 60 * 30;

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
                    _seg = new Seg(folder, Now.AddMilliseconds(-50.0 * _preroll.Count), _floor);
                    foreach (var f in _preroll) _seg.Write(f, M4aWriter.Dbfs(f));
                }
                catch (Exception e) { log($"[{tag}] 파일을 열지 못함: {e.Message}"); _seg = null; }
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
        catch (Exception e) { log($"[{tag}] 파일 마무리 실패 {s.Rel}: {e.Message}"); }
    }

    private sealed class Seg
    {
        public readonly DateTimeOffset Start;
        public readonly string Rel;
        private readonly double _floorAtStart;
        private readonly M4aWriter _w;
        public int Frames { get; private set; }
        private double _sumDb, _peak = -120;

        public Seg(string folder, DateTimeOffset start, double floor)
        {
            Start = start; _floorAtStart = floor;
            var (abs, rel) = RecordingPaths.New(folder, start);
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
