using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;
using LifeLog.Core;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 랜선 위치 지문(2026-11-19). 노트북이 이더넷에 붙어 있을 때 공유기 너머를 잰다:
/// 사용자는 공유기를 들고 다녀 게이트웨이(lan:MAC)가 어디서나 같으므로, 그 다음 홉들과 주변 와이파이 AP로 자리를 가린다.
///   { time, net_key, gateway_mac, target, hops_json: [{n, ip, rtt_ms}], aps_json: [{bssid, ssid, rssi, freq_mhz, channel}], wifi: ok|off|denied|none|error }
/// 이더넷 키가 새로 생기거나 바뀔 때 한 번(주소가 자리 잡게 20초 뒤), 같은 이더넷이 이어지면 30분마다.
/// 경로: 우리 API 서버 주소로 TTL 1..8 ICMP(홉마다 1초, 한 번 다시). 서버 주소가 없으면 경로는 비운다.
/// AP: wlanapi로 훑기(WlanScan) 뒤 4초 기다려 BSS 목록. 무선 어댑터가 없거나 꺼져 있으면 비운다(Windows 11 24H2는 위치 권한이 없으면 denied).
/// 개인정보: 행은 이 PC 큐와 사용자 서버에만 간다.
/// </summary>
public sealed class LanProbeSource(string? apiUrl, Action<string> log) : ISource
{
    public string Id => "lan_probe";

    public static readonly TimeSpan Interval = TimeSpan.FromMinutes(30);
    const int MaxHops = 8, HopTimeoutMs = 1000;

    private System.Threading.Timer? _timer;
    private Action<Dictionary<string, object?>>? _emit;
    private readonly LanProbe.Schedule _schedule = new(Interval);
    private int _running;

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        NetworkChange.NetworkAddressChanged += OnChanged;
        _timer = new System.Threading.Timer(_ => Tick(), null, TimeSpan.FromSeconds(20), TimeSpan.FromMinutes(1));
    }

    private void OnChanged(object? s, EventArgs e) => _timer?.Change(TimeSpan.FromSeconds(20), TimeSpan.FromMinutes(1));

    private void Tick()
    {
        if (Interlocked.Exchange(ref _running, 1) == 1) return;   // 앞 측정이 아직 도는 중
        try
        {
            NetworkSource.Net net;
            try { net = NetworkSource.Read(); } catch (Exception) { return; }
            var key = net.Kind == "ethernet" ? net.Key : null;
            if (!_schedule.Due(key, DateTime.UtcNow)) return;
            var ev = Probe(net);
            _emit?.Invoke(ev);
        }
        catch (Exception e) { log($"[lan_probe] 측정 실패: {e.Message}"); }
        finally { Volatile.Write(ref _running, 0); }
    }

    /// <summary>지금 한 번 잰다(이더넷이 아니어도). 시험 명령 lan-probe-test용.</summary>
    public Dictionary<string, object?> ProbeNow() => Probe(NetworkSource.Read());

    private Dictionary<string, object?> Probe(NetworkSource.Net net)
    {
        var time = DateTime.UtcNow;
        string? target = null;
        List<LanProbe.Hop> hops = [];
        try
        {
            if (Uri.TryCreate(apiUrl ?? "", UriKind.Absolute, out var u))
            {
                target = u.Host;
                hops = Trace(target);
            }
        }
        catch (Exception e) { log($"[lan_probe] 경로 실패: {e.Message}"); }
        var (wifi, aps) = Wlan.Scan(log);
        return new Dictionary<string, object?>
        {
            ["time"] = time, ["net_key"] = net.Key, ["gateway_mac"] = net.GatewayMac, ["target"] = target,
            ["hops_json"] = JsonSerializer.Serialize(hops.Select(h => new { n = h.N, ip = h.Ip, rtt_ms = h.RttMs })),
            ["aps_json"] = JsonSerializer.Serialize(aps.Select(a => new { bssid = a.Bssid, ssid = a.Ssid, rssi = a.Rssi, freq_mhz = a.FreqMhz, channel = a.Channel })),
            ["wifi"] = wifi,
        };
    }

    /// <summary>tracert처럼 TTL을 늘려 가며 ICMP. 목적지에 닿으면 멈춘다.</summary>
    static List<LanProbe.Hop> Trace(string host)
    {
        var addr = Dns.GetHostAddresses(host).FirstOrDefault(a => a.AddressFamily == AddressFamily.InterNetwork);
        if (addr is null) return [];
        using var ping = new Ping();
        var buf = new byte[32];
        var hops = new List<LanProbe.Hop>();
        for (var ttl = 1; ttl <= MaxHops; ttl++)
        {
            LanProbe.Hop hop = new(ttl, null, null);
            for (var attempt = 0; attempt < 2 && hop.Ip is null; attempt++)
            {
                var sw = System.Diagnostics.Stopwatch.StartNew();
                PingReply r;
                try { r = ping.Send(addr, HopTimeoutMs, buf, new PingOptions(ttl, true)); }
                catch (PingException) { continue; }
                if (r.Status is IPStatus.TtlExpired or IPStatus.Success && r.Address is not null && !r.Address.Equals(IPAddress.Any))
                    hop = new(ttl, r.Address.ToString(), (int)(r.Status == IPStatus.Success ? r.RoundtripTime : sw.ElapsedMilliseconds));
            }
            hops.Add(hop);
            if (hop.Ip == addr.ToString()) break;
        }
        return LanProbe.TrimHops(hops);
    }

    public void Dispose()
    {
        NetworkChange.NetworkAddressChanged -= OnChanged;
        _timer?.Dispose();
    }

    /// <summary>wlanapi.dll. 첫 무선 어댑터로 훑고 BSS 목록을 읽는다.</summary>
    static class Wlan
    {
        [DllImport("wlanapi.dll")] static extern uint WlanOpenHandle(uint ver, IntPtr reserved, out uint negotiated, out IntPtr handle);
        [DllImport("wlanapi.dll")] static extern uint WlanCloseHandle(IntPtr handle, IntPtr reserved);
        [DllImport("wlanapi.dll")] static extern uint WlanEnumInterfaces(IntPtr handle, IntPtr reserved, out IntPtr list);
        [DllImport("wlanapi.dll")] static extern uint WlanScan(IntPtr handle, ref Guid iface, IntPtr ssid, IntPtr ie, IntPtr reserved);
        [DllImport("wlanapi.dll")] static extern uint WlanGetNetworkBssList(IntPtr handle, ref Guid iface, IntPtr ssid, int bssType, bool secure, IntPtr reserved, out IntPtr list);
        [DllImport("wlanapi.dll")] static extern void WlanFreeMemory(IntPtr p);

        const uint ErrorAccessDenied = 5, ErrorNotReady = 21, ErrorPowerOff = 0x80342002;
        const int AnyBss = 3;   // dot11_BSS_type_any

        public static (string Status, List<LanProbe.Ap> Aps) Scan(Action<string> log)
        {
            IntPtr h = IntPtr.Zero, ifs = IntPtr.Zero, bss = IntPtr.Zero;
            try
            {
                if (WlanOpenHandle(2, IntPtr.Zero, out _, out h) != 0) return ("none", []);
                if (WlanEnumInterfaces(h, IntPtr.Zero, out ifs) != 0) return ("error", []);
                var n = Marshal.ReadInt32(ifs);
                if (n == 0) return ("none", []);
                // WLAN_INTERFACE_INFO_LIST: n(4) index(4), WLAN_INTERFACE_INFO[] = GUID 16 + 이름 512 + 상태 4
                var guid = Marshal.PtrToStructure<Guid>(ifs + 8);
                var sr = WlanScan(h, ref guid, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                if (sr == 0) Thread.Sleep(4000);   // 훑기 끝 알림을 기다리는 대신 넉넉히(보통 2~4초). 실패해도 캐시된 목록은 읽는다
                var r = WlanGetNetworkBssList(h, ref guid, IntPtr.Zero, AnyBss, false, IntPtr.Zero, out bss);
                if (r == ErrorAccessDenied) return ("denied", []);
                if (r is ErrorNotReady or ErrorPowerOff) return ("off", []);
                if (r != 0) { log($"[lan_probe] BSS 목록 실패 {r}"); return ("error", []); }
                var size = Marshal.ReadInt32(bss);
                var bytes = new byte[size];
                Marshal.Copy(bss, bytes, 0, size);
                var aps = LanProbe.ParseBssList(bytes);
                return (aps.Count == 0 && sr != 0 ? "off" : "ok", aps);
            }
            catch (DllNotFoundException) { return ("none", []); }
            finally
            {
                if (bss != IntPtr.Zero) WlanFreeMemory(bss);
                if (ifs != IntPtr.Zero) WlanFreeMemory(ifs);
                if (h != IntPtr.Zero) WlanCloseHandle(h, IntPtr.Zero);
            }
        }
    }
}
