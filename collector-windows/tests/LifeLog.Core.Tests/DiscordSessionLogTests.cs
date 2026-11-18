using System.Text.Json;
using LifeLog.Core;
using Xunit;

namespace LifeLog.Core.Tests;

/// <summary>디스코드 봇 세션 기록: 가짜 게이트웨이 이벤트로 타임라인·이름 바뀜·파일 연결(설계 문서 "디스코드 봇 메타데이터.md" 1-1 모양).</summary>
public class DiscordSessionLogTests
{
    static readonly DateTimeOffset T0 = new(2026, 11, 19, 13, 5, 0, TimeSpan.FromHours(9));
    static DateTimeOffset At(int sec) => T0.AddSeconds(sec);
    static readonly DiscordVoiceFlags Plain = new();

    static DiscordSessionLog New() => new("111", "우리 서버", "222", "일반", botUserId: "333", targetUserId: "444", T0, suffix: "1a2b3c4d");

    static JsonElement Doc(DiscordSessionLog s) => JsonDocument.Parse(s.ToJson()).RootElement;

    [Fact]
    public void Shape_and_paths()
    {
        var s = New();
        var j = Doc(s);
        Assert.Equal("discord-bot-session", j.GetProperty("kind").GetString());
        Assert.Equal(1, j.GetProperty("schema").GetInt32());
        Assert.Equal("20261119-130500-1a2b3c4d", s.Id);
        Assert.Equal("2026-11-19/20261119-130500-session-1a2b3c4d.session.json", s.Rel);
        Assert.Equal("111", j.GetProperty("guild").GetProperty("id").GetString());
        Assert.Equal("일반", j.GetProperty("channel").GetProperty("name").GetString());
        Assert.Equal("333", j.GetProperty("bot_user_id").GetString());
        Assert.Equal("444", j.GetProperty("target_user_id").GetString());
        Assert.Equal(JsonValueKind.Null, j.GetProperty("end").ValueKind);
        Assert.Equal("2026-11-19T13:05:00.000+09:00", j.GetProperty("start").GetString());
    }

    [Fact]
    public void Presence_timeline_from_present_join_leave_close()
    {
        var s = New();
        s.Present(T0, "444", Plain);
        s.Present(T0, "555", Plain with { SelfMute = true });
        s.Present(T0, "333", Plain);           // 봇 자신은 기록하지 않음
        s.Join(At(10), "666", Plain);
        s.Leave(At(20), "555");
        s.Leave(At(21), "999");                // 없던 사람은 무시
        s.Close(At(30));
        var j = Doc(s);
        var p = j.GetProperty("presence").EnumerateArray().ToList();
        Assert.Equal(3, p.Count);
        Assert.Equal(["444", "555"], p[0].GetProperty("user_ids").EnumerateArray().Select(x => x.GetString()!).ToArray());
        Assert.Equal("2026-11-19T13:05:10.000+09:00", p[0].GetProperty("end").GetString());
        Assert.Equal(["444", "555", "666"], p[1].GetProperty("user_ids").EnumerateArray().Select(x => x.GetString()!).ToArray());
        Assert.Equal(["444", "666"], p[2].GetProperty("user_ids").EnumerateArray().Select(x => x.GetString()!).ToArray());
        Assert.Equal("2026-11-19T13:05:30.000+09:00", p[2].GetProperty("end").GetString());
        var types = j.GetProperty("events").EnumerateArray().Select(e => e.GetProperty("type").GetString() + ":" + e.GetProperty("user_id").GetString()).ToArray();
        Assert.Equal(["present:444", "present:555", "join:666", "leave:555"], types);
        Assert.True(j.GetProperty("events")[1].GetProperty("self_mute").GetBoolean());
        Assert.Equal("2026-11-19T13:05:30.000+09:00", j.GetProperty("end").GetString());
        // 닫은 뒤 이벤트는 무시
        s.Join(At(40), "777", Plain);
        Assert.DoesNotContain("777", s.ToJson());
    }

    [Fact]
    public void State_events_only_when_flags_change()
    {
        var s = New();
        s.Present(T0, "555", Plain);
        s.State(At(1), "555", Plain);                          // 같음 → 없음
        s.Join(At(2), "555", Plain with { SelfDeaf = true });  // 이미 있음 → state
        s.State(At(3), "555", Plain with { SelfDeaf = true, Stream = true });
        s.State(At(4), "888", Plain with { Mute = true });     // 채널에 없는 사람 → 없음
        var ev = Doc(s).GetProperty("events").EnumerateArray().ToList();
        Assert.Equal(["present", "state", "state"], ev.Select(e => e.GetProperty("type").GetString()!).ToArray());
        Assert.True(ev[1].GetProperty("self_deaf").GetBoolean());
        Assert.True(ev[2].GetProperty("stream").GetBoolean());
        Assert.Single(Doc(s).GetProperty("presence").EnumerateArray());   // 상태 바뀜은 presence를 나누지 않음
    }

    [Fact]
    public void Name_first_known_then_changed_with_before()
    {
        var s = New();
        Assert.False(s.Name(T0, "555", new("abc", null, null, false)));   // 세션에 아직 안 나온 사람
        s.Join(At(1), "555", Plain);
        Assert.True(s.Name(At(1), "555", new("abc", null, null, false)));
        Assert.False(s.Name(At(2), "555", new("abc", null, null, false)));   // 같음
        Assert.True(s.Name(At(300), "555", new("abc", "에이", "닉", false)));
        var j = Doc(s);
        var names = j.GetProperty("events").EnumerateArray().Where(e => e.GetProperty("type").GetString() == "name").ToList();
        Assert.Equal(2, names.Count);
        Assert.False(names[0].TryGetProperty("before", out _));
        Assert.Equal(JsonValueKind.Null, names[0].GetProperty("global_name").ValueKind);
        Assert.Equal("에이", names[1].GetProperty("global_name").GetString());
        Assert.Equal("닉", names[1].GetProperty("nick").GetString());
        Assert.Equal(JsonValueKind.Null, names[1].GetProperty("before").GetProperty("global_name").ValueKind);
        var u = j.GetProperty("users").GetProperty("555");
        Assert.Equal("에이", u.GetProperty("global_name").GetString());
        Assert.False(u.GetProperty("bot").GetBoolean());
        // 나간 뒤에도 세션 사람이라 이름 바뀜은 남는다
        s.Leave(At(400), "555");
        Assert.True(s.Name(At(401), "555", new("abc", "에이2", "닉", false)));
    }

    [Fact]
    public void Files_link_audio_to_user_id_and_make_user_known()
    {
        var s = New();
        s.File("2026-11-19/20261119-130512-555-ab12cd34.m4a", "555", At(12), At(70));
        Assert.True(s.Name(At(70), "555", new("abc", "에이", null, false)));   // 파일로 나온 사람도 이름을 받는다
        var j = Doc(s);
        var f = j.GetProperty("files")[0];
        Assert.Equal("555", f.GetProperty("user_id").GetString());
        Assert.Equal("2026-11-19/20261119-130512-555-ab12cd34.m4a", f.GetProperty("file").GetString());
        Assert.Equal("2026-11-19T13:06:10.000+09:00", f.GetProperty("end").GetString());
        Assert.True(j.GetProperty("users").TryGetProperty("555", out _));
    }

    [Fact]
    public void Dirty_flag_and_write_to_disk()
    {
        var s = New();
        Assert.True(s.Dirty);
        var dir = Path.Combine(Path.GetTempPath(), "sb-session-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            s.WriteTo(dir);
            Assert.False(s.Dirty);
            s.Join(At(1), "555", Plain);
            Assert.True(s.Dirty);
            s.WriteTo(dir);
            var path = Path.Combine(dir, "2026-11-19", "20261119-130500-session-1a2b3c4d.session.json");
            Assert.True(File.Exists(path));
            Assert.False(File.Exists(path + ".tmp"));
            Assert.Contains("\"join\"", File.ReadAllText(path));
        }
        finally { try { Directory.Delete(dir, true); } catch { } }
    }

    [Theory]
    [InlineData("abc", "에이", "닉", "에이")]
    [InlineData("abc", null, "닉", "abc")]
    [InlineData("", null, null, "555")]
    public void Default_name_is_global_then_username_then_id(string username, string? global, string? nick, string expected) =>
        Assert.Equal(expected, DiscordUserInfo.DefaultName(new(username, global, nick, false), "555"));
}
