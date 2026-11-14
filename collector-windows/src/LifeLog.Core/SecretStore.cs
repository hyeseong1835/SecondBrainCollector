using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace LifeLog.Core;

/// <summary>
/// 플러그인 비밀값(봇 토큰 등) 로컬 저장소(2026-11-11). 매니페스트의 `x-secrets`([{ key, label, description? }])가 선언한 키만 쓴다.
/// %LOCALAPPDATA%\SecondBrain\collector\secrets.json = { 플러그인id: { 키: DPAPI(현재 사용자) base64 } }.
/// DB·API·로그에 보내지 않는다. 같은 Windows 사용자인 수집기와 데스크톱 설정 화면이 같은 파일을 읽고 쓴다.
/// </summary>
public static class SecretStore
{
    public static string FilePath => Path.Combine(Config.Root, "collector", "secrets.json");
    private static readonly object Lock = new();

    private static JsonObject Load()
    {
        try { if (LocalPaths.ReadText(FilePath) is { } text) return JsonNode.Parse(text)?.AsObject() ?? new(); }
        catch (JsonException) { }
        return new();
    }

    public static bool Has(string plugin, string key)
    {
        lock (Lock) return Load()[plugin]?[key] is JsonValue;
    }

    /// <summary>평문. 없거나 풀 수 없으면 null(다른 사용자·기기에서 만든 파일).</summary>
    public static string? Get(string plugin, string key)
    {
        string? b64;
        lock (Lock) b64 = Load()[plugin]?[key]?.GetValue<string>();
        if (string.IsNullOrEmpty(b64)) return null;
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(b64), null, DataProtectionScope.CurrentUser)); }
        catch (CryptographicException) { return null; }
        catch (FormatException) { return null; }
    }

    /// <summary>value가 비면 지운다.</summary>
    public static void Set(string plugin, string key, string? value)
    {
        lock (Lock)
        {
            var all = Load();
            var p = all[plugin] as JsonObject ?? new JsonObject();
            if (string.IsNullOrEmpty(value)) p.Remove(key);
            else p[key] = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(value), null, DataProtectionScope.CurrentUser));
            if (p.Count == 0) all.Remove(plugin); else all[plugin] = p;
            LocalPaths.WriteTextAtomic(FilePath, all.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
    }

    public static DateTime FileTime => File.Exists(FilePath) ? File.GetLastWriteTimeUtc(FilePath) : DateTime.MinValue;
}
