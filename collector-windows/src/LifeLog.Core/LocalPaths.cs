using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

namespace LifeLog.Core;

/// <summary>
/// 이 PC의 설정 폴더(%LOCALAPPDATA%)를 한 곳에서 정한다(2026-11-14 설정 통합).
/// 수집기·데스크톱이 "설정이 없다"·"수집기 기록을 읽지 못함"을 낸 일이 여러 번 있었다. 파일은 제자리에 있었고,
/// 앱이 Claude 세션(샌드박스) 안에서 켜져 경로가 다르게 보이거나 읽기가 막힌 것이 원인으로 보였다.
/// 그래서 후보 경로 여럿(Known Folder → 사용자 SID의 프로필 경로 → 환경 변수 → 프로필\AppData\Local) 중
/// SecondBrain 폴더가 실제로 있는 곳을 고르고, 고른 경로와 이유를 화면에 보여 줄 수 있게 남긴다.
/// </summary>
public static class LocalPaths
{
    public sealed record Candidate(string Source, string Path, bool HasSecondBrain);

    static readonly Lazy<(string Path, string Source, List<Candidate> All)> Resolved = new(Resolve);

    /// <summary>%LOCALAPPDATA%에 해당하는 폴더(SecondBrain 폴더가 있는 후보를 우선).</summary>
    public static string LocalAppData => Resolved.Value.Path;

    /// <summary>LocalAppData를 어디서 얻었는지(known-folder, profile-list, env, user-profile).</summary>
    public static string Source => Resolved.Value.Source;

    /// <summary>살펴본 후보 전부(진단 화면용).</summary>
    public static IReadOnlyList<Candidate> Candidates => Resolved.Value.All;

    /// <summary>후보끼리 경로가 다르면 true. 샌드박스·다른 사용자 환경을 의심할 근거.</summary>
    public static bool CandidatesDisagree =>
        Candidates.Select(c => Normalize(c.Path)).Distinct(StringComparer.OrdinalIgnoreCase).Count() > 1;

    static (string, string, List<Candidate>) Resolve()
    {
        var all = new List<Candidate>();
        void Add(string source, string? path)
        {
            if (string.IsNullOrWhiteSpace(path)) return;
            try { path = System.IO.Path.GetFullPath(path); } catch (Exception e) when (e is ArgumentException or NotSupportedException or PathTooLongException) { return; }
            if (all.Any(c => c.Source == source)) return;
            bool has;
            try { has = Directory.Exists(System.IO.Path.Combine(path, "SecondBrain")); } catch { has = false; }
            all.Add(new Candidate(source, path, has));
        }
        Add("known-folder", KnownFolder());
        Add("profile-list", ProfileListLocal());
        Add("env", Environment.GetEnvironmentVariable("LOCALAPPDATA"));
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!string.IsNullOrWhiteSpace(profile)) Add("user-profile", System.IO.Path.Combine(profile, "AppData", "Local"));
        var pick = all.FirstOrDefault(c => c.HasSecondBrain) ?? all.FirstOrDefault()
                   ?? new Candidate("temp", System.IO.Path.GetTempPath(), false);
        return (pick.Path, pick.Source, all);
    }

    static string Normalize(string p) => p.TrimEnd('\\', '/');

    static string? KnownFolder()
    {
        try { return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolderOption.DoNotVerify); }
        catch (PlatformNotSupportedException) { return null; }
    }

    /// <summary>HKLM ProfileList\{SID}\ProfileImagePath\AppData\Local. 환경 변수·셸 폴더 설정과 무관한 실제 프로필.</summary>
    static string? ProfileListLocal()
    {
        if (!OperatingSystem.IsWindows()) return null;
        try
        {
            var sid = WindowsIdentity.GetCurrent().User?.Value;
            if (sid is null) return null;
            using var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey($@"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\{sid}");
            if (key?.GetValue("ProfileImagePath") is not string img || img.Length == 0) return null;
            return System.IO.Path.Combine(Environment.ExpandEnvironmentVariables(img), "AppData", "Local");
        }
        catch (Exception e) when (e is System.Security.SecurityException or UnauthorizedAccessException or IOException) { return null; }
    }

    // ───────── 샌드박스 감지 ─────────

    /// <summary>
    /// 이 프로세스가 사용자 셸이 아닌 곳(Claude 세션·제한 토큰·AppContainer)에서 켜진 것 같으면 그 이유. 아니면 null.
    /// 앱을 Claude 세션에서 띄우면 그 세션의 환경 변수(CLAUDECODE 등)를 물려받는다.
    /// </summary>
    public static string? SandboxReason()
    {
        var reasons = new List<string>();
        if (Environment.GetEnvironmentVariable("CLAUDECODE") is { Length: > 0 } || Environment.GetEnvironmentVariable("CLAUDE_CODE_ENTRYPOINT") is { Length: > 0 })
            reasons.Add("Claude 세션에서 켜짐");
        if (OperatingSystem.IsWindows())
        {
            try
            {
                using var id = WindowsIdentity.GetCurrent();
                if (IsTokenRestricted(id.Token)) reasons.Add("제한된 토큰");
                if (IsAppContainer(id.Token)) reasons.Add("AppContainer");
            }
            catch (Exception e) when (e is System.Security.SecurityException or UnauthorizedAccessException) { }
        }
        if (CandidatesDisagree) reasons.Add("프로필 경로가 다르게 보임");
        return reasons.Count == 0 ? null : string.Join(", ", reasons);
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool IsTokenRestricted(IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetTokenInformation(IntPtr token, int infoClass, out int info, int length, out int returned);

    static bool IsAppContainer(IntPtr token) =>
        GetTokenInformation(token, 29 /* TokenIsAppContainer */, out var v, sizeof(int), out _) && v != 0;

    // ───────── 파일 읽고 쓰기 ─────────

    /// <summary>파일 상태: 있음·없음·막힘(접근 거부)·읽기 실패.</summary>
    public enum FileState { Ok, Missing, Denied, Error }

    public sealed record FileCheck(string Path, FileState State, string? Error, DateTime? Modified);

    /// <summary>
    /// 파일을 실제로 열어 본다. File.Exists는 접근이 막혀도 false를 돌려 "없음"과 "막힘"을 구분하지 못한다.
    /// </summary>
    public static FileCheck Check(string path)
    {
        try
        {
            using var _ = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            return new(path, FileState.Ok, null, File.GetLastWriteTime(path));
        }
        catch (FileNotFoundException) { return new(path, FileState.Missing, null, null); }
        catch (DirectoryNotFoundException) { return new(path, FileState.Missing, null, null); }
        catch (UnauthorizedAccessException e) { return new(path, FileState.Denied, e.Message, null); }
        catch (IOException e) { return new(path, FileState.Error, e.Message, null); }
    }

    /// <summary>
    /// 다른 프로세스가 쓰는 중이어도 읽는다(FileShare.ReadWrite). 공유 위반이면 잠깐 기다렸다 다시. 없으면 null.
    /// 접근 거부는 <see cref="LocalFileDeniedException"/>(원인 안내가 붙은 예외)로 올린다.
    /// </summary>
    public static string? ReadText(string path)
    {
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                using var sr = new StreamReader(fs, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
                return sr.ReadToEnd();
            }
            catch (FileNotFoundException) { return null; }
            catch (DirectoryNotFoundException) { return null; }
            catch (UnauthorizedAccessException e) { throw new LocalFileDeniedException(path, e); }
            catch (IOException) when (attempt < 5) { Thread.Sleep(60 * (attempt + 1)); }
        }
    }

    /// <summary>임시 파일에 쓰고 바꿔 넣는다. 읽는 쪽이 쓰다 만 파일을 보지 않는다. 공유 위반이면 다시.</summary>
    public static void WriteTextAtomic(string path, string text)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
        var tmp = path + "." + Environment.ProcessId + ".tmp";
        File.WriteAllText(tmp, text, new UTF8Encoding(false));
        for (var attempt = 0; ; attempt++)
        {
            try { File.Move(tmp, path, overwrite: true); return; }
            catch (UnauthorizedAccessException e) { TryDelete(tmp); throw new LocalFileDeniedException(path, e); }
            catch (IOException) when (attempt < 5) { Thread.Sleep(60 * (attempt + 1)); }
            catch { TryDelete(tmp); throw; }
        }
    }

    static void TryDelete(string p) { try { File.Delete(p); } catch { } }

    /// <summary>"막힘"일 때 화면에 보일 안내(원인 + 해결).</summary>
    public static string DeniedHint(string path)
    {
        var why = SandboxReason();
        return $"{path}을(를) 읽을 수 없습니다(접근 거부). " +
               (why is null ? "파일 권한을 확인하세요." : $"샌드박스에서 실행된 것 같습니다({why}). 시작 메뉴의 SecondBrain 바로가기로 다시 켜세요.");
    }
}

/// <summary>설정 파일 접근이 막혔다. 메시지에 원인과 해결(다시 켜기)을 담는다.</summary>
public sealed class LocalFileDeniedException(string path, Exception inner)
    : UnauthorizedAccessException(LocalPaths.DeniedHint(path), inner)
{
    public string FilePath { get; } = path;
}
