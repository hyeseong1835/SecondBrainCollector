using System.Text.Json;
using LifeLog.Collector.Windows.Sources.Recording;
using NAudio.Dsp;
using NetCord;
using NetCord.Gateway;
using NetCord.Gateway.Voice;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 디스코드 봇 녹음(2026-11-11, 켰을 때만). HearingAid와 같은 봇이 대상 사용자(나)가 있는 음성 채널을 따라 들어가
/// <b>말하는 사람별로</b> 오디오를 저장한다. 실시간 전사는 하지 않는다. 대상이 채널을 옮기면 따라가고 나가면 봇도 나간다.
///
/// - 켜고 끄기: 플러그인 win-discord-bot 설정 enabled(데스크톱 설정 › 플러그인, 트레이 메뉴 "디스코드 봇 녹음", `secondbrain-collector discord-bot on|off`).
/// - 대상 사용자 ID는 설정 target_user_id, 봇 토큰은 비밀값 bot_token(DPAPI, 데스크톱 설정 화면 또는 `discord-token`). 토큰은 메모리에서만 쓴다.
/// - 사람마다 파일: Opus(48kHz) → 16kHz 모노 → AAC 32kbps. 패킷이 5초 끊기면 그 사람의 파일을 닫고 다음 말부터 새 파일.
///   5초 안의 끊김은 무음으로 채워 파일 안 시각이 실제 시각과 맞는다. 30분이면 나눈다.
/// - 위치: %USERPROFILE%\SecondBrainRecordings\discord-bot\yyyy-MM-dd\yyyyMMdd-HHmmss-&lt;사용자ID&gt;-xxxxxxxx.m4a
///   옆에 같은 이름의 .json(사용자 ID·표시 이름·서버·채널·시작/끝 시각)을 둔다. 분석(tools/recordings/analyze.py)이 화자로 쓴다.
/// - 파일 하나가 끝날 때마다 { start, end, file, bytes, sha256, codec, duration_ms, mean_db, peak_db, user_id, speaker, guild_id, channel_id }를 낸다.
///   ID는 JS 숫자 범위를 넘으므로 문자열.
/// </summary>
public sealed class DiscordBotSource : ISource
{
    public string Id => "discord_bot";
    public const string Folder = "discord-bot";
    const int Rate = M4aWriter.Rate;
    static readonly TimeSpan Gap = TimeSpan.FromSeconds(5);
    static readonly TimeSpan MaxLen = TimeSpan.FromMinutes(30);

    /// <summary>봇이 음성 채널에 들어가 녹음 중인가. 이때는 discord_output·laptop_mic이 멈춘다(<see cref="DiscordCall.Recording"/>).</summary>
    public static volatile bool InVoice;

    /// <summary>트레이에 보여 줄 상태.</summary>
    public static string StatusText { get; private set; } = "꺼짐";

    private readonly Action<string> _log;
    private Action<Dictionary<string, object?>>? _emit;
    private readonly SemaphoreSlim _lock = new(1, 1);       // 시작/정지·채널 이동 직렬화
    private readonly CancellationTokenSource _cts = new();
    private Task? _watch;
    private bool _enabled;

    private GatewayClient? _client;
    private VoiceClient? _voice;
    private ulong _target;
    private ulong? _guildId, _channelId;

    private readonly Dictionary<ulong, Pipeline> _pipes = new();     // 사용자 → 디코더(채널에 있는 동안)
    private readonly Dictionary<ulong, Track> _tracks = new();       // 사용자 → 지금 쓰는 파일
    private readonly Dictionary<ulong, (string display, string username)> _names = new();
    private readonly HashSet<ulong> _nameRequests = new();

    public DiscordBotSource(Action<string> log) { _log = log; }

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        DiscordSettings.Changed += OnSettingsChanged;
        _watch = Task.Run(WatchAsync);
    }

    private void OnSettingsChanged(DiscordSettings s) => _ = ApplyAsync(s.BotEnabled);

    /// <summary>설정(다시 읽기로 새로 들어온 값)·비밀값 파일이 바뀌면 다시 적용한다. 끊긴 파일 닫기도 여기서.</summary>
    private async Task WatchAsync()
    {
        string? seen = null;
        while (!_cts.IsCancellationRequested)
        {
            try
            {
                var t = DiscordSettings.Signature;
                if (t != seen) { seen = t; await ApplyAsync(DiscordSettings.Load().BotEnabled); }
                CloseIdle(DateTimeOffset.Now);
            }
            catch (Exception e) { _log($"[discord-bot] {e.Message}"); }
            try { await Task.Delay(1000, _cts.Token); } catch (OperationCanceledException) { }
        }
    }

    private async Task ApplyAsync(bool enabled)
    {
        await _lock.WaitAsync();
        try
        {
            if (enabled == _enabled && (!enabled || _client is not null)) return;
            _enabled = enabled;
            if (enabled) await StartBotAsync(); else await StopBotAsync("꺼짐");
        }
        catch (Exception e) { _log($"[discord-bot] {(enabled ? "시작" : "정지")} 실패: {e.Message}"); StatusText = "오류: " + e.Message; _client = null; }
        finally { _lock.Release(); }
    }

    private async Task StartBotAsync()
    {
        var s = DiscordSettings.Load();
        if (!s.HasBotCredentials) { StatusText = "토큰·대상 사용자 없음(설정 › 플러그인)"; _log("[discord-bot] 봇 토큰이나 따라갈 사용자 ID가 없습니다. 데스크톱 설정 › 플러그인 › 디스코드 봇 녹음에서 넣으세요"); return; }
        var (token, target) = s.GetBotCredentials();
        _target = target;
        var c = new GatewayClient(new BotToken(token), new GatewayClientConfiguration { Intents = GatewayIntents.Guilds | GatewayIntents.GuildVoiceStates });
        c.Ready += _ => { var __ = ScanAndJoinAsync(); return default; };
        c.VoiceStateUpdate += OnVoiceStateUpdate;
        c.GuildUserChunk += OnGuildUserChunk;
        _client = c;
        await c.StartAsync();
        StatusText = "대기(대상이 음성 채널에 없음)";
        _log("[discord-bot] 켬. 게이트웨이 연결");
    }

    private async Task StopBotAsync(string status)
    {
        await LeaveAsync();
        if (_client is { } c)
        {
            _client = null;
            try { await c.CloseAsync(); } catch { }
            c.Dispose();
            _log("[discord-bot] 끔");
        }
        StatusText = status;
    }

    private async Task ScanAndJoinAsync()
    {
        await Task.Delay(2000);   // 길드 목록(GUILD_CREATE)이 도착할 시간
        if (_client is not { } c) return;
        foreach (var g in c.Cache.Guilds.Values)
            if (g.VoiceStates.TryGetValue(_target, out var vs) && vs.ChannelId is ulong ch)
            {
                await FollowAsync(g.Id, ch);
                return;
            }
    }

    private ValueTask OnVoiceStateUpdate(VoiceState st)
    {
        if (st.UserId == _target) _ = FollowAsync(st.GuildId, st.ChannelId);
        else if (st.User is { } u) RememberName(u);
        return default;
    }

    /// <summary>대상 사용자를 따라간다. channel이 null이면(나감) 봇도 나간다.</summary>
    private async Task FollowAsync(ulong guildId, ulong? channelId)
    {
        await _lock.WaitAsync();
        try
        {
            if (!_enabled || _client is not { } c) return;
            if (channelId is null)
            {
                if (_guildId == guildId) { await LeaveAsync(); StatusText = "대기(대상이 음성 채널에 없음)"; _log("[discord-bot] 대상이 나가서 봇도 나감"); }
                return;
            }
            if (_guildId == guildId && _channelId == channelId) return;
            await LeaveAsync();
            var vc = await c.JoinVoiceChannelAsync(guildId, channelId.Value);
            vc.VoiceReceive += OnVoiceReceive;
            await vc.StartAsync();
            _voice = vc; _guildId = guildId; _channelId = channelId;
            InVoice = true;
            var (gName, chName) = Names(guildId, channelId.Value);
            StatusText = $"녹음 중: {gName} / {chName}";
            _log($"[discord-bot] 참가: {gName} / {chName}");
        }
        catch (Exception e) { _log($"[discord-bot] 음성 채널 참가 실패: {e.Message}"); StatusText = "참가 실패: " + e.Message; }
        finally { _lock.Release(); }
    }

    private async Task LeaveAsync()
    {
        var vc = _voice;
        var guild = _guildId;
        _voice = null; _guildId = null; _channelId = null;
        InVoice = false;
        if (vc is not null)
        {
            vc.VoiceReceive -= OnVoiceReceive;
            try { await vc.CloseAsync(); } catch { }
            vc.Dispose();
        }
        if (guild is ulong g && _client is { } c) { try { await c.UpdateVoiceStateAsync(new(g, null)); } catch { } }
        List<Track> closing;
        lock (_tracks)
        {
            closing = _tracks.Values.ToList();
            _tracks.Clear();
            foreach (var p in _pipes.Values) p.Dispose();
            _pipes.Clear();
        }
        var now = DateTimeOffset.Now;
        foreach (var t in closing) Finish(t, now);
    }

    private ValueTask OnVoiceReceive(VoiceReceiveEventArgs args)
    {
        if (_voice is not { } vc || !vc.Cache.SsrcUsers.TryGetValue(args.Ssrc, out var userId)) return default;
        Pipeline p;
        lock (_tracks)
        {
            if (!_pipes.TryGetValue(userId, out p!))
                _pipes[userId] = p = new Pipeline(samples => OnPcm(userId, samples));
        }
        try { p.Decoder.Write(args.Frame); } catch { /* 손상된 Opus 프레임 */ }
        return default;
    }

    private void OnPcm(ulong userId, short[] pcm)
    {
        var now = DateTimeOffset.Now;
        Track? old = null;
        lock (_tracks)
        {
            _tracks.TryGetValue(userId, out var t);
            if (t is not null && (now - t.Last > Gap || now - t.Start > MaxLen)) { old = t; _tracks.Remove(userId); t = null; }
            if (t is null)
            {
                if (_guildId is not ulong g || _channelId is not ulong ch) return;
                try { t = _tracks[userId] = new Track(userId, g, ch, now.AddMilliseconds(-pcm.Length * 1000.0 / Rate)); }
                catch (Exception e) { _log($"[discord-bot] 파일을 열지 못함: {e.Message}"); return; }
                EnsureName(userId);
            }
            t.Write(pcm, now);
        }
        if (old is not null) _ = Task.Run(() => Finish(old, old.Last));
    }

    private void CloseIdle(DateTimeOffset now)
    {
        List<Track> idle;
        lock (_tracks)
        {
            idle = _tracks.Values.Where(t => now - t.Last > Gap).ToList();
            foreach (var t in idle) _tracks.Remove(t.UserId);
        }
        foreach (var t in idle) Finish(t, t.Last);
    }

    private void Finish(Track t, DateTimeOffset end)
    {
        try
        {
            var (bytes, sha) = t.W.Finish();
            (string display, string username) n;
            lock (_names) n = _names.TryGetValue(t.UserId, out var hit) ? hit : (t.UserId.ToString(), "");
            var (display, username) = n;
            var (gName, chName) = Names(t.GuildId, t.ChannelId);
            var meta = new Dictionary<string, object?>
            {
                ["source"] = Folder, ["file"] = t.Rel, ["user_id"] = t.UserId.ToString(), ["speaker"] = display, ["username"] = username,
                ["guild_id"] = t.GuildId.ToString(), ["guild"] = gName, ["channel_id"] = t.ChannelId.ToString(), ["channel"] = chName,
                ["start"] = t.Start.ToString("o"), ["end"] = end.ToString("o"), ["duration_ms"] = t.W.DurationMs,
                ["bytes"] = bytes, ["sha256"] = sha, ["codec"] = M4aWriter.Codec,
            };
            File.WriteAllText(Path.ChangeExtension(t.W.Path, ".json"), JsonSerializer.Serialize(meta, new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
            _emit?.Invoke(new()
            {
                ["start"] = t.Start.UtcDateTime, ["end"] = end.UtcDateTime, ["file"] = t.Rel, ["bytes"] = bytes, ["sha256"] = sha,
                ["codec"] = M4aWriter.Codec, ["duration_ms"] = t.W.DurationMs, ["mean_db"] = t.MeanDb, ["peak_db"] = t.PeakDb,
                ["user_id"] = t.UserId.ToString(), ["speaker"] = display, ["guild_id"] = t.GuildId.ToString(), ["channel_id"] = t.ChannelId.ToString(),
            });
        }
        catch (Exception e) { t.W.Abort(); _log($"[discord-bot] 파일 마무리 실패 {t.Rel}: {e.Message}"); }
    }

    // ---- 이름 ----
    private (string guild, string channel) Names(ulong guildId, ulong channelId)
    {
        try
        {
            if (_client?.Cache.Guilds.TryGetValue(guildId, out var g) == true)
                return (g.Name, g.Channels.TryGetValue(channelId, out var ch) ? ch.Name : channelId.ToString());
        }
        catch { }
        return (guildId.ToString(), channelId.ToString());
    }

    private void RememberName(GuildUser u)
    {
        var display = !string.IsNullOrEmpty(u.Nickname) ? u.Nickname : !string.IsNullOrEmpty(u.GlobalName) ? u.GlobalName : u.Username;
        lock (_names) _names[u.Id] = (display!, u.Username);
    }

    /// <summary>봇이 들어오기 전부터 있던 사람은 음성 상태에 member가 없을 수 있어 op 8(user_ids 지정, 특권 인텐트 불필요)로 묻는다. HearingAid와 같다.</summary>
    private void EnsureName(ulong userId)
    {
        lock (_names) if (_names.ContainsKey(userId)) return;
        if (_client is not { } c || _guildId is not ulong g) return;
        if (c.Cache.Guilds.TryGetValue(g, out var guild) && guild.VoiceStates.TryGetValue(userId, out var vs) && vs.User is { } u) { RememberName(u); return; }
        lock (_nameRequests) if (!_nameRequests.Add(userId)) return;
        _ = Task.Run(async () =>
        {
            try { await c.RequestGuildUsersAsync(new GuildUsersRequestProperties(g).WithUserIds([userId])); }
            catch (Exception e) { lock (_nameRequests) _nameRequests.Remove(userId); _log($"[discord-bot] 이름 조회 실패: {e.Message}"); }
        });
    }

    private ValueTask OnGuildUserChunk(GuildUserChunkEventArgs args)
    {
        foreach (var u in args.Users) RememberName(u);
        return default;
    }

    public void Dispose()
    {
        DiscordSettings.Changed -= OnSettingsChanged;
        _cts.Cancel();
        try { _watch?.Wait(TimeSpan.FromSeconds(3)); } catch { }
        _lock.Wait(TimeSpan.FromSeconds(5));
        try { StopBotAsync("꺼짐").Wait(TimeSpan.FromSeconds(60)); } catch { }
        _enabled = false;
    }

    // ---- 사람별 파이프라인과 파일 ----

    /// <summary>Opus 디코드(48kHz 모노 float) → 저역 통과 → 1/3 솎기(16kHz) → 16비트. HearingAid SsrcPipeline과 같다.</summary>
    private sealed class Pipeline : IDisposable
    {
        public readonly OpusDecodeStream Decoder;
        private readonly BiQuadFilter _lp = BiQuadFilter.LowPassFilter(48000, 7000, 0.707f);
        private int _phase;
        private readonly Action<short[]> _out;

        public Pipeline(Action<short[]> output)
        {
            _out = output;
            Decoder = new OpusDecodeStream(new Sink(OnPcm), PcmFormat.Float, VoiceChannels.Mono);
        }

        private void OnPcm(ReadOnlySpan<byte> bytes)
        {
            var n = bytes.Length / 4;
            if (n == 0) return;
            var o = new List<short>(n / 3 + 1);
            for (int i = 0; i < n; i++)
            {
                var f = _lp.Transform(BitConverter.ToSingle(bytes.Slice(i * 4, 4)));
                if (_phase == 0) o.Add(M4aWriter.ToShort(f));
                _phase = (_phase + 1) % 3;
            }
            if (o.Count > 0) _out(o.ToArray());
        }

        public void Dispose() => Decoder.Dispose();
    }

    private delegate void PcmHandler(ReadOnlySpan<byte> data);

    private sealed class Sink(PcmHandler onWrite) : Stream
    {
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => onWrite(new ReadOnlySpan<byte>(buffer, offset, count));
        public override void Write(ReadOnlySpan<byte> buffer) => onWrite(buffer);
    }

    private sealed class Track
    {
        public readonly ulong UserId, GuildId, ChannelId;
        public readonly DateTimeOffset Start;
        public DateTimeOffset Last;
        public readonly M4aWriter W;
        public readonly string Rel;
        private double _sumDb; private int _chunks;
        public double PeakDb { get; private set; } = -120;
        public double MeanDb => _chunks > 0 ? _sumDb / _chunks : -120;

        public Track(ulong userId, ulong guildId, ulong channelId, DateTimeOffset start)
        {
            UserId = userId; GuildId = guildId; ChannelId = channelId; Start = Last = start;
            var (abs, rel) = RecordingPaths.New(Folder, start, userId.ToString());
            Rel = rel;
            W = new M4aWriter(abs);
        }

        /// <summary>도착 시각에 맞춰 쓴다. 파일 위치보다 200ms 넘게 늦게 온 말은 그 사이를 무음으로 채운다.</summary>
        public void Write(short[] pcm, DateTimeOffset now)
        {
            var due = (long)((now - Start).TotalSeconds * Rate) - pcm.Length;
            if (due - W.Samples > Rate / 5) W.WriteSilence(due - W.Samples);
            W.Write(pcm);
            Last = now;
            var db = M4aWriter.Dbfs(pcm);
            _sumDb += db; _chunks++;
            if (db > PeakDb) PeakDb = db;
        }
    }
}
