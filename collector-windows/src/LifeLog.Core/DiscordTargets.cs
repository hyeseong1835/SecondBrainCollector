namespace LifeLog.Core;

/// <summary>
/// 디스코드 봇 녹음 '자동' 목록(2026-11-13, 플러그인 win-discord-bot 설정 auto_targets) 항목 규칙. 수집기(따라갈지 판단)와 데스크톱(목록 고르기)이 같이 쓴다.
/// 항목은 "guild:&lt;서버 ID&gt;"(그 서버의 모든 음성 채널) 또는 "channel:&lt;채널 ID&gt;". 접두어 없는 숫자는 서버·채널 어느 쪽과 맞아도 된다.
/// </summary>
public static class DiscordTargets
{
    public static string Guild(string id) => "guild:" + id;
    public static string Channel(string id) => "channel:" + id;

    /// <summary>(종류 "guild"·"channel"·"", ID). 앞뒤 공백·대소문자는 무시.</summary>
    public static (string Kind, string Id) Parse(string raw)
    {
        var t = raw.Trim();
        var i = t.IndexOf(':');
        return i < 0 ? ("", t) : (t[..i].Trim().ToLowerInvariant(), t[(i + 1)..].Trim());
    }

    /// <summary>목록에 이 서버나 이 채널이 있나.</summary>
    public static bool Listed(IEnumerable<string>? targets, ulong guildId, ulong channelId)
    {
        if (targets is null) return false;
        string g = guildId.ToString(), c = channelId.ToString();
        foreach (var raw in targets)
        {
            var (kind, id) = Parse(raw);
            if (id.Length == 0) continue;
            if ((kind is "guild" or "") && id == g) return true;
            if ((kind is "channel" or "") && id == c) return true;
        }
        return false;
    }
}
