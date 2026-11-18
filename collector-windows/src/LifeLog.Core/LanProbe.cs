using System.Buffers.Binary;
using System.Text;

namespace LifeLog.Core;

/// <summary>
/// 랜선 위치 지문(2026-11-19)의 순수 부분: WLAN BSS 목록 해석, 주파수 → 채널, 경로 홉 정리, 언제 다시 잴지.
/// 네이티브 호출(Ping·wlanapi)은 수집기 LanProbeSource가 하고, 여기는 시험할 수 있는 계산만 둔다.
/// 사용자는 공유기를 들고 다녀 게이트웨이(lan:MAC)가 어디서나 같으므로, 공유기 너머(경로 홉)와 주변 AP로 자리를 가린다.
/// </summary>
public static class LanProbe
{
    /// <summary>경로의 한 홉. Ip null = 응답 없음(*).</summary>
    public sealed record Hop(int N, string? Ip, int? RttMs);

    /// <summary>주변 AP 하나. Rssi = dBm, FreqMhz = 중심 주파수.</summary>
    public sealed record Ap(string Bssid, string Ssid, int Rssi, int FreqMhz, int? Channel);

    /// <summary>WLAN_BSS_ENTRY 크기(x64·x86 같음): DOT11_SSID 36 + phy 4 + bssid 6(+2) + type 4 + phy type 4 + rssi 4 + quality 4 + regdomain 1(+1) + beacon 2(+4) + 시각 8·8 + cap 2(+2) + freq 4 + rate set 256 + ie 4·4.</summary>
    public const int BssEntrySize = 360;

    /// <summary>
    /// WlanGetNetworkBssList가 준 WLAN_BSS_LIST 메모리(그대로 복사한 바이트) → AP 목록. 신호가 센 것부터, max개까지.
    /// 머리: dwTotalSize(4) dwNumberOfItems(4), 그 뒤 WLAN_BSS_ENTRY가 360바이트씩.
    /// </summary>
    public static List<Ap> ParseBssList(ReadOnlySpan<byte> buf, int max = 40)
    {
        var list = new List<Ap>();
        if (buf.Length < 8) return list;
        var n = (int)BinaryPrimitives.ReadUInt32LittleEndian(buf[4..]);
        for (var i = 0; i < n; i++)
        {
            var off = 8 + i * BssEntrySize;
            if (off + BssEntrySize > buf.Length) break;
            var e = buf.Slice(off, BssEntrySize);
            var ssidLen = Math.Min(32, (int)BinaryPrimitives.ReadUInt32LittleEndian(e));
            var ssid = DecodeSsid(e.Slice(4, ssidLen));
            var bssid = string.Join(":", e.Slice(40, 6).ToArray().Select(b => b.ToString("x2")));
            var rssi = BinaryPrimitives.ReadInt32LittleEndian(e[56..]);
            var freqKhz = (int)BinaryPrimitives.ReadUInt32LittleEndian(e[92..]);
            var mhz = freqKhz / 1000;
            if (bssid == "00:00:00:00:00:00") continue;
            list.Add(new Ap(bssid, ssid, rssi, mhz, Channel(mhz)));
        }
        // 같은 BSSID가 두 번(드물게 phy마다) 나오면 센 쪽만
        return list.GroupBy(a => a.Bssid).Select(g => g.MaxBy(a => a.Rssi)!)
            .OrderByDescending(a => a.Rssi).ThenBy(a => a.Bssid, StringComparer.Ordinal).Take(max).ToList();
    }

    static string DecodeSsid(ReadOnlySpan<byte> b)
    {
        try { return new UTF8Encoding(false, true).GetString(b); }
        catch (DecoderFallbackException) { return Convert.ToHexString(b).ToLowerInvariant(); }   // 깨진 글자면 16진수로
    }

    /// <summary>중심 주파수(MHz) → 채널. 2.4 GHz(1~14)·5 GHz·6 GHz. 모르면 null.</summary>
    public static int? Channel(int mhz) => mhz switch
    {
        2484 => 14,
        >= 2412 and <= 2472 => (mhz - 2407) / 5,
        >= 5160 and <= 5885 => (mhz - 5000) / 5,
        >= 5955 and <= 7115 => (mhz - 5950) / 5,
        _ => null,
    };

    /// <summary>뒤쪽의 응답 없는 홉(*)을 잘라 낸다. 앞·가운데의 *는 자리를 지키려고 남긴다.</summary>
    public static List<Hop> TrimHops(IEnumerable<Hop> hops)
    {
        var l = hops.OrderBy(h => h.N).ToList();
        while (l.Count > 0 && l[^1].Ip is null) l.RemoveAt(l.Count - 1);
        return l;
    }

    /// <summary>
    /// 언제 잴지. 이더넷 키가 새로 생기거나 바뀌면 곧바로, 같은 이더넷이 이어지면 Interval마다. 이더넷이 아니면(null) 재지 않고 잊는다.
    /// 실패해도 Due가 true였던 때를 잰 것으로 친다(경로가 막힌 곳에서 1분마다 다시 재지 않게).
    /// </summary>
    public sealed class Schedule(TimeSpan interval)
    {
        string? _key;
        DateTime _last;

        public bool Due(string? ethernetKey, DateTime now)
        {
            if (ethernetKey is null) { _key = null; return false; }
            if (ethernetKey != _key || now - _last >= interval) { _key = ethernetKey; _last = now; return true; }
            return false;
        }
    }
}
