using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace LifeLog.Core;

/// <summary>
/// 수집기 앱 설정. %LOCALAPPDATA%\LifeLog\collector\config.json.
/// 기기 롤 비밀번호는 DPAPI(현재 사용자)로 암호화해 둔다. 관리자 접속 정보는 저장하지 않는다(Documents/인증·권한.md 3절).
/// </summary>
public sealed class Config
{
    /// <summary>앱 이름. 수집기는 "collector", 대시보드는 "dashboard". 시작 시 한 번 정한다.</summary>
    public static string AppName { get; set; } = "collector";
    private static string LocalAppData => Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
    /// <summary>자료 폴더 %LOCALAPPDATA%\SecondBrain\{collector,dashboard}. 2026-11-11 세컨뇌로 이름을 바꾸기 전에는 %LOCALAPPDATA%\LifeLog였다.</summary>
    public static string Root { get; } = Path.Combine(LocalAppData, "SecondBrain");
    public static string OldRoot { get; } = Path.Combine(LocalAppData, "LifeLog");

    /// <summary>
    /// 옛 폴더(LifeLog\collector·dashboard)를 새 곳으로 옮긴다. 새 곳에 이미 있으면 건드리지 않는다.
    /// 옛 수집기·대시보드가 파일을 잡고 있으면 IOException(먼저 멈춘다). LifeLog\vscode(VS Code 수집기가 쓰는 곳)는 그대로 둔다.
    /// </summary>
    public static void MoveFromOldRoot()
    {
        foreach (var app in new[] { "collector", "dashboard" })
        {
            var from = Path.Combine(OldRoot, app);
            var to = Path.Combine(Root, app);
            if (!Directory.Exists(from) || Directory.Exists(to)) continue;
            Directory.CreateDirectory(Root);
            Directory.Move(from, to);
        }
    }
    public static string Dir => Path.Combine(Root, AppName);
    public static string PluginsDir => Path.Combine(Dir, "plugins");
    public static string QueuePath => Path.Combine(Dir, "queue.db");
    private static string FilePath => Path.Combine(Dir, "config.json");

    [JsonPropertyName("device")] public string Device { get; set; } = "";
    [JsonPropertyName("host")] public string Host { get; set; } = "";
    [JsonPropertyName("port")] public int Port { get; set; } = 5432;
    [JsonPropertyName("database")] public string Database { get; set; } = "";
    [JsonPropertyName("ssl")] public bool Ssl { get; set; } = true;
    /// <summary>풀러 테넌트 접미사. Supabase 풀러는 사용자 이름이 `롤.프로젝트ref` 꼴이어야 테넌트를 안다. 맨 Postgres면 비워 둔다.</summary>
    [JsonPropertyName("tenant")] public string Tenant { get; set; } = "";
    /// <summary>DPAPI로 보호한 비밀번호(base64). 평문은 메모리에서만.</summary>
    [JsonPropertyName("password_protected")] public string PasswordProtected { get; set; } = "";

    public string User => "lifelog_device_" + Device + (string.IsNullOrEmpty(Tenant) ? "" : "." + Tenant);

    /// <summary>
    /// SecondBrainApi 주소(세컨뇌 DB 이전 7단계). 비어 있지 않으면 DB에 직접 붙지 않고 API를 거친다(<see cref="Db.Open"/>).
    /// 키는 종류 collector, 이름 = 기기 id. DPAPI로 보호해 둔다.
    /// </summary>
    [JsonPropertyName("api_url")] public string ApiUrl { get; set; } = "";
    [JsonPropertyName("api_key_protected")] public string ApiKeyProtected { get; set; } = "";

    [JsonIgnore] public bool UsesApi => !string.IsNullOrWhiteSpace(ApiUrl);

    public void SetApiKey(string plain)
        => ApiKeyProtected = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser));

    public string GetApiKey()
        => Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(ApiKeyProtected), null, DataProtectionScope.CurrentUser));

    public static Config? Load()
    {
        if (File.Exists(FilePath)) return JsonSerializer.Deserialize<Config>(File.ReadAllText(FilePath));
        // 같은 기기의 다른 앱(수집기 ↔ 대시보드)은 같은 기기 롤을 쓴다. DPAPI가 같은 사용자라 그대로 읽힌다.
        foreach (var other in new[] { "collector", "dashboard" })
        {
            var f = Path.Combine(Root, other, "config.json");
            if (other != AppName && File.Exists(f)) return JsonSerializer.Deserialize<Config>(File.ReadAllText(f));
        }
        return null;
    }

    public void Save()
    {
        Directory.CreateDirectory(Dir);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
    }

    public void SetPassword(string plain)
        => PasswordProtected = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser));

    public string GetPassword()
        => Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(PasswordProtected), null, DataProtectionScope.CurrentUser));

    /// <summary>Npgsql 키워드 형식. 비밀번호에 URL 예약 문자가 섞이므로 URL을 쓰지 않는다.</summary>
    public string ConnectionString()
    {
        var b = new Npgsql.NpgsqlConnectionStringBuilder
        {
            Host = Host, Port = Port, Database = Database, Username = User, Password = GetPassword(),
            SslMode = Ssl ? Npgsql.SslMode.Require : Npgsql.SslMode.Prefer,
            Pooling = true, MaxPoolSize = 2, MinPoolSize = 0,   // 앱당 연결 하나(풀러 연결 수 한도)
            ApplicationName = "secondbrain-collector",
        };
        return b.ConnectionString;
    }
}
