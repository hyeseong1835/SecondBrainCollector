using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using Windows.Networking.Connectivity;

namespace LifeLog.Collector.Windows.Sources;

/// <summary>
/// 노트북이 붙은 네트워크(2026-11-16, 네트워크별 거리 구간용). 기본 경로(IPv4 게이트웨이가 있는 어댑터) 중 대표 하나를
/// 30초마다·주소가 바뀔 때 읽어, 바뀌면 그리고 적어도 10분마다 낸다.
///   { time, kind: wifi | ethernet | none, net_key, name, ssid, gateway_mac, adapter }
/// net_key: 와이파이 = "wifi:" + SSID, 유선 = "lan:" + 게이트웨이 MAC(못 읽으면 "lan-ip:" + 게이트웨이 IP). 연결 없음 = kind none, net_key null.
/// SSID는 WinRT 연결 프로필(GetConnectedSsid → 안 되면 프로필 이름. Windows 11 24H2부터 위치 권한이 없으면 SSID가 비어 프로필 이름을 쓴다).
/// 대표 어댑터: 인터넷 연결 프로필의 어댑터 → 없으면 게이트웨이 있는 어댑터 중 유선 → 와이파이 순.
/// </summary>
public sealed class NetworkSource : ISource
{
    public string Id => "network";

    public sealed record Net(string Kind, string? Key, string? Name, string? Ssid, string? GatewayMac, string? Adapter);

    private System.Threading.Timer? _timer;
    private Net? _last;
    private DateTime _lastEmit;
    private Action<Dictionary<string, object?>>? _emit;
    private readonly object _lock = new();

    public void Start(Action<Dictionary<string, object?>> emit)
    {
        _emit = emit;
        NetworkChange.NetworkAddressChanged += OnChanged;
        _timer = new System.Threading.Timer(_ => Tick(), null, TimeSpan.Zero, TimeSpan.FromSeconds(30));
    }

    private void OnChanged(object? s, EventArgs e) => _timer?.Change(TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(30));   // 주소가 자리 잡을 때까지 잠깐 기다림

    private void Tick()
    {
        lock (_lock)
        {
            Net cur;
            try { cur = Read(); } catch (Exception) { return; }
            var now = DateTime.UtcNow;
            if (_last == cur && now - _lastEmit < TimeSpan.FromMinutes(10)) return;
            _last = cur; _lastEmit = now;
            _emit?.Invoke(new Dictionary<string, object?>
            {
                ["time"] = now, ["kind"] = cur.Kind, ["net_key"] = cur.Key, ["name"] = cur.Name,
                ["ssid"] = cur.Ssid, ["gateway_mac"] = cur.GatewayMac, ["adapter"] = cur.Adapter,
            });
        }
    }

    /// <summary>지금 대표 네트워크 하나.</summary>
    public static Net Read()
    {
        var withGw = NetworkInterface.GetAllNetworkInterfaces()
            .Where(n => n.OperationalStatus == OperationalStatus.Up && n.NetworkInterfaceType is not (NetworkInterfaceType.Loopback or NetworkInterfaceType.Tunnel))
            .Select(n => (n, gw: n.GetIPProperties().GatewayAddresses.Select(g => g.Address)
                .FirstOrDefault(a => a.AddressFamily == AddressFamily.InterNetwork && !a.Equals(IPAddress.Any))))
            .Where(x => x.gw is not null).ToList();
        if (withGw.Count == 0) return new Net("none", null, null, null, null, null);

        var profiles = Profiles();
        string? internetId = null;
        try { internetId = NetworkInformation.GetInternetConnectionProfile()?.NetworkAdapter?.NetworkAdapterId.ToString("B"); } catch (Exception) { }
        var pick = withGw.FirstOrDefault(x => internetId != null && string.Equals(x.n.Id, internetId, StringComparison.OrdinalIgnoreCase));
        if (pick.n is null) pick = withGw.OrderBy(x => x.n.NetworkInterfaceType == NetworkInterfaceType.Wireless80211 ? 1 : 0).First();

        var (n, gw) = pick;
        var wifi = n.NetworkInterfaceType == NetworkInterfaceType.Wireless80211;
        profiles.TryGetValue(n.Id.ToLowerInvariant(), out var prof);
        var mac = GatewayMac(gw!);
        var ssid = wifi ? prof.Ssid : null;
        var name = wifi ? ssid : prof.Name ?? n.Name;
        string? key = wifi ? (ssid is { Length: > 0 } ? "wifi:" + ssid : null)
                           : mac is not null ? "lan:" + mac : "lan-ip:" + gw;
        return new Net(wifi ? "wifi" : "ethernet", key, name, ssid, mac, n.Description);
    }

    /// <summary>어댑터 id("{guid}" 소문자) → (SSID, 프로필 이름).</summary>
    static Dictionary<string, (string? Ssid, string? Name)> Profiles()
    {
        var o = new Dictionary<string, (string?, string?)>();
        try
        {
            foreach (var p in NetworkInformation.GetConnectionProfiles())
            {
                if (p.NetworkAdapter is null || p.GetNetworkConnectivityLevel() == NetworkConnectivityLevel.None) continue;
                string? ssid = null;
                if (p.IsWlanConnectionProfile)
                {
                    try { ssid = p.WlanConnectionProfileDetails?.GetConnectedSsid(); } catch (Exception) { }
                    if (string.IsNullOrEmpty(ssid)) ssid = p.ProfileName;
                }
                o[p.NetworkAdapter.NetworkAdapterId.ToString("B").ToLowerInvariant()] = (ssid, p.ProfileName);
            }
        }
        catch (Exception) { }
        return o;
    }

    [DllImport("iphlpapi.dll")] static extern int SendARP(uint dest, uint src, byte[] mac, ref int len);

    /// <summary>게이트웨이 MAC("aa:bb:cc:dd:ee:ff"). ARP가 안 되면 null.</summary>
    public static string? GatewayMac(IPAddress gw)
    {
        try
        {
            var mac = new byte[6]; var len = 6;
            if (SendARP(BitConverter.ToUInt32(gw.GetAddressBytes(), 0), 0, mac, ref len) != 0 || len < 6) return null;
            if (mac.All(b => b == 0)) return null;
            return string.Join(":", mac.Take(6).Select(b => b.ToString("x2")));
        }
        catch (Exception) { return null; }
    }

    public void Dispose()
    {
        NetworkChange.NetworkAddressChanged -= OnChanged;
        _timer?.Dispose();
    }
}
