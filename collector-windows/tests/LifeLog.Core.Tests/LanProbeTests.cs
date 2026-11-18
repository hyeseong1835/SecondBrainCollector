using Xunit;
using System.Buffers.Binary;
using System.Text;
using LifeLog.Core;

namespace LifeLog.Core.Tests;

/// <summary>랜선 위치 지문(2026-11-19): WLAN BSS 목록 해석·채널·홉 정리·측정 주기. 네이티브 호출 없이 바이트를 만들어 본다.</summary>
public class LanProbeTests
{
    static byte[] BssList(params (string Ssid, string Bssid, int Rssi, int FreqKhz)[] entries)
    {
        var buf = new byte[8 + entries.Length * LanProbe.BssEntrySize];
        BinaryPrimitives.WriteUInt32LittleEndian(buf, (uint)buf.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(buf.AsSpan(4), (uint)entries.Length);
        for (var i = 0; i < entries.Length; i++)
        {
            var e = buf.AsSpan(8 + i * LanProbe.BssEntrySize, LanProbe.BssEntrySize);
            var ssid = Encoding.UTF8.GetBytes(entries[i].Ssid);
            BinaryPrimitives.WriteUInt32LittleEndian(e, (uint)ssid.Length);
            ssid.CopyTo(e[4..]);
            Convert.FromHexString(entries[i].Bssid.Replace(":", "")).CopyTo(e[40..]);
            BinaryPrimitives.WriteInt32LittleEndian(e[56..], entries[i].Rssi);
            BinaryPrimitives.WriteUInt32LittleEndian(e[92..], (uint)entries[i].FreqKhz);
        }
        return buf;
    }

    [Fact]
    public void Bss_list_is_parsed_strongest_first()
    {
        var buf = BssList(("U+Net2051", "50:46:AE:E0:20:50", -81, 2_437_000), ("U+Net78E7", "0C:96:CD:EF:78:E6", -48, 2_462_000),
            ("기숙사5G", "0C:96:CD:EF:78:E7", -60, 5_180_000), ("", "00:00:00:00:00:00", -40, 2_412_000));
        var aps = LanProbe.ParseBssList(buf);
        Assert.Equal(["0c:96:cd:ef:78:e6", "0c:96:cd:ef:78:e7", "50:46:ae:e0:20:50"], aps.Select(a => a.Bssid));
        Assert.Equal(new LanProbe.Ap("0c:96:cd:ef:78:e6", "U+Net78E7", -48, 2462, 11), aps[0]);
        Assert.Equal(("기숙사5G", 36), (aps[1].Ssid, aps[1].Channel));
    }

    [Fact]
    public void Bss_list_tolerates_short_buffers_and_caps_count()
    {
        Assert.Empty(LanProbe.ParseBssList([]));
        var buf = BssList(("a", "02:00:00:00:00:01", -50, 2_412_000), ("b", "02:00:00:00:00:02", -60, 2_412_000));
        Assert.Single(LanProbe.ParseBssList(buf.AsSpan(0, 8 + LanProbe.BssEntrySize + 10)));   // 둘째 항목이 잘림
        Assert.Single(LanProbe.ParseBssList(buf, max: 1));
    }

    [Fact]
    public void Channels_from_frequency()
    {
        Assert.Equal(1, LanProbe.Channel(2412));
        Assert.Equal(14, LanProbe.Channel(2484));
        Assert.Equal(149, LanProbe.Channel(5745));
        Assert.Equal(5, LanProbe.Channel(5975));
        Assert.Null(LanProbe.Channel(900));
    }

    [Fact]
    public void Trailing_silent_hops_are_trimmed()
    {
        var hops = LanProbe.TrimHops([new(3, null, null), new(1, "192.168.0.1", 0), new(2, null, null), new(4, "10.0.0.1", 5), new(5, null, null)]);
        Assert.Equal([1, 2, 3, 4], hops.Select(h => h.N));
    }

    [Fact]
    public void Probe_when_ethernet_starts_changes_or_interval_passes()
    {
        var s = new LanProbe.Schedule(TimeSpan.FromMinutes(30));
        var t = new DateTime(2026, 11, 19, 0, 0, 0, DateTimeKind.Utc);
        Assert.False(s.Due(null, t));
        Assert.True(s.Due("lan:aa", t));                          // 이더넷 붙음
        Assert.False(s.Due("lan:aa", t.AddMinutes(10)));
        Assert.True(s.Due("lan:aa", t.AddMinutes(30)));           // 30분
        Assert.True(s.Due("lan:bb", t.AddMinutes(31)));           // 바뀜
        Assert.False(s.Due(null, t.AddMinutes(32)));              // 와이파이로
        Assert.True(s.Due("lan:bb", t.AddMinutes(33)));           // 다시 붙으면 곧바로
    }
}
