using System.Diagnostics;
using System.Runtime.InteropServices;
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
        var status = new ToolStripMenuItem("세컨뇌 수집기") { Enabled = false };
        menu.Items.Add(status);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("로그 폴더 열기", null, (_, _) => Process.Start(new ProcessStartInfo("explorer.exe", Config.Dir) { UseShellExecute = true }));
        menu.Items.Add("종료", null, (_, _) => _quit());
        menu.Opening += (_, _) => status.Text = _status();
        _icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "세컨뇌 수집기", Visible = true, ContextMenuStrip = menu };
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
