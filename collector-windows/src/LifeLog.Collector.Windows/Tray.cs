using System.Diagnostics;
using System.Runtime.InteropServices;
using LifeLog.Collector.Windows.Sources;
using LifeLog.Collector.Windows.Sources.Recording;
using LifeLog.Core;

namespace LifeLog.Collector.Windows;

/// <summary>트레이 아이콘. 별도 STA 스레드에서 WinForms 메시지 루프를 돌린다. 메뉴: 상태, 로그 폴더 열기, 종료.</summary>
public sealed class Tray : IDisposable
{
    [DllImport("kernel32.dll")] private static extern bool FreeConsole();

    private readonly Thread _thread;
    private NotifyIcon? _icon;
    private readonly Func<string> _status;
    private readonly Action _quit;
    private readonly Action? _retry;
    private ToolStripMenuItem? _retryItem;
    private Control? _ui;   // UI 스레드로 넘기는 용도
    private (string? Title, string? Text) _problem;

    /// <param name="retry">설정을 다시 읽게 하는 동작(설정 없음일 때 메뉴 '다시 시도').</param>
    public Tray(Func<string> status, Action quit, Action? retry = null)
    {
        _status = status; _quit = quit; _retry = retry;
        _thread = new Thread(Loop) { Name = "lifelog-tray", IsBackground = true };
        _thread.SetApartmentState(ApartmentState.STA);
    }

    public void Start()
    {
        // 콘솔에서 떨어진다. 창을 숨기기만 하면 PowerShell 창에서 띄운 경우 그 창을 닫을 때 수집기도 같이 죽는다(CTRL_CLOSE). 로그는 파일에 남는다
        FreeConsole();
        // 떨어지기 전에 콘솔에 한 번이라도 썼으면 Console.Out/Error가 닫힌 콘솔 핸들을 쥐고 있어 이후 쓰기마다 IOException이 난다.
        // (2026-11-14: 시작 줄을 FreeConsole 전에 쓰게 바꾼 뒤 바로가기로 켜면 로그 한 줄만 남기고 죽었다) 그래서 버린다.
        Console.SetOut(TextWriter.Null);
        Console.SetError(TextWriter.Null);
        _thread.Start();
    }

    private void Loop()
    {
        var menu = new ContextMenuStrip();
        var status = new ToolStripMenuItem("SecondBrain Collector") { Enabled = false };
        menu.Items.Add(status);
        menu.Items.Add(new ToolStripSeparator());
        // 디스코드 봇 녹음 모드(플러그인 win-discord-bot 설정 enabled·auto_only, DB에도 쓴다): 꺼짐 · 켜짐(어디든 따라감) · 자동(등록한 서버·채널에서만)
        var bot = new ToolStripMenuItem("디스코드 봇 녹음");
        var modes = new[] { (DiscordSettings.ModeOff, "꺼짐"), (DiscordSettings.ModeOn, "켜짐 (어디든 따라감)"), (DiscordSettings.ModeAuto, "자동 (등록한 서버·채널만)") }
            .Select(x => { var it = new ToolStripMenuItem(x.Item2) { Tag = x.Item1 }; it.Click += (_, _) => DiscordSettings.SetBotMode(x.Item1); return it; }).ToArray();
        bot.DropDownItems.AddRange(modes);
        var botStatus = new ToolStripMenuItem("") { Enabled = false };
        menu.Items.Add(bot);
        menu.Items.Add(botStatus);
        menu.Items.Add(new ToolStripSeparator());
        _retryItem = new ToolStripMenuItem("다시 시도 (설정 다시 읽기)", null, (_, _) => _retry?.Invoke()) { Visible = false };
        menu.Items.Add(_retryItem);
        menu.Items.Add("설정·로그 폴더 열기", null, (_, _) => { Directory.CreateDirectory(Config.Dir); Process.Start(new ProcessStartInfo("explorer.exe", Config.Dir) { UseShellExecute = true }); });
        menu.Items.Add("녹음 폴더 열기", null, (_, _) => { Directory.CreateDirectory(RecordingPaths.Root); Process.Start(new ProcessStartInfo("explorer.exe", RecordingPaths.Root) { UseShellExecute = true }); });
        menu.Items.Add("종료", null, (_, _) => _quit());
        menu.Opening += (_, _) =>
        {
            status.Text = _status();
            var mode = DiscordSettings.Load().BotMode;
            foreach (var it in modes) it.Checked = (string)it.Tag! == mode;
            bot.Checked = mode != DiscordSettings.ModeOff;
            botStatus.Text = "  봇: " + DiscordBotSource.StatusText;
        };
        _icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "SecondBrain Collector", Visible = true, ContextMenuStrip = menu };
        _ui = new Control();
        _ui.CreateControl();
        _ = _ui.Handle;
        ApplyProblem();
        Application.Run();
        _icon.Visible = false;
        _icon.Dispose();
    }

    /// <summary>
    /// 문제(예: 설정 없음)를 트레이에 보인다: 경고 아이콘·툴팁·풍선 알림 한 번, 메뉴에 '다시 시도'. title이 null이면 원래대로.
    /// 트레이 스레드가 아직 아이콘을 만들기 전이어도 된다(만든 뒤 적용).
    /// </summary>
    public void ShowProblem(string? title, string? text)
    {
        _problem = (title, text);
        var ui = _ui;
        if (ui is { IsHandleCreated: true }) { try { ui.BeginInvoke(ApplyProblem); } catch (InvalidOperationException) { } }
    }

    private void ApplyProblem()
    {
        if (_icon is null) return;
        var (title, text) = _problem;
        var bad = title is not null;
        _icon.Icon = bad ? SystemIcons.Warning : SystemIcons.Application;
        var tip = bad ? title! : "SecondBrain Collector";
        _icon.Text = tip.Length > 63 ? tip[..63] : tip;   // NotifyIcon.Text 한도
        if (_retryItem is not null) _retryItem.Visible = bad && _retry is not null;
        if (bad) _icon.ShowBalloonTip(15000, title, text ?? "", ToolTipIcon.Warning);
    }

    public void Dispose()
    {
        if (_icon is null) return;
        try { Application.Exit(); } catch { }
        _thread.Join(TimeSpan.FromSeconds(3));
    }
}
