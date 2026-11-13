using System.IO.Compression;
using System.Text.Json;

namespace LifeLog.Core;

/// <summary>
/// 플러그인 설치(수집기·대시보드 공용). 출처는 폴더, .zip 파일, GitHub URL(https://github.com/소유자/저장소[/tree/브랜치[/폴더]]), .zip URL.
/// plugins/&lt;id&gt;로 복사하고 등록 함수를 부른다. URL이면 등록부의 source_url에 적는다(set_plugin_source).
/// </summary>
public static class PluginInstaller
{
    public sealed record Result(Manifest Manifest, string Dir, JsonElement Registered, JsonElement? Install);

    public static bool IsUrl(string source) => source.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || source.StartsWith("https://", StringComparison.OrdinalIgnoreCase);

    /// <summary>출처를 manifest.json이 있는 로컬 폴더로 만든다(URL·zip은 임시 폴더에 푼다).</summary>
    public static async Task<string> FetchAsync(string source, CancellationToken ct = default)
    {
        if (Directory.Exists(source)) return source;
        var tmp = Path.Combine(Path.GetTempPath(), "lifelog-plugin-" + Guid.NewGuid().ToString("N"));
        if (File.Exists(source) && source.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
        {
            ZipFile.ExtractToDirectory(source, tmp);
            return FindManifestDir(tmp, null);
        }
        if (IsUrl(source))
        {
            var (zipUrl, sub) = ResolveUrl(source);
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("lifelog-plugin-installer");
            var bytes = await http.GetByteArrayAsync(zipUrl, ct);
            var zipPath = tmp + ".zip";
            await File.WriteAllBytesAsync(zipPath, bytes, ct);
            try { ZipFile.ExtractToDirectory(zipPath, tmp); } finally { File.Delete(zipPath); }
            return FindManifestDir(tmp, sub);
        }
        throw new InvalidOperationException("플러그인 폴더·zip·GitHub URL이어야 합니다: " + source);
    }

    /// <summary>GitHub 저장소 URL → codeload zip URL과 하위 폴더. 그 밖의 .zip URL은 그대로.</summary>
    public static (string zipUrl, string? sub) ResolveUrl(string url)
    {
        var u = new Uri(url);
        if (u.Host.Equals("github.com", StringComparison.OrdinalIgnoreCase) || u.Host.Equals("www.github.com", StringComparison.OrdinalIgnoreCase))
        {
            var seg = u.AbsolutePath.Trim('/').Split('/', StringSplitOptions.RemoveEmptyEntries);
            if (seg.Length < 2) throw new InvalidOperationException("GitHub URL은 https://github.com/소유자/저장소 꼴이어야 합니다");
            var owner = seg[0];
            var repo = seg[1].EndsWith(".git", StringComparison.OrdinalIgnoreCase) ? seg[1][..^4] : seg[1];
            string? reff = null, sub = null;
            if (seg.Length >= 4 && (seg[2] == "tree" || seg[2] == "blob"))
            {
                reff = seg[3];
                if (seg.Length > 4) sub = string.Join('/', seg[4..]);
            }
            var zip = reff is null
                ? $"https://codeload.github.com/{owner}/{repo}/zip/HEAD"
                : $"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{reff}";
            return (zip, sub);
        }
        if (u.AbsolutePath.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)) return (url, null);
        throw new InvalidOperationException("GitHub 저장소 URL이나 .zip URL이어야 합니다: " + url);
    }

    private static string FindManifestDir(string root, string? sub)
    {
        // GitHub zip은 최상위 폴더 하나(저장소-브랜치/)로 싸여 있다
        var top = root;
        var entries = Directory.GetFileSystemEntries(root);
        if (entries.Length == 1 && Directory.Exists(entries[0])) top = entries[0];
        if (sub is not null)
        {
            var d = Path.Combine(top, sub.Replace('/', Path.DirectorySeparatorChar));
            if (File.Exists(Path.Combine(d, "manifest.json"))) return d;
            throw new InvalidOperationException($"{sub}에 manifest.json이 없습니다");
        }
        if (File.Exists(Path.Combine(top, "manifest.json"))) return top;
        var found = Directory.GetFiles(top, "manifest.json", SearchOption.AllDirectories);
        if (found.Length == 1) return Path.GetDirectoryName(found[0])!;
        throw new InvalidOperationException(found.Length == 0
            ? "manifest.json이 없습니다"
            : "manifest.json이 여럿입니다. URL 뒤에 /tree/<브랜치>/<폴더>를 붙여 하나를 고르세요");
    }

    /// <summary>이미 설치돼 있으면 그 설정 중 새 매니페스트가 선언한 키만(JSON). 없으면 null(전부 기본값).</summary>
    static async Task<string?> KeptSettingsAsync(Db db, Manifest m, string device, CancellationToken ct)
    {
        Db.Install? old;
        try { old = await db.GetInstallAsync(m.Id, device, ct); } catch (Exception) { return null; }
        if (old is null || old.Settings.ValueKind != JsonValueKind.Object) return null;
        var keys = m.Settings.Concat(m.Privacy).Select(d => d.Key).ToHashSet();
        var o = new Dictionary<string, JsonElement>();
        foreach (var p in old.Settings.EnumerateObject())
            if (keys.Contains(p.Name) && p.Value.ValueKind != JsonValueKind.Null) o[p.Name] = p.Value;
        return o.Count == 0 ? null : JsonSerializer.Serialize(o);
    }

    /// <summary>복사 → 등록 → (grantAll이면) 권한 전부 허용하고 켬(이미 설치돼 있으면 설정은 남긴다). expectType이 있으면 그 종류만 받는다.</summary>
    public static async Task<Result> InstallAsync(Db db, string device, string pluginsDir, string source, bool grantAll, string? expectType = null, CancellationToken ct = default)
    {
        var src = await FetchAsync(source, ct);
        var m = Manifest.Load(Path.Combine(src, "manifest.json"));
        if (expectType is not null && m.Type != expectType) throw new InvalidOperationException($"{expectType} 매니페스트가 아닙니다: {m.Type}");
        if (!m.Supports("windows")) throw new InvalidOperationException($"{m.Id}는 windows를 지원하지 않습니다(platforms: {string.Join(", ", m.Platforms!)})");
        var dest = Path.Combine(pluginsDir, m.Id);
        if (Directory.Exists(dest)) Directory.Delete(dest, true);
        Directory.CreateDirectory(dest);
        foreach (var f in Directory.GetFiles(src, "*", SearchOption.AllDirectories))
        {
            var rel = Path.GetRelativePath(src, f);
            if (rel.StartsWith(".git" + Path.DirectorySeparatorChar)) continue;
            var target = Path.Combine(dest, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(f, target, true);
        }
        var reg = await db.RegisterPluginAsync(m, device, ct);
        if (IsUrl(source)) await db.SetPluginSourceAsync(m.Id, source, ct);
        File.WriteAllText(Path.Combine(dest, ".source.json"), JsonSerializer.Serialize(new { source, installed_at = DateTime.UtcNow }));
        JsonElement? inst = null;
        if (grantAll)
        {
            var granted = JsonSerializer.Serialize(m.Capabilities.Select(c => c.Id));
            // 다시 설치(버전 올림)해도 이 기기의 기존 설정은 남긴다(2026-11-13). 새 매니페스트에 선언된 키만 넘기고, 타입이 바뀌어 거부되면 기본값으로
            var keep = await KeptSettingsAsync(db, m, device, ct);
            try { inst = await db.SetInstallAsync(m.Id, device, m.Version, true, keep, granted, ct); }
            catch (Exception) when (keep is not null) { inst = await db.SetInstallAsync(m.Id, device, m.Version, true, null, granted, ct); }
        }
        return new Result(m, dest, reg, inst);
    }
}
