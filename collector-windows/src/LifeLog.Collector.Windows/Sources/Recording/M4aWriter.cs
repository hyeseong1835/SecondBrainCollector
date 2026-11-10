using System.Diagnostics;
using System.Security.Cryptography;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 녹음 파일 하나. 16kHz 모노 16비트 PCM을 ffmpeg 표준 입력으로 흘려 AAC-LC 32kbps(.m4a)로 쓴다(폰 녹음과 같은 형식).
/// Windows Media Foundation의 AAC 인코더는 44.1/48kHz·96kbps 이상만 받아서 ffmpeg를 쓴다(분석 도구가 이미 받아 둔 것).
/// 쓰는 동안 `.part`, 끝나면 이름을 바꾼다. 지우지 않는다.
/// </summary>
public sealed class M4aWriter
{
    public const int Rate = 16_000;
    public const string Codec = "aac-lc 32kbps 16kHz mono";

    public string Path { get; }
    public long Samples { get; private set; }
    private readonly string _part;
    private readonly Process _ff;
    private readonly Stream _in;
    private readonly byte[] _buf = new byte[Rate / 10 * 2];

    public M4aWriter(string path)
    {
        Path = path;
        _part = path + ".part";
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
        var psi = new ProcessStartInfo(Ffmpeg.Exe)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardError = true, RedirectStandardOutput = true,
        };
        foreach (var a in new[] { "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
                     "-c:a", "aac", "-b:a", "32k", "-f", "mp4", _part })
            psi.ArgumentList.Add(a);
        _ff = Process.Start(psi) ?? throw new InvalidOperationException("ffmpeg를 띄우지 못했습니다");
        _ff.ErrorDataReceived += (_, _) => { };
        _ff.OutputDataReceived += (_, _) => { };
        _ff.BeginErrorReadLine();
        _ff.BeginOutputReadLine();
        _in = _ff.StandardInput.BaseStream;
    }

    /// <summary>16비트 PCM 샘플을 쓴다.</summary>
    public void Write(ReadOnlySpan<short> pcm)
    {
        while (pcm.Length > 0)
        {
            var n = Math.Min(pcm.Length, _buf.Length / 2);
            for (int i = 0; i < n; i++) { var v = pcm[i]; _buf[i * 2] = (byte)v; _buf[i * 2 + 1] = (byte)(v >> 8); }
            _in.Write(_buf, 0, n * 2);
            Samples += n;
            pcm = pcm[n..];
        }
    }

    public void WriteSilence(long samples)
    {
        Span<short> zero = stackalloc short[800];
        while (samples > 0) { var n = (int)Math.Min(samples, zero.Length); Write(zero[..n]); samples -= n; }
    }

    public long DurationMs => Samples * 1000 / Rate;

    /// <summary>인코딩을 끝내고 `.part`를 본 이름으로 바꾼다. (bytes, sha256)을 돌려준다.</summary>
    public (long bytes, string sha256) Finish()
    {
        try { _in.Flush(); _in.Close(); } catch (IOException) { }
        if (!_ff.WaitForExit(60_000)) { try { _ff.Kill(); } catch { } throw new IOException("ffmpeg가 끝나지 않았습니다: " + _part); }
        if (_ff.ExitCode != 0) throw new IOException($"ffmpeg 실패({_ff.ExitCode}): {_part}");
        File.Move(_part, Path, overwrite: false);
        using var fs = File.OpenRead(Path);
        var hash = Convert.ToHexStringLower(SHA256.HashData(fs));
        return (fs.Length, hash);
    }

    /// <summary>실패한 경우: 프로세스만 정리한다. `.part`는 남긴다(지우지 않는다).</summary>
    public void Abort()
    {
        try { _in.Close(); } catch { }
        try { if (!_ff.WaitForExit(5000)) _ff.Kill(); } catch { }
    }

    public static double Dbfs(ReadOnlySpan<short> pcm)
    {
        double sum = 0;
        foreach (var v in pcm) sum += (double)v * v;
        var rms = Math.Sqrt(sum / Math.Max(1, pcm.Length)) / 32768.0;
        return rms <= 1e-9 ? -120.0 : 20 * Math.Log10(rms);
    }

    public static short ToShort(float f) => (short)Math.Clamp((int)MathF.Round(f * 32767f), short.MinValue, short.MaxValue);
}

/// <summary>ffmpeg 위치: 환경 변수 SECONDBRAIN_FFMPEG → 분석 도구 폴더(%LOCALAPPDATA%\SecondBrain\analysis\ffmpeg\**\ffmpeg.exe) → PATH.</summary>
public static class Ffmpeg
{
    private static string? _exe;
    public static string Exe => _exe ??= Find();

    private static string Find()
    {
        var env = Environment.GetEnvironmentVariable("SECONDBRAIN_FFMPEG");
        if (!string.IsNullOrEmpty(env) && File.Exists(env)) return env;
        var dir = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SecondBrain", "analysis", "ffmpeg");
        if (Directory.Exists(dir))
        {
            var f = Directory.EnumerateFiles(dir, "ffmpeg.exe", SearchOption.AllDirectories).FirstOrDefault();
            if (f is not null) return f;
        }
        foreach (var p in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries))
        {
            var f = System.IO.Path.Combine(p.Trim(), "ffmpeg.exe");
            if (File.Exists(f)) return f;
        }
        throw new FileNotFoundException("ffmpeg.exe를 찾지 못했습니다(SECONDBRAIN_FFMPEG, %LOCALAPPDATA%\\SecondBrain\\analysis\\ffmpeg, PATH)");
    }
}

/// <summary>녹음 저장 위치: %USERPROFILE%\SecondBrainRecordings\&lt;원천&gt;\yyyy-MM-dd\yyyyMMdd-HHmmss-&lt;꼬리&gt;.m4a</summary>
public static class RecordingPaths
{
    public static string Root => Environment.GetEnvironmentVariable("SECONDBRAIN_RECORDINGS") is { Length: > 0 } r ? r
        : System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "SecondBrainRecordings");

    /// <summary>(절대 경로, 원천 폴더 기준 상대 경로 "yyyy-MM-dd/이름.m4a")</summary>
    public static (string abs, string rel) New(string source, DateTimeOffset start, string? tag = null)
    {
        var local = start.ToLocalTime();
        var name = $"{local:yyyyMMdd-HHmmss}-{(tag is null ? "" : tag + "-")}{Guid.NewGuid().ToString("N")[..8]}.m4a";
        var rel = $"{local:yyyy-MM-dd}/{name}";
        return (System.IO.Path.Combine(Root, source, local.ToString("yyyy-MM-dd"), name), rel);
    }
}
