using System.Text;

namespace LifeLog.Core;

/// <summary>
/// 수집기 자료 폴더(<c>%USERPROFILE%\.secondbrain\collector</c>)를 정하고, 옛 위치에서 베껴 온다(2026-11-14).
///
/// 왜 %LOCALAPPDATA%를 떠났나: Claude 데스크톱 앱(MSIX 패키지) 안의 셸에서 켠 프로세스는 %LOCALAPPDATA%·%APPDATA%를 패키지 사본
/// (<c>%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Local</c>)으로 본다. 읽기는 실제 폴더로 떨어지지만 쓰기는 사본에만 남는다.
/// 수집기 setup을 Claude 셸에서 했더니 config.json·secrets.json·queue.db가 사본에만 생겼고, 시작 메뉴·시작프로그램 바로가기·
/// 데스크톱 트레이 '켜기'로 켠 수집기(실제 환경)는 실제 %LOCALAPPDATA%\SecondBrain\collector가 없어 "설정 없음"으로 꺼졌다(로그 폴더도 없어 로그조차 못 남김).
/// 패키지 가상화는 AppData와 HKCU만 건드리고 사용자 프로필의 다른 폴더는 그대로라, 이 폴더는 어떻게 켜든 같은 파일을 본다.
/// 대시보드 폴더(%LOCALAPPDATA%\SecondBrain\dashboard)는 데스크톱이 실제 환경에서 쓰므로 그대로 둔다.
///
/// 비밀값(DPAPI 현재 사용자)은 Windows 사용자 키로 풀리므로 폴더만 옮기면 그대로 읽힌다.
/// 환경 변수 <see cref="EnvVar"/>로 다른 폴더를 줄 수 있다(시험용).
/// </summary>
public static class DataRoot
{
    public const string EnvVar = "SECONDBRAIN_DATA_DIR";
    /// <summary>실행 중인 수집기가 쥐는 뮤텍스. 다른 프로세스가 쥐고 있으면 옮기지 않는다(쓰는 중인 queue.db를 베끼지 않게).</summary>
    public const string CollectorMutexName = @"Local\SecondBrain.Collector.Running";
    const string MarkerName = "migrated-from.txt";
    const string App = "collector";

    /// <summary>자료 폴더. 기본 %USERPROFILE%\.secondbrain.</summary>
    public static string Path { get; } = Resolve();
    /// <summary>수집기 자료 폴더 Path\collector.</summary>
    public static string CollectorDir => System.IO.Path.Combine(Path, App);

    static string Resolve()
    {
        if (Environment.GetEnvironmentVariable(EnvVar) is { Length: > 0 } env) return System.IO.Path.GetFullPath(env);
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (string.IsNullOrWhiteSpace(profile)) profile = Environment.GetEnvironmentVariable("USERPROFILE") ?? System.IO.Path.GetTempPath();
        return System.IO.Path.Combine(profile, ".secondbrain");
    }

    public sealed record Migration(string From, string To, int Files, string? Error);

    /// <summary>
    /// 옛 위치에서 베끼기를 할지. 뮤텍스를 쥔 수집기 run만 켠다(기본 꺼짐). 데스크톱·시험·다른 명령이 먼저 베끼면, 아직 옛 빌드 수집기가
    /// 옛 위치에 계속 쓰는 동안 새 폴더가 낡은 사본으로 굳는다(새 폴더에 설정이 있으면 다시 베끼지 않으므로).
    /// </summary>
    public static bool MigrationEnabled { get; set; }

    /// <summary>수집기 run이 뮤텍스를 쥔 뒤 true로 둔다.</summary>
    public static bool OwnsCollectorLock { get; set; }

    /// <summary>베끼지 않는 하위 폴더(캐시).</summary>
    static readonly HashSet<string> SkipDirs = new(StringComparer.OrdinalIgnoreCase) { "webview2", "EBWebView", "cache" };

    static readonly object Gate = new();
    static readonly List<Migration> Done = [];
    static bool _finished;
    static DateTime _lastTry = DateTime.MinValue;

    /// <summary>이번 프로세스에서 옮긴 기록(로그에 남기려고).</summary>
    public static IReadOnlyList<Migration> Migrations { get { lock (Gate) return Done.ToList(); } }

    /// <summary>
    /// <see cref="MigrationEnabled"/>이고 새 폴더에 config.json이 없으면 옛 후보에서 폴더째 베껴 온다. 여러 번 불러도 된다(못 했으면 30초마다 다시 본다).
    /// 원본은 지우지 않는다(되돌릴 수 있게). 원본 쪽에 MOVED-TO.txt, 새 폴더에 migrated-from.txt를 남긴다.
    /// </summary>
    public static void EnsureMigrated()
    {
        lock (Gate)
        {
            if (!MigrationEnabled || _finished) return;
            if (Environment.GetEnvironmentVariable("SECONDBRAIN_NO_MIGRATE") == "1") return;   // 시험용: 빈 자료 폴더로 켜 보기
            if (DateTime.UtcNow - _lastTry < TimeSpan.FromSeconds(30)) return;
            _lastTry = DateTime.UtcNow;
            var to = CollectorDir;
            if (File.Exists(System.IO.Path.Combine(to, "config.json"))) { _finished = true; return; }
            var from = FindLegacy();
            if (from is null) return;   // 아직 setup 전. 나중에 생길 수 있다
            if (CollectorRunningElsewhere()) return;
            try
            {
                var n = CopyDir(from, to);
                File.WriteAllText(System.IO.Path.Combine(to, MarkerName),
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {from} 에서 베껴 옴(원본은 그대로 둠). pid {Environment.ProcessId}{Environment.NewLine}", new UTF8Encoding(false));
                TryWrite(System.IO.Path.Combine(from, "MOVED-TO.txt"),
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} 이 폴더는 더 쓰지 않습니다. 새 위치: {to}{Environment.NewLine}");
                Done.Add(new(from, to, n, null));
                _finished = true;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                Done.Add(new(from, to, 0, e.Message));
            }
        }
    }

    /// <summary>베껴 올 수 있는 옛 설정 폴더(config.json이 있는 첫 후보, 없으면 null). 진단·안내용, 베끼지는 않는다.</summary>
    public static string? FindLegacy() =>
        LegacyCandidates().FirstOrDefault(d => LocalPaths.Check(System.IO.Path.Combine(d, "config.json")).State == LocalPaths.FileState.Ok);

    /// <summary>
    /// 옛 위치 후보(앞이 우선): 실제 %LOCALAPPDATA%\SecondBrain\collector → Claude 패키지 사본들(설정 파일이 최근인 것부터) → 옛 LifeLog 이름.
    /// Claude 셸 안에서는 첫 후보가 이미 사본과 겹쳐 보인다.
    /// </summary>
    public static IReadOnlyList<string> LegacyCandidates()
    {
        var local = LocalPaths.LocalAppData;
        var bases = new List<string> { local };
        try
        {
            var packages = System.IO.Path.Combine(local, "Packages");
            if (Directory.Exists(packages))
                bases.AddRange(Directory.EnumerateDirectories(packages, "Claude_*")
                    .Select(p => System.IO.Path.Combine(p, "LocalCache", "Local"))
                    .Where(Directory.Exists)
                    .OrderByDescending(p => LastWrite(System.IO.Path.Combine(p, "SecondBrain", App, "config.json"))));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        var list = new List<string>();
        foreach (var name in new[] { "SecondBrain", "LifeLog" })
            foreach (var b in bases)
                list.Add(System.IO.Path.Combine(b, name, App));
        return list.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
    }

    static DateTime LastWrite(string f) { try { return File.Exists(f) ? File.GetLastWriteTimeUtc(f) : DateTime.MinValue; } catch { return DateTime.MinValue; } }

    static bool CollectorRunningElsewhere()
    {
        if (OwnsCollectorLock || !OperatingSystem.IsWindows()) return false;
        if (!Mutex.TryOpenExisting(CollectorMutexName, out var m)) return false;
        using (m)
        {
            // 다른 프로세스가 쥐고 있으면 WaitOne(0)이 false.
            try { var mine = m.WaitOne(0); if (mine) m.ReleaseMutex(); return !mine; }
            catch (AbandonedMutexException) { try { m.ReleaseMutex(); } catch { } return false; }
        }
    }

    /// <summary>폴더를 통째로 베낀다. 이미 있는 파일은 덮지 않는다. *.tmp·캐시 폴더는 건너뛴다. 다른 프로세스가 쓰는 중이어도 읽는다.</summary>
    static int CopyDir(string from, string to)
    {
        var n = 0;
        Directory.CreateDirectory(to);
        foreach (var f in Directory.EnumerateFiles(from, "*", SearchOption.AllDirectories))
        {
            var name = System.IO.Path.GetFileName(f);
            if (name.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase) || name == "MOVED-TO.txt") continue;
            var rel = System.IO.Path.GetRelativePath(from, f);
            if (rel.Split(System.IO.Path.DirectorySeparatorChar).SkipLast(1).Any(SkipDirs.Contains)) continue;
            var dest = System.IO.Path.Combine(to, rel);
            if (File.Exists(dest)) continue;
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(dest)!);
            using (var src = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var dst = new FileStream(dest, FileMode.CreateNew, FileAccess.Write))
                src.CopyTo(dst);
            File.SetLastWriteTimeUtc(dest, File.GetLastWriteTimeUtc(f));
            n++;
        }
        return n;
    }

    static void TryWrite(string path, string text) { try { File.AppendAllText(path, text, new UTF8Encoding(false)); } catch { } }

    // ───────── 진단 ─────────

    /// <summary>
    /// 이 프로세스의 %LOCALAPPDATA% 쓰기가 Claude 앱 패키지 사본으로 가는지 알아본다(작은 파일을 써 보고 사본 쪽에 생기는지 본다).
    /// 그렇다면 사본 경로, 아니면 null. 패키지 이름 API(GetCurrentPackageFullName)는 Claude 셸에서도 "패키지 아님"이라 쓸 수 없었다.
    /// </summary>
    public static string? AppDataOverlay()
    {
        try
        {
            var local = LocalPaths.LocalAppData;
            var packages = System.IO.Path.Combine(local, "Packages");
            if (!Directory.Exists(packages)) return null;
            var name = $".overlay-probe-{Environment.ProcessId}";
            var probe = System.IO.Path.Combine(local, name);
            File.WriteAllText(probe, "");
            try
            {
                foreach (var pkg in Directory.EnumerateDirectories(packages, "Claude_*"))
                {
                    var copy = System.IO.Path.Combine(pkg, "LocalCache", "Local");
                    if (File.Exists(System.IO.Path.Combine(copy, name))) return copy;
                }
                return null;
            }
            finally { try { File.Delete(probe); } catch { } }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return null; }
    }

    /// <summary>시작할 때 로그에 남길 한 줄: 자료 폴더, 실행 환경, 옛 후보 중 설정이 있는 곳.</summary>
    public static string Describe()
    {
        var overlay = AppDataOverlay();
        var withConfig = LegacyCandidates()
            .Where(d => LocalPaths.Check(System.IO.Path.Combine(d, "config.json")).State == LocalPaths.FileState.Ok).ToList();
        return $"자료 폴더 {CollectorDir}" +
               (overlay is null ? " · AppData 실제" : $" · AppData가 Claude 앱 사본({overlay})으로 보임(Claude 셸에서 켬)") +
               $" · exe {Environment.ProcessPath}" +
               (withConfig.Count == 0 ? "" : " · 옛 위치의 설정: " + string.Join(", ", withConfig));
    }
}
