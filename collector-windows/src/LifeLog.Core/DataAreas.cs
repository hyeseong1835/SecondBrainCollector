using System.Text;

namespace LifeLog.Core;

/// <summary>
/// 데스크톱·분석 스크립트·훅이 쓰는 이 PC의 설정을 <see cref="DataRoot.Path"/>(<c>%USERPROFILE%\.secondbrain</c>) 아래로 모은다(2026-11-14).
/// 수집기 폴더(<see cref="DataRoot.CollectorDir"/>)와 같은 이유: Claude 앱 셸에서 %LOCALAPPDATA%에 쓰면 패키지 사본에만 남아
/// 바로가기로 켠 앱(실제 환경)이 못 본다. 사용자 프로필의 다른 폴더는 가상화되지 않는다.
///
/// | 무엇 | 새 위치 | 옛 위치 |
/// |---|---|---|
/// | 데스크톱 상태·API 키·LMS 자격 증명(LMS 브라우저 프로필 web\은 새로 만듦) | .secondbrain\desktop | %LOCALAPPDATA%\SecondBrainDesktop |
/// | 기기 설정(하루 시작 등)·대시보드 플러그인 | .secondbrain\dashboard | %LOCALAPPDATA%\SecondBrain\dashboard (더 옛: LifeLog\dashboard) |
/// | 분석 설정·전사 대기열·실행 잠금 | .secondbrain\analysis | %LOCALAPPDATA%\SecondBrain\analysis\*.json |
/// | 스크립트·훅용 API 주소·키(api.json), 폰 수집기 키 | .secondbrain\api.json 등 | %LOCALAPPDATA%\SecondBrain\ |
///
/// 분석 도구·모델(whisper·llama·ffmpeg, 수 GB)은 옮기지 않는다: <see cref="AnalysisToolsDir"/>.
/// 옛 위치에서 베끼는 일은 평소 데스크톱(단일 실행 잠금을 쥔 인스턴스)만 한다(<see cref="MigrateAll"/>). 원본은 두고 MOVED-TO.txt,
/// 새 폴더에 migrated-from.txt를 남긴다. 비밀값(DPAPI 현재 사용자)은 Windows 사용자 키로 풀리므로 그대로 읽힌다.
/// 다른 프로그램(스크립트·훅)은 새 위치를 먼저 보고 없으면 옛 위치를 본다.
/// </summary>
public static class DataAreas
{
    public const string Marker = "migrated-from.txt";

    public static string DesktopDir => Path.Combine(DataRoot.Path, "desktop");
    public static string DashboardDir => Path.Combine(DataRoot.Path, "dashboard");
    /// <summary>분석의 작은 상태 파일(settings.json·queue.json·analyze.lock).</summary>
    public static string AnalysisDir => Path.Combine(DataRoot.Path, "analysis");
    /// <summary>스크립트·훅이 읽는 API 주소·키({"url","key"}).</summary>
    public static string ApiConfigPath => Path.Combine(DataRoot.Path, "api.json");
    /// <summary>
    /// 분석 도구·모델 폴더. 크고(수 GB) 설치한 곳에 그대로 둔다(베끼지 않음).
    /// 후보(앞이 우선, models 폴더가 있는 첫 곳): .secondbrain\analysis(나중에 옮겨 둔 경우) → 실제 %LOCALAPPDATA%\SecondBrain\analysis →
    /// Claude 패키지 사본의 SecondBrain\analysis. 2026-11-14 확인: 도구를 Claude 셸에서 받아 사본에만 있었고 실제 AppData엔 analysis 폴더가 없었다.
    /// 사본 경로는 보통 폴더라 실제 환경에서도 그대로 읽힌다. 아무 데도 없으면 실제 AppData 자리.
    /// </summary>
    public static string AnalysisToolsDir => _tools ??= FindTools();
    static string? _tools;

    /// <summary>도구 폴더 후보(진단·시험용).</summary>
    public static IReadOnlyList<string> AnalysisToolsCandidates() =>
        new[] { AnalysisDir }.Concat(LegacyBases().Select(b => Path.Combine(b, "SecondBrain", "analysis")))
            .Distinct(StringComparer.OrdinalIgnoreCase).ToList();

    static string FindTools()
    {
        if (Environment.GetEnvironmentVariable("SECONDBRAIN_ANALYSIS_TOOLS") is { Length: > 0 } env) return Path.GetFullPath(env);
        foreach (var c in AnalysisToolsCandidates())
            try { if (Directory.Exists(Path.Combine(c, "models"))) return c; } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        return Path.Combine(LocalPaths.LocalAppData, "SecondBrain", "analysis");
    }

    /// <summary>예전 분석 잠금 파일(%LOCALAPPDATA%\SecondBrain\analysis\analyze.lock). 옛 빌드 스크립트가 아직 이 자리를 쓸 수 있어 '돌고 있나'를 볼 때 같이 본다.</summary>
    public static string LegacyAnalyzeLock => Path.Combine(LocalPaths.LocalAppData, "SecondBrain", "analysis", "analyze.lock");

    /// <summary>
    /// 옮길 한 덩어리. Legacy는 앞이 우선(실제 AppData → Claude 사본들 → 옛 LifeLog 이름). HasData(폴더)가 참인 첫 후보에서 베낀다.
    /// Include(상대 경로)가 거짓인 파일은 건너뛴다. Recursive=false면 맨 위 파일만.
    /// </summary>
    public sealed record Area(string Name, string To, IReadOnlyList<string> Legacy, string[] KeyFiles, bool Recursive, Func<string, bool> Include);

    static readonly HashSet<string> CacheDirs = new(StringComparer.OrdinalIgnoreCase)
    {
        "webview2", "EBWebView", "Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache", "GraphiteDawnCache",
        "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "Crashpad", "CacheStorage", "ScriptCache", "blob_storage",
    };

    static bool NotCache(string rel) =>
        !rel.Split(Path.DirectorySeparatorChar).SkipLast(1).Any(CacheDirs.Contains);

    static bool NotTemp(string rel)
    {
        var name = Path.GetFileName(rel);
        return !name.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase) && name != "MOVED-TO.txt" && name != Marker
               && !name.Equals("analyze.lock", StringComparison.OrdinalIgnoreCase) && !name.StartsWith(".overlay-probe", StringComparison.Ordinal);
    }

    /// <summary>
    /// 옛 위치의 바탕 폴더(%LOCALAPPDATA% 자리): 실제 AppData → Claude 패키지 사본들(최근에 바뀐 것부터).
    /// Claude 셸 안에서는 첫 후보가 이미 사본과 겹쳐 보인다.
    /// </summary>
    public static IReadOnlyList<string> LegacyBases()
    {
        var local = LocalPaths.LocalAppData;
        var bases = new List<string> { local };
        try
        {
            var packages = Path.Combine(local, "Packages");
            if (Directory.Exists(packages))
                bases.AddRange(Directory.EnumerateDirectories(packages, "Claude_*")
                    .Select(p => Path.Combine(p, "LocalCache", "Local"))
                    .Where(Directory.Exists)
                    .OrderByDescending(p => Max(LastWrite(Path.Combine(p, "SecondBrain")), LastWrite(Path.Combine(p, "SecondBrainDesktop")))));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        return bases;
    }

    static DateTime Max(DateTime a, DateTime b) => a > b ? a : b;
    static DateTime LastWrite(string d) { try { return Directory.Exists(d) ? Directory.GetLastWriteTimeUtc(d) : DateTime.MinValue; } catch { return DateTime.MinValue; } }

    /// <summary>옮길 덩어리들. bases를 주면 그 바탕 폴더들로(시험용).</summary>
    public static IReadOnlyList<Area> Areas(IReadOnlyList<string>? bases = null, string? root = null)
    {
        bases ??= LegacyBases();
        root ??= DataRoot.Path;
        IReadOnlyList<string> Under(params string[][] rels) =>
            rels.SelectMany(r => bases.Select(b => Path.Combine([b, .. r]))).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        return
        [
            new("desktop", Path.Combine(root, "desktop"), Under(["SecondBrainDesktop"]),
                ["state.json", "api-key.bin", "lms-credential.bin"], false, NotTemp),   // web\(LMS 브라우저 프로필)은 베끼지 않는다: 세션 쿠키라 다시 로그인
            new("dashboard", Path.Combine(root, "dashboard"), Under(["SecondBrain", "dashboard"], ["LifeLog", "dashboard"]),
                ["settings.json", "config.json"], true, r => NotTemp(r) && NotCache(r)),
            new("analysis", Path.Combine(root, "analysis"), Under(["SecondBrain", "analysis"]),
                ["settings.json", "queue.json"], false, r => NotTemp(r) && r.EndsWith(".json", StringComparison.OrdinalIgnoreCase)),
            new("api", root, Under(["SecondBrain"]),
                ["api.json"], false, r => r is "api.json" or "collector-key-phone.bin"),
        ];
    }

    static readonly object Gate = new();
    static readonly List<DataRoot.Migration> Done = [];

    /// <summary>이번 프로세스에서 옮긴 기록(진단 화면·로그).</summary>
    public static IReadOnlyList<DataRoot.Migration> Migrations { get { lock (Gate) return Done.ToList(); } }

    /// <summary>
    /// 모든 덩어리를 옮긴다. 평소 데스크톱이 단일 실행 잠금을 쥔 뒤 한 번 부른다(시험 인스턴스·Claude 셸 시험은 부르지 않는다:
    /// 옛 빌드가 옛 위치에 계속 쓰는 동안 새 폴더가 낡은 사본으로 굳지 않게). SECONDBRAIN_NO_MIGRATE=1이면 안 한다.
    /// </summary>
    public static IReadOnlyList<DataRoot.Migration> MigrateAll()
    {
        if (Environment.GetEnvironmentVariable("SECONDBRAIN_NO_MIGRATE") == "1") return [];
        var list = new List<DataRoot.Migration>();
        foreach (var a in Areas())
            if (Migrate(a) is { } m) list.Add(m);
        lock (Gate) Done.AddRange(list);
        return list;
    }

    /// <summary>
    /// 한 덩어리를 옮긴다. 새 폴더에 표식(migrated-from.txt, api는 migrated-from.api.txt)이 있으면 이미 한 것.
    /// 아니면 열쇠 파일이 있는 옛 후보들에서 앞 순서대로 새 폴더에 아직 없는 파일만 베낀다(있는 파일은 덮지 않는다).
    /// 그래서 같은 이름이면 실제 AppData 것이 이기고, 사본에만 있는 파일(예: Claude 셸에서 켠 앱이 저장한 LMS 자격 증명)도 채워진다.
    /// 옮길 것이 없으면 null.
    /// </summary>
    public static DataRoot.Migration? Migrate(Area a)
    {
        var marker = MarkerPath(a);
        if (File.Exists(marker)) return null;
        var to = Path.GetFullPath(a.To).TrimEnd('\\');
        var sources = a.Legacy
            .Where(d => !string.Equals(Path.GetFullPath(d).TrimEnd('\\'), to, StringComparison.OrdinalIgnoreCase))
            .Where(d => a.KeyFiles.Any(k => LocalPaths.Check(Path.Combine(d, k)).State == LocalPaths.FileState.Ok))
            .ToList();
        if (sources.Count == 0) return null;
        var from = string.Join(" + ", sources);
        try
        {
            var n = 0;
            var log = new StringBuilder();
            foreach (var s in sources)
            {
                var k = CopyDir(s, a.To, a.Recursive, a.Include);
                n += k;
                log.Append($"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {s} 에서 {a.Name} 설정 {k}개를 베껴 옴(원본은 그대로 둠). pid {Environment.ProcessId}{Environment.NewLine}");
                TryAppend(Path.Combine(s, "MOVED-TO.txt"),
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {a.Name} 설정은 더 여기서 쓰지 않습니다. 새 위치: {a.To}" +
                    (a.Name == "analysis" ? " (도구·모델은 이 폴더에 그대로)" : a.Name == "api" ? " (api.json·collector-key-phone.bin만)" : "") + Environment.NewLine);
            }
            File.WriteAllText(marker, log.ToString(), new UTF8Encoding(false));
            return new(from, a.To, n, null);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return new(from, a.To, 0, e.Message);
        }
    }

    /// <summary>옮긴 표식 파일. api 덩어리는 설정 폴더 맨 위라 이름을 달리한다.</summary>
    public static string MarkerPath(Area a) => Path.Combine(a.To, a.Name == "api" ? "migrated-from.api.txt" : Marker);

    static int CopyDir(string from, string to, bool recursive, Func<string, bool> include)
    {
        var n = 0;
        Directory.CreateDirectory(to);
        foreach (var f in Directory.EnumerateFiles(from, "*", recursive ? SearchOption.AllDirectories : SearchOption.TopDirectoryOnly))
        {
            var rel = Path.GetRelativePath(from, f);
            if (!include(rel)) continue;
            var dest = Path.Combine(to, rel);
            if (File.Exists(dest)) continue;
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            try
            {
                using (var src = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var dst = new FileStream(dest, FileMode.CreateNew, FileAccess.Write))
                    src.CopyTo(dst);
                File.SetLastWriteTimeUtc(dest, File.GetLastWriteTimeUtc(f));
                n++;
            }
            catch (IOException) when (recursive && !IsKeyLike(rel))
            {
                // 브라우저 프로필 안의 잠긴 파일 등은 건너뛴다(없어도 다시 만들어짐). 맨 위 설정 파일이 안 되면 실패로 올린다.
                TryDelete(dest);
            }
        }
        return n;
    }

    static bool IsKeyLike(string rel) => !rel.Contains(Path.DirectorySeparatorChar);

    static void TryAppend(string path, string text) { try { File.AppendAllText(path, text, new UTF8Encoding(false)); } catch { } }
    static void TryDelete(string path) { try { File.Delete(path); } catch { } }

    /// <summary>읽을 파일: 새 위치에 있으면 그것, 없고 옛 위치에 있으면 옛 것, 둘 다 없으면 새 위치. 스크립트와 같은 규칙.</summary>
    public static string PreferNew(string newPath, string legacyPath) =>
        LocalPaths.Check(newPath).State != LocalPaths.FileState.Missing ? newPath
        : LocalPaths.Check(legacyPath).State != LocalPaths.FileState.Missing ? legacyPath : newPath;
}
