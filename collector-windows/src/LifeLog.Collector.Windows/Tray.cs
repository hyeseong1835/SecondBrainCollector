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

    public Tray(Func<string> status, Action quit)
    {
        _status = status; _quit = quit;
        _thread = new Thread(Loop) { Name = "lifelog-tray", IsBackground = true };
        _thread.SetApartmentState(ApartmentState.STA);
    }

    public void Start()
    {
        // 콘솔에서 떨어진다. 창을 숨기기만 하면 PowerShell 창에서 띄운 경우 그 창을 닫을 때 수집기도 같이 죽는다(CTRL_CLOSE). 로그는 파일에 남는다
        FreeConsole();
        _thread.Start();
    }

    private void Loop()
    {
        var menu = new ContextMenuStrip();
        var status = new ToolStripMenuItem("SecondBrain Collector") { Enabled = false };
        menu.Items.Add(status);
        menu.Items.Add(new ToolStripSeparator());
        // 디스코드 봇 녹음 켜고 끄기(discord.json의 bot_enabled). 켜져 있으면 내가 음성 채널에 들어갈 때 봇이 따라 들어가 사람별로 저장한다
        var bot = new ToolStripMenuItem("디스코드 봇 녹음") { CheckOnClick = false };
        bot.Click += (_, _) => DiscordSettings.Update(s => s.BotEnabled = !s.BotEnabled);
        var botStatus = new ToolStripMenuItem("") { Enabled = false };
        menu.Items.Add(bot);
        menu.Items.Add(botStatus);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("로그 폴더 열기", null, (_, _) => Process.Start(new ProcessStartInfo("explorer.exe", Config.Dir) { UseShellExecute = true }));
        menu.Items.Add("녹음 폴더 열기", null, (_, _) => { Directory.CreateDirectory(RecordingPaths.Root); Process.Start(new ProcessStartInfo("explorer.exe", RecordingPaths.Root) { UseShellExecute = true }); });
        menu.Items.Add("종료", null, (_, _) => _quit());
        menu.Opening += (_, _) =>
        {
            status.Text = _status();
            bot.Checked = DiscordSettings.Load().BotEnabled;
            botStatus.Text = "  봇: " + DiscordBotSource.StatusText;
        };
        _icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "SecondBrain Collector", Visible = true, ContextMenuStrip = menu };
        Application.Run();
        _icon.Visible = false;
        _icon.Dispose();
    }

    public void Dispose()
    {
        if (_icon is null) return;
        try { Application.Exit(); } catch { }
        _thread.Join(TimeSpan.FromSeconds(3));
    }
}
