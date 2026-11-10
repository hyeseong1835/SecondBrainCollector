using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using LifeLog.Core;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 디스코드 녹음 설정. %LOCALAPPDATA%\SecondBrain\collector\discord.json.
/// 봇 토큰과 대상 사용자 ID는 DPAPI(현재 사용자)로 보호해 둔다. 평문은 메모리에서만(출력·로그에 남기지 않는다).
/// </summary>
public sealed class DiscordSettings
{
    private static string FilePath => Path.Combine(Config.Dir, "discord.json");
    private static readonly object Lock = new();

    /// <summary>상시 녹음할 렌더 장치. 장치 ID(MMDevice.ID) 또는 이름 일부. 비면 discord-output 소스가 쉰다.</summary>
    [JsonPropertyName("output_device")] public string OutputDevice { get; set; } = "";
    /// <summary>봇 녹음 켬(트레이 메뉴·`discord-bot on|off`). 꺼진 채 시작한다.</summary>
    [JsonPropertyName("bot_enabled")] public bool BotEnabled { get; set; }
    [JsonPropertyName("bot_token_protected")] public string BotTokenProtected { get; set; } = "";
    [JsonPropertyName("target_user_protected")] public string TargetUserProtected { get; set; } = "";

    [JsonIgnore] public bool HasBotCredentials => BotTokenProtected.Length > 0 && TargetUserProtected.Length > 0;

    /// <summary>설정이 바뀌면(같은 프로세스 안) 알린다. 다른 프로세스(`discord-bot on`)의 변경은 소스가 파일 시각으로 알아챈다.</summary>
    public static event Action<DiscordSettings>? Changed;

    public static DiscordSettings Load()
    {
        lock (Lock)
        {
            try { if (File.Exists(FilePath)) return JsonSerializer.Deserialize<DiscordSettings>(File.ReadAllText(FilePath)) ?? new(); }
            catch (JsonException) { }
            return new();
        }
    }

    public static DateTime FileTime => File.Exists(FilePath) ? File.GetLastWriteTimeUtc(FilePath) : DateTime.MinValue;

    public void Save()
    {
        lock (Lock)
        {
            Directory.CreateDirectory(Config.Dir);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
        }
        Changed?.Invoke(this);
    }

    public static void Update(Action<DiscordSettings> change)
    {
        var s = Load();
        change(s);
        s.Save();
    }

    public void SetBotCredentials(string token, ulong targetUserId)
    {
        BotTokenProtected = Protect(token);
        TargetUserProtected = Protect(targetUserId.ToString());
    }

    public (string token, ulong targetUserId) GetBotCredentials()
        => (Unprotect(BotTokenProtected), ulong.Parse(Unprotect(TargetUserProtected)));

    private static string Protect(string plain)
        => Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser));
    private static string Unprotect(string b64)
        => Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(b64), null, DataProtectionScope.CurrentUser));
}
