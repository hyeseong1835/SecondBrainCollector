using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using LifeLog.Core;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 디스코드 녹음 설정(2026-11-11 플러그인 설정으로 옮김). 원본은 이 기기의 플러그인 설치 설정(DB)과 로컬 비밀값 저장소다.
/// - win-discord-output 설정 `output_device`: 상시 녹음할 렌더 장치 ID(비면 소스가 쉰다)
/// - win-discord-bot 설정 `enabled`(봇 녹음 켬), `auto_only`(자동: 등록한 서버·채널에서만), `auto_targets`(자동 목록 "guild:ID"·"channel:ID"),
///   `target_user_id`(따라갈 사용자 ID), 비밀값 `bot_token`(<see cref="SecretStore"/>, DPAPI)
/// - 모드(2026-11-13): enabled=false → 꺼짐, enabled=true·auto_only=false → 켜짐(어디든 따라감), enabled=true·auto_only=true → 자동.
///   옛 "켬"(enabled=true)은 auto_only 기본값 false라 그대로 켜짐이다.
/// 호스트가 설치 행을 읽은 뒤 <see cref="Apply"/>로 넣고, 데스크톱 설정 화면·CLI가 DB를 바꾸면 수집기 다시 읽기(reload)로 새 호스트가 다시 넣는다.
/// 옛 %LOCALAPPDATA%\SecondBrain\collector\discord.json은 처음 실행 때 <see cref="TakeLegacy"/>로 옮기고 discord.json.migrated로 이름을 바꾼다.
/// </summary>
public sealed record DiscordSettings(string OutputDevice, bool BotEnabled, string TargetUser, bool BotAuto = false, IReadOnlyList<string>? AutoTargets = null)
{
    public const string ModeOff = "off", ModeOn = "on", ModeAuto = "auto";
    public const string OutputPlugin = "win-discord-output";
    public const string BotPlugin = "win-discord-bot";
    public const string TokenKey = "bot_token";

    private static DiscordSettings _current = new("", false, "");

    /// <summary>off · on · auto.</summary>
    public string BotMode => !BotEnabled ? ModeOff : BotAuto ? ModeAuto : ModeOn;

    /// <summary>
    /// 이 음성 채널에 따라 들어가도 되나. 켜짐이면 어디든, 자동이면 목록에 그 서버("guild:ID") 또는 채널("channel:ID")이 있을 때만.
    /// 접두어 없는 숫자는 서버·채널 어느 쪽과 맞아도 된다. 꺼짐이면 false.
    /// </summary>
    public bool Allows(ulong guildId, ulong channelId) => BotMode switch
    {
        ModeOn => true,
        ModeAuto => Listed(AutoTargets, guildId, channelId),
        _ => false,
    };

    public static bool Listed(IEnumerable<string>? targets, ulong guildId, ulong channelId) => DiscordTargets.Listed(targets, guildId, channelId);
    private static long _version;

    /// <summary>지금 값(호스트가 넣은 것). 호스트 밖(CLI)에서는 빈 값이다.</summary>
    public static DiscordSettings Load() => _current;

    /// <summary>바뀌었는지 비교용. 설정이 다시 들어오거나 비밀값 파일이 바뀌면 달라진다.</summary>
    public static string Signature => $"{Interlocked.Read(ref _version)}:{SecretStore.FileTime.Ticks}";

    /// <summary>같은 프로세스 안에서 바뀌면(트레이) 알린다.</summary>
    public static event Action<DiscordSettings>? Changed;

    /// <summary>트레이가 봇을 켜고 끌 때 DB에도 쓴다(호스트가 넣는다). (플러그인, 키, 값)</summary>
    public static Func<string, string, JsonNode?, Task>? Persist { get; set; }

    public bool HasBotCredentials => SecretStore.Has(BotPlugin, TokenKey) && ulong.TryParse(TargetUser, out _);

    public (string token, ulong targetUserId) GetBotCredentials()
        => (SecretStore.Get(BotPlugin, TokenKey) ?? throw new InvalidOperationException("봇 토큰을 풀 수 없습니다(다른 Windows 사용자가 저장?)"), ulong.Parse(TargetUser));

    /// <summary>호스트: 두 플러그인의 설치 설정을 넣는다(없으면 기본값).</summary>
    public static void Apply(JsonElement? output, JsonElement? bot)
    {
        _current = new(Str(output, "output_device"), Bool(bot, "enabled"), Str(bot, "target_user_id"), Bool(bot, "auto_only"), List(bot, "auto_targets"));
        Interlocked.Increment(ref _version);
        Changed?.Invoke(_current);
    }

    /// <summary>트레이: 봇 녹음 모드(off·on·auto). 바로 반영하고 DB에도 쓴다(enabled → auto_only 순서로).</summary>
    public static void SetBotMode(string mode)
    {
        var on = mode != ModeOff;
        var auto = mode == ModeAuto ? true : mode == ModeOn ? false : _current.BotAuto;   // 끌 때는 자동 여부를 남겨 둔다
        _current = _current with { BotEnabled = on, BotAuto = auto };
        Interlocked.Increment(ref _version);
        Changed?.Invoke(_current);
        if (Persist is { } p)
            _ = Task.Run(async () => { await p(BotPlugin, "enabled", JsonValue.Create(on)); await p(BotPlugin, "auto_only", JsonValue.Create(auto)); });
    }

    static string Str(JsonElement? s, string key) => s is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
    static List<string> List(JsonElement? s, string key) => s is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Array
        ? v.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!).Where(x => x.Trim().Length > 0).ToList() : [];
    static bool Bool(JsonElement? s, string key) => s is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;

    // ---- 옛 discord.json 옮기기 ----
    private sealed class Legacy
    {
        [JsonPropertyName("output_device")] public string OutputDevice { get; set; } = "";
        [JsonPropertyName("bot_enabled")] public bool BotEnabled { get; set; }
        [JsonPropertyName("bot_token_protected")] public string BotTokenProtected { get; set; } = "";
        [JsonPropertyName("target_user_protected")] public string TargetUserProtected { get; set; } = "";
    }

    private static string LegacyPath => Path.Combine(Config.Dir, "discord.json");

    /// <summary>옛 파일이 있으면 (장치, 봇 켬, 대상 사용자) 값을 돌려주고 토큰은 비밀값 저장소로 옮긴다. 없으면 null. 다 옮긴 뒤 <see cref="RetireLegacy"/>.</summary>
    public static (string OutputDevice, bool BotEnabled, string TargetUser)? TakeLegacy()
    {
        if (!File.Exists(LegacyPath)) return null;
        Legacy? l;
        try { l = JsonSerializer.Deserialize<Legacy>(File.ReadAllText(LegacyPath)); } catch (JsonException) { l = null; }
        if (l is null) return null;
        var token = Unprotect(l.BotTokenProtected);
        if (!string.IsNullOrEmpty(token) && !SecretStore.Has(BotPlugin, TokenKey)) SecretStore.Set(BotPlugin, TokenKey, token);
        return (l.OutputDevice, l.BotEnabled, Unprotect(l.TargetUserProtected) ?? "");
    }

    public static void RetireLegacy()
    {
        if (File.Exists(LegacyPath)) File.Move(LegacyPath, LegacyPath + ".migrated", overwrite: true);
    }

    private static string? Unprotect(string b64)
    {
        if (string.IsNullOrEmpty(b64)) return null;
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(b64), null, DataProtectionScope.CurrentUser)); }
        catch (Exception e) when (e is CryptographicException or FormatException) { return null; }
    }
}
