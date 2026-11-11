using System.Diagnostics;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;

namespace LifeLog.Collector.Windows.Sources.Recording;

/// <summary>
/// 디스코드 음성 통화 중인지(2026-11-11). discord_output·laptop_mic은 <see cref="Recording"/>이 true일 때만 장치를 열어 녹음한다.
/// 노트북을 들고 다녀 강의실 소리가 잡히는 것을 막기 위함이다.
///
/// - 통화 중 = Windows 오디오 세션 중 Discord 프로세스(Discord·DiscordPTB·DiscordCanary)의 세션이 활성(Active)인 것이 있을 때.
///   입력 장치(마이크)의 활성 세션은 바로 통화로 본다(음성 채널에 있으면 Discord가 마이크를 연다).
///   출력 장치의 활성 세션은 2번 연속(약 4초) 보여야 통화로 본다(짧은 알림음 거르기). 사용자는 Discord 출력을 가상 케이블로 보낸다.
/// - 2초마다 확인하고, 2번 연속 안 보이면 통화 끝.
/// - 디스코드 봇이 음성 채널에 들어가 녹음 중이면(<see cref="DiscordBotSource.InVoice"/>) 둘 다 멈춘다(봇 녹음이 사람별로 더 낫다).
/// </summary>
public static class DiscordCall
{
    static readonly string[] Names = ["Discord", "DiscordPTB", "DiscordCanary", "DiscordDevelopment"];
    static readonly object Gate = new();
    static Thread? _thread;
    static volatile bool _inCall;
    static int _renderHits, _misses;

    /// <summary>디스코드 통화 중(봇 녹음과 무관).</summary>
    public static bool InCall { get { EnsureStarted(); return _inCall; } }

    /// <summary>일반 녹음(discord_output·laptop_mic)을 할 때인가: 통화 중이고 봇이 녹음 중이 아님.</summary>
    public static bool Recording => InCall && !DiscordBotSource.InVoice;

    /// <summary>지금 상태를 사람이 읽을 말로(로그용).</summary>
    public static string Why => !InCall ? "디스코드 통화 아님" : DiscordBotSource.InVoice ? "봇이 녹음 중" : "디스코드 통화 중";

    static void EnsureStarted()
    {
        lock (Gate)
        {
            if (_thread is not null) return;
            _thread = new Thread(Loop) { IsBackground = true, Name = "discord-call" };
            _thread.Start();
        }
    }

    static void Loop()
    {
        using var en = new MMDeviceEnumerator();
        while (true)
        {
            try { Step(Probe(en)); }
            catch { /* 장치 열거 실패는 다음 번에 */ }
            Thread.Sleep(2000);
        }
    }

    static void Step((bool capture, bool render) s)
    {
        _renderHits = s.render ? _renderHits + 1 : 0;
        var seen = s.capture || _renderHits >= 2;
        if (seen) { _misses = 0; _inCall = true; }
        else if (++_misses >= 2) _inCall = false;
    }

    /// <summary>(입력 장치에 Discord 활성 세션, 출력 장치에 Discord 활성 세션)</summary>
    public static (bool capture, bool render) Probe(MMDeviceEnumerator en, Action<string>? trace = null)
        => (Has(en, DataFlow.Capture, trace), Has(en, DataFlow.Render, trace));

    static bool Has(MMDeviceEnumerator en, DataFlow flow, Action<string>? trace)
    {
        var hit = false;
        foreach (var d in en.EnumerateAudioEndPoints(flow, DeviceState.Active))
            using (d)
            {
                try
                {
                    var sessions = d.AudioSessionManager.Sessions;
                    for (int i = 0; i < sessions.Count; i++)
                    {
                        var s = sessions[i];
                        if (s.IsSystemSoundsSession) continue;
                        var name = ProcessName((int)s.GetProcessID);
                        if (name is null || !Names.Contains(name, StringComparer.OrdinalIgnoreCase)) continue;
                        trace?.Invoke($"{(flow == DataFlow.Capture ? "입력" : "출력")} {d.FriendlyName}: {name}({s.GetProcessID}) {s.State}");
                        if (s.State == AudioSessionState.AudioSessionStateActive) hit = true;
                    }
                }
                catch { }
            }
        return hit;
    }

    static string? ProcessName(int pid)
    {
        if (pid == 0) return null;
        try { using var p = Process.GetProcessById(pid); return p.ProcessName; }
        catch { return null; }
    }

    /// <summary>통화 조건이 맞을 때까지 기다린다(1초마다). 취소되면 false.</summary>
    public static bool WaitForRecording(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            if (Recording) return true;
            ct.WaitHandle.WaitOne(1000);
        }
        return false;
    }
}
