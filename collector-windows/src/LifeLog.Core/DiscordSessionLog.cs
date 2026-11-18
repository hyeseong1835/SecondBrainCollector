using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace LifeLog.Core;

/// <summary>디스코드 사용자 이름 세 가지(2026-11-19). 기본 표시 이름은 <see cref="DefaultName"/>(전역 이름 → 사용자명 → id). 서버 닉네임은 기록만.</summary>
public sealed record DiscordUserInfo(string Username, string? GlobalName, string? Nick, bool Bot)
{
    public static string DefaultName(DiscordUserInfo? u, string userId) =>
        !string.IsNullOrWhiteSpace(u?.GlobalName) ? u!.GlobalName! : !string.IsNullOrWhiteSpace(u?.Username) ? u!.Username : userId;
}

/// <summary>음성 상태 플래그(바뀐 뒤 값 전부를 이벤트로 남긴다).</summary>
public sealed record DiscordVoiceFlags(bool SelfMute = false, bool SelfDeaf = false, bool Mute = false, bool Deaf = false, bool Stream = false, bool Video = false);

/// <summary>
/// 디스코드 봇 녹음 세션 기록(2026-11-19). 봇이 음성 채널에 들어가서 나올 때까지 한 세션: 서버·채널, 그 시각 채널에 있던 사람 타임라인(presence),
/// 들어옴·나감·음성 상태·이름 이벤트, 사람별 오디오 파일과 사용자 id 연결. 모양은 설계 문서 "디스코드 봇 메타데이터.md" 1-1(공통 약속, name-history가 읽음).
/// 네트워크·NetCord 없이 순수 로직만(호스트 DiscordBotSource가 게이트웨이 이벤트를 옮겨 부르고, 시험이 가짜 이벤트로 부른다). 스레드 안전(내부 잠금).
/// 사람 식별은 사용자 id(문자열). 봇 자신(<see cref="BotUserId"/>)은 기록하지 않는다.
/// </summary>
public sealed class DiscordSessionLog
{
    public const string Kind = "discord-bot-session";
    public const int Schema = 1;
    public const string Suffix = ".session.json";

    public string Id { get; }
    /// <summary>discord-bot 폴더 기준 상대 경로 "yyyy-MM-dd/yyyyMMdd-HHmmss-session-xxxxxxxx.session.json"</summary>
    public string Rel { get; }
    public string GuildId { get; }
    public string ChannelId { get; }
    public string? BotUserId { get; }
    public string? TargetUserId { get; }
    public DateTimeOffset Start { get; }
    public DateTimeOffset? End { get; private set; }
    /// <summary>마지막으로 파일에 쓴 뒤 바뀌었나(호스트가 1초마다 보고 쓴다).</summary>
    public bool Dirty { get { lock (_gate) return _dirty; } }

    private readonly object _gate = new();
    private string _guildName, _channelName;
    private readonly Dictionary<string, DiscordUserInfo> _users = new();
    private readonly Dictionary<string, DiscordVoiceFlags> _in = new();   // 지금 채널에 있는 사람 → 상태
    private readonly HashSet<string> _seen = new();                       // 세션에 한 번이라도 나온 사람(이름 이벤트를 받을 사람)
    private readonly List<JsonObject> _events = new();
    private readonly List<(DateTimeOffset Start, DateTimeOffset? End, string[] Users)> _presence = new();
    private readonly List<(string File, string User, DateTimeOffset Start, DateTimeOffset End)> _files = new();
    private bool _dirty = true;

    public DiscordSessionLog(string guildId, string guildName, string channelId, string channelName, string? botUserId, string? targetUserId,
        DateTimeOffset start, string? suffix = null)
    {
        var local = start.ToLocalTime();
        var hex = suffix ?? Guid.NewGuid().ToString("N")[..8];
        Id = $"{local:yyyyMMdd-HHmmss}-{hex}";
        Rel = $"{local:yyyy-MM-dd}/{local:yyyyMMdd-HHmmss}-session-{hex}{Suffix}";
        GuildId = guildId; ChannelId = channelId; _guildName = guildName; _channelName = channelName;
        BotUserId = botUserId; TargetUserId = targetUserId; Start = start;
        _presence.Add((start, null, []));
    }

    public IReadOnlyCollection<string> Members { get { lock (_gate) return _in.Keys.ToList(); } }

    /// <summary>봇이 들어갈 때 이미 채널에 있던 사람.</summary>
    public void Present(DateTimeOffset at, string userId, DiscordVoiceFlags flags) => Enter(at, userId, flags, "present");

    /// <summary>들어옴. 이미 있으면 상태만 본다.</summary>
    public void Join(DateTimeOffset at, string userId, DiscordVoiceFlags flags) => Enter(at, userId, flags, "join");

    private void Enter(DateTimeOffset at, string userId, DiscordVoiceFlags flags, string type)
    {
        if (userId == BotUserId) return;
        lock (_gate)
        {
            if (End is not null) return;
            if (_in.ContainsKey(userId)) { StateLocked(at, userId, flags); return; }
            _in[userId] = flags;
            _seen.Add(userId);
            _events.Add(FlagsEvent(at, type, userId, flags));
            PresenceChanged(at);
        }
    }

    public void Leave(DateTimeOffset at, string userId)
    {
        lock (_gate)
        {
            if (End is not null || !_in.Remove(userId)) return;
            _events.Add(new JsonObject { ["at"] = Iso(at), ["type"] = "leave", ["user_id"] = userId });
            PresenceChanged(at);
        }
    }

    /// <summary>음성 상태 갱신(채널에 있는 사람). 플래그가 바뀌었을 때만 state 이벤트.</summary>
    public void State(DateTimeOffset at, string userId, DiscordVoiceFlags flags)
    {
        lock (_gate) StateLocked(at, userId, flags);
    }

    private void StateLocked(DateTimeOffset at, string userId, DiscordVoiceFlags flags)
    {
        if (End is not null || !_in.TryGetValue(userId, out var old) || old == flags) return;
        _in[userId] = flags;
        _events.Add(FlagsEvent(at, "state", userId, flags));
        _dirty = true;
    }

    /// <summary>
    /// 이름을 알게 됨. 세션에 나온 사람만 기록: 처음 알면 name 이벤트(before 없음), 바뀌었으면 name 이벤트(before = 앞 값). 바뀐 게 있으면 true.
    /// </summary>
    public bool Name(DateTimeOffset at, string userId, DiscordUserInfo info)
    {
        lock (_gate)
        {
            if (End is not null || userId == BotUserId || !_seen.Contains(userId)) return false;
            _users.TryGetValue(userId, out var old);
            if (old == info) return false;
            _users[userId] = info;
            var e = new JsonObject { ["at"] = Iso(at), ["type"] = "name", ["user_id"] = userId };
            foreach (var (k, v) in NameFields(info)) e[k] = v;
            if (old is not null)
            {
                var before = new JsonObject();
                foreach (var (k, v) in NameFields(old)) before[k] = v;
                e["before"] = before;
            }
            _events.Add(e);
            _dirty = true;
            return true;
        }
    }

    /// <summary>사람별 오디오 파일 하나가 끝남(파일 ↔ 사용자 id). 파일로 처음 나온 사람도 세션 사람이 된다.</summary>
    public void File(string rel, string userId, DateTimeOffset start, DateTimeOffset end)
    {
        lock (_gate)
        {
            _seen.Add(userId);
            _files.Add((rel, userId, start, end));
            _dirty = true;
        }
    }

    /// <summary>서버·채널 이름이 바뀜(들어갈 때 몰랐다가 나중에 앎).</summary>
    public void Rename(string guildName, string channelName)
    {
        lock (_gate)
        {
            if (guildName == _guildName && channelName == _channelName) return;
            _guildName = guildName; _channelName = channelName; _dirty = true;
        }
    }

    /// <summary>세션 끝(봇이 나옴). 남아 있던 사람은 leave 없이 presence 끝 = 세션 끝.</summary>
    public void Close(DateTimeOffset at)
    {
        lock (_gate)
        {
            if (End is not null) return;
            End = at;
            var last = _presence[^1];
            _presence[^1] = (last.Start, at, last.Users);
            _dirty = true;
        }
    }

    public DiscordUserInfo? User(string userId) { lock (_gate) return _users.GetValueOrDefault(userId); }

    private void PresenceChanged(DateTimeOffset at)
    {
        var users = _in.Keys.Order(StringComparer.Ordinal).ToArray();
        var last = _presence[^1];
        if (last.Start == at || last.Start > at) _presence[^1] = (last.Start, null, users);   // 같은 시각에 여럿(들어갈 때 present)
        else
        {
            _presence[^1] = (last.Start, at, last.Users);
            _presence.Add((at, null, users));
        }
        _dirty = true;
    }

    private static JsonObject FlagsEvent(DateTimeOffset at, string type, string userId, DiscordVoiceFlags f) => new()
    {
        ["at"] = Iso(at), ["type"] = type, ["user_id"] = userId,
        ["self_mute"] = f.SelfMute, ["self_deaf"] = f.SelfDeaf, ["mute"] = f.Mute, ["deaf"] = f.Deaf, ["stream"] = f.Stream, ["video"] = f.Video,
    };

    private static IEnumerable<(string, JsonNode?)> NameFields(DiscordUserInfo u) =>
        [("username", u.Username), ("global_name", u.GlobalName), ("nick", u.Nick)];

    private static string Iso(DateTimeOffset t) => t.ToLocalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");

    static readonly JsonSerializerOptions Opts = new() { WriteIndented = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    /// <summary>세션 파일 내용(문서 1-1 모양). 부르면 Dirty가 풀린다.</summary>
    public string ToJson()
    {
        lock (_gate)
        {
            var users = new JsonObject();
            foreach (var id in _seen.Order(StringComparer.Ordinal))
            {
                var u = _users.GetValueOrDefault(id);
                users[id] = new JsonObject
                {
                    ["username"] = u?.Username, ["global_name"] = u?.GlobalName, ["nick"] = u?.Nick, ["bot"] = u?.Bot ?? false,
                };
            }
            var o = new JsonObject
            {
                ["kind"] = Kind, ["schema"] = Schema, ["id"] = Id,
                ["guild"] = new JsonObject { ["id"] = GuildId, ["name"] = _guildName },
                ["channel"] = new JsonObject { ["id"] = ChannelId, ["name"] = _channelName },
                ["bot_user_id"] = BotUserId, ["target_user_id"] = TargetUserId,
                ["start"] = Iso(Start), ["end"] = End is { } e ? Iso(e) : null,
                ["users"] = users,
                ["events"] = new JsonArray(_events.Select(x => (JsonNode)x.DeepClone()).ToArray()),
                ["presence"] = new JsonArray(_presence.Select(p => (JsonNode)new JsonObject
                {
                    ["start"] = Iso(p.Start), ["end"] = p.End is { } pe ? Iso(pe) : null,
                    ["user_ids"] = new JsonArray(p.Users.Select(u => (JsonNode)JsonValue.Create(u)!).ToArray()),
                }).ToArray()),
                ["files"] = new JsonArray(_files.Select(f => (JsonNode)new JsonObject
                {
                    ["file"] = f.File, ["user_id"] = f.User, ["start"] = Iso(f.Start), ["end"] = Iso(f.End),
                }).ToArray()),
            };
            _dirty = false;
            return o.ToJsonString(Opts);
        }
    }

    /// <summary>원천 폴더(discord-bot) 아래에 쓴다(임시 파일 → 바꾸기). 실패는 호출한 쪽이 다룬다.</summary>
    public void WriteTo(string sourceRoot)
    {
        var path = Path.Combine(sourceRoot, Rel.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var tmp = path + ".tmp";
        System.IO.File.WriteAllText(tmp, ToJson());
        System.IO.File.Move(tmp, path, overwrite: true);
    }
}
