namespace LifeLog.Collector.Windows;

/// <summary>
/// 로그온 자동 실행(2026-11-14). 시작프로그램 폴더(shell:startup)에 "SecondBrain 수집기.lnk"(대상 수집기 exe, 인수 run --tray, 최소화)를 둔다.
/// 예전에는 HKCU Run 값(SecondBrainCollector)을 썼는데, Claude 세션에서 등록하면 레지스트리가 가상화돼 실제 Windows에는 들어가지 않았고
/// 11-12부터 로그온 때 한 번도 실행되지 않았다. 파일은 실제 환경에서도 보이므로 데스크톱 앱(install-startup.ps1)과 같은 방식으로 바꿨다.
/// 옛 Run 값(SecondBrainCollector·LifeLogCollector)은 on/off 때 지우려 하지만, 가상화된 세션에서는 실제로 안 지워질 수 있어 status·on이 안내한다.
/// </summary>
public static class Autostart
{
    public const string ShortcutName = "SecondBrain 수집기.lnk";
    static readonly string[] OldRunValues = ["SecondBrainCollector", "LifeLogCollector"];
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    const int ShowMinimized = 7;   // WshShortcut.WindowStyle: 최소화

    public static string ShortcutPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), ShortcutName);

    public static int Run(string? arg)
    {
        switch (arg)
        {
            case "on": On(); return 0;
            case "off": Off(); return 0;
            case "status": case null: return Status();
            default: Console.WriteLine("autostart on|off|status"); return 1;
        }
    }

    static void On()
    {
        var exe = Environment.ProcessPath ?? throw new InvalidOperationException("실행 파일 경로를 알 수 없습니다");
        Directory.CreateDirectory(Path.GetDirectoryName(ShortcutPath)!);
        var shell = NewShell();
        try
        {
            dynamic s = shell.CreateShortcut(ShortcutPath);
            s.TargetPath = exe;
            s.Arguments = "run --tray";
            s.WorkingDirectory = Path.GetDirectoryName(exe);
            s.WindowStyle = ShowMinimized;
            s.IconLocation = exe + ",0";
            s.Description = "SecondBrain 수집기 (로그온 시 트레이에 실행)";
            s.Save();
        }
        finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell); }
        Console.WriteLine($"로그온 시 자동 실행 등록(시작프로그램 바로가기): {ShortcutPath}");
        Console.WriteLine($"  대상: \"{exe}\" run --tray (최소화)");
        RemoveOldRunValues();
    }

    static void Off()
    {
        if (File.Exists(ShortcutPath)) { File.Delete(ShortcutPath); Console.WriteLine($"자동 실행 해제: {ShortcutPath} 지움"); }
        else Console.WriteLine("자동 실행 바로가기가 없습니다");
        RemoveOldRunValues();
    }

    static int Status()
    {
        if (ReadShortcut() is { } t)
        {
            var ok = File.Exists(t.Target);
            Console.WriteLine($"자동 실행 켜짐(시작프로그램 바로가기): {ShortcutPath}");
            Console.WriteLine($"  대상: \"{t.Target}\" {t.Args}{(ok ? "" : "  ← 파일이 없습니다. autostart on을 다시 하세요")}");
        }
        else Console.WriteLine("자동 실행 꺼짐(시작프로그램 바로가기 없음)");
        var left = OldRunValuesPresent();
        if (left.Count > 0) Console.WriteLine($"옛 레지스트리 자동 실행 값이 남아 있습니다: {string.Join(", ", left)}. " + OldRunHint);
        return 0;
    }

    const string OldRunHint = "작업 관리자 › 시작 앱에서 해당 항목을 '사용 안 함'으로 바꾸거나, Claude 밖의 일반 터미널에서 autostart on을 다시 실행하세요"
        + "(Claude 세션에서는 레지스트리가 가상화돼 실제 값이 안 보이거나 안 지워질 수 있습니다).";

    static void RemoveOldRunValues()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey, true);
            foreach (var v in OldRunValues)
                if (key?.GetValue(v) is not null) { key.DeleteValue(v, false); Console.WriteLine($"옛 레지스트리 자동 실행 값 지움: HKCU\\...\\Run\\{v}"); }
        }
        catch (Exception e) when (e is UnauthorizedAccessException or System.Security.SecurityException or IOException)
        {
            Console.WriteLine($"옛 레지스트리 자동 실행 값을 지우지 못했습니다: {e.Message}");
        }
        Console.WriteLine($"참고: 옛 자동 실행 항목({string.Join("·", OldRunValues)})이 작업 관리자 › 시작 앱에 남아 있으면 '사용 안 함'으로 바꾸세요"
            + "(Claude 세션에서 실행했으면 레지스트리가 가상화돼 실제로는 안 지워졌을 수 있습니다).");
    }

    static List<string> OldRunValuesPresent()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey);
            return OldRunValues.Where(v => key?.GetValue(v) is not null).ToList();
        }
        catch (Exception e) when (e is UnauthorizedAccessException or System.Security.SecurityException or IOException) { return []; }
    }

    /// <summary>바로가기의 대상·인수. 없으면 null.</summary>
    public static (string Target, string Args)? ReadShortcut()
    {
        if (!File.Exists(ShortcutPath)) return null;
        var shell = NewShell();
        try
        {
            dynamic s = shell.CreateShortcut(ShortcutPath);
            return ((string)s.TargetPath, (string)s.Arguments);
        }
        finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell); }
    }

    static dynamic NewShell() =>
        Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell") ?? throw new InvalidOperationException("WScript.Shell을 쓸 수 없습니다"))!;
}
