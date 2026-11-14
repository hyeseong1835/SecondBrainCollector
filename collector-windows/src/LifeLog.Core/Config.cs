using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace LifeLog.Core;

/// <summary>
/// 수집기 앱 설정. %USERPROFILE%\.secondbrain\collector\config.json(<see cref="DataRoot"/>).
/// 기기 롤 비밀번호는 DPAPI(현재 사용자)로 암호화해 둔다. 관리자 접속 정보는 저장하지 않는다(Documents/인증·권한.md 3절).
/// </summary>
public sealed class Config
{
    /// <summary>앱 이름. 수집기는 "collector", 대시보드는 "dashboard". 시작 시 한 번 정한다.</summary>
    public static string AppName { get; set; } = "collector";
    private static string LocalAppData => LocalPaths.LocalAppData;
    /// <summary>
    /// 자료 폴더의 부모 %USERPROFILE%\.secondbrain(<see cref="DataRoot.Path"/>, 2026-11-14). 그 아래 collector·dashboard·desktop·analysis.
    /// 그 전에는 %LOCALAPPDATA%\SecondBrain(2026-11-11 이름을 바꾸기 전에는 %LOCALAPPDATA%\LifeLog)였다. 옛 대시보드 폴더는
    /// 데스크톱이 <see cref="DataAreas.MigrateAll"/>로 베껴 온다.
    /// </summary>
    public static string Root => DataRoot.Path;
    public static string OldRoot { get; } = Path.Combine(LocalAppData, "LifeLog");

    /// <summary>
    /// 수집기 자료 폴더 %USERPROFILE%\.secondbrain\collector(<see cref="DataRoot"/>, 2026-11-14). 그 전에는 %LOCALAPPDATA%\SecondBrain\collector였다.
    /// Claude 앱 셸에서 쓴 AppData가 패키지 사본에만 남아 바로가기로 켠 수집기가 설정을 못 찾았다. 옛 위치에서 베끼는 일은 수집기 run만 한다.
    /// </summary>
    public static string CollectorDir { get { DataRoot.EnsureMigrated(); return DataRoot.CollectorDir; } }

    /// <summary>
    /// 예전: 옛 대시보드 폴더(LifeLog\dashboard)를 Root\dashboard로 옮겼다. 2026-11-14부터 대시보드 폴더는 데스크톱이
    /// <see cref="DataAreas.MigrateAll"/>로 (실제 AppData → Claude 사본 → LifeLog 순으로) 베껴 오므로 아무것도 하지 않는다. 옛 호출자를 위해 남긴다.
    /// </summary>
    public static void MoveFromOldRoot() { }
    public static string Dir => AppName == "collector" ? CollectorDir : Path.Combine(Root, AppName);
    public static string PluginsDir => Path.Combine(Dir, "plugins");
    public static string QueuePath => Path.Combine(Dir, "queue.db");
    private static string FilePath => Path.Combine(Dir, "config.json");
    /// <summary>수집기 설정 파일(진단 화면용). 대시보드도 이 파일을 먼저 본다.</summary>
    public static string CollectorConfigPath => Path.Combine(CollectorDir, "config.json");

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
    /// SecondBrainApi 주소(SecondBrain DB 이전 7단계). 비어 있지 않으면 DB에 직접 붙지 않고 API를 거친다(<see cref="Db.Open"/>).
    /// 키는 종류 collector, 이름 = 기기 id. DPAPI로 보호해 둔다.
    /// </summary>
    [JsonPropertyName("api_url")] public string ApiUrl { get; set; } = "";
    [JsonPropertyName("api_key_protected")] public string ApiKeyProtected { get; set; } = "";

    [JsonIgnore] public bool UsesApi => !string.IsNullOrWhiteSpace(ApiUrl);

    public void SetApiKey(string plain)
        => ApiKeyProtected = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(plain), null, DataProtectionScope.CurrentUser));

    public string GetApiKey()
        => Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(ApiKeyProtected), null, DataProtectionScope.CurrentUser));

    /// <summary>
    /// 설정을 읽는다. 없으면 null. 접근이 막혔으면(샌드박스 등) 원인 안내가 붙은 <see cref="LocalFileDeniedException"/>,
    /// 깨진 JSON이면 경로가 붙은 InvalidOperationException. 예전에는 File.Exists가 막힘을 "없음"으로 돌려 "setup을 먼저 하세요"가 떴다.
    /// </summary>
    public static Config? Load()
    {
        var paths = new List<string> { FilePath };
        // 같은 기기의 다른 앱(수집기 ↔ 대시보드)은 같은 기기 롤을 쓴다. DPAPI가 같은 사용자라 그대로 읽힌다.
        paths.Add(AppName == "collector" ? Path.Combine(Root, "dashboard", "config.json") : CollectorConfigPath);
        foreach (var f in paths)
        {
            var text = LocalPaths.ReadText(f);
            if (text is null) continue;
            try { return JsonSerializer.Deserialize<Config>(text); }
            catch (JsonException e) { throw new InvalidOperationException($"설정 파일이 깨졌습니다({f}): {e.Message}", e); }
        }
        return null;
    }

    /// <summary>설정이 없을 때 보일 글. 경로와, 옛 위치에 설정이 남아 있으면 그곳과 옮기는 방법을 붙인다.</summary>
    public static string MissingHint()
    {
        var failed = DataRoot.Migrations.LastOrDefault(m => m.Error is not null);
        var legacy = DataRoot.FindLegacy();
        return $"수집기 설정이 없습니다({CollectorConfigPath})." +
               (failed is not null ? $" 옛 설정({failed.From})을 옮기지 못했습니다: {failed.Error}."
                : legacy is not null ? $" 옛 설정({legacy})이 있습니다. 다른 수집기가 돌고 있으면 끄고(stop) `run --tray`로 다시 켜면 옮깁니다."
                : " 수집기 setup(또는 use-api)을 먼저 하세요.");
    }

    public void Save() => LocalPaths.WriteTextAtomic(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));

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
