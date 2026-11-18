"""디스코드 봇 녹음 메타데이터(2026-11-19). 모양: 설계 문서 "디스코드 봇 메타데이터.md"(SecondBrainCollector/Documents, name-history 세션과 공통 약속).

수집기(DiscordBotSource)가 쓰는 것:
  사람별 오디오 옆 .json   {user_id, speaker, username, global_name, nick, bot, session, guild_id, guild, channel_id, channel, start, end, ...}
  세션 파일 *.session.json  {kind: "discord-bot-session", id, guild, channel, bot_user_id, target_user_id, start, end, users, events, presence, files}

여기서 하는 일:
  who(meta)          → (트랙 키 = user_id, 기본 표시 이름, speakerId "discord:<id>"). 사람 식별은 사용자 id, 이름은 id에서 푼다.
                       기본 이름 = global_name → username → id. 옛 .json(global_name 키 없음, 2026-11-19 전)은 그때 speaker(nick 우선이던 표시 이름).
                       지정 이름(name-history의 이름 구간)은 서버가 올릴 때 speakerId로 푼다(Api RecordingStore, IPlatformNames).
  bundle_meta(...)   → 전사 결과 본문의 meta = {"discord": {sessions, targetUserId, participants}}. *Ms는 묶음 시작 기준(음수 = 전).
                       세션 파일이 없는 옛 녹음은 sessions = [] 이고 participants는 사람별 .json에서.
"""
import datetime as dt, json
from pathlib import Path

PLATFORM = "discord"
MARGIN_MS = 60_000   # 묶음 앞뒤로 이만큼 presence·events를 더 남긴다(누가 막 들어왔다 나갔는지)


def load(path: Path):
    """JSON 파일. 없거나 깨졌으면 None."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def who(meta: dict | None):
    """(트랙 키, 기본 표시 이름, speakerId). .json이 없으면 ('', None, None)"""
    if not meta:
        return "", None, None
    uid = str(meta.get("user_id") or "")
    if "global_name" in meta:   # 2026-11-19부터: 전역 이름이 기본
        name = meta.get("global_name") or meta.get("username") or uid or meta.get("speaker")
    else:
        name = meta.get("speaker") or meta.get("username") or uid
    return (uid or name or ""), (name or None), (f"{PLATFORM}:{uid}" if uid else None)


def ms(s) -> int | None:
    if not s: return None
    try:
        return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _flags(e: dict) -> dict:
    return {"selfMute": bool(e.get("self_mute")), "selfDeaf": bool(e.get("self_deaf")), "mute": bool(e.get("mute")), "deaf": bool(e.get("deaf")),
            "stream": bool(e.get("stream")), "video": bool(e.get("video"))}


def _names(u: dict) -> dict:
    return {"username": u.get("username"), "globalName": u.get("global_name"), "nick": u.get("nick")}


def session_meta(s: dict, b_start: int, b_end: int, margin: int = MARGIN_MS):
    """세션 하나 → 묶음 기준 {id, guild, channel, startMs, endMs, presence, events} + (창 안에서 마지막으로 안 이름 {id: 이름 셋}, 창 안 사람 id 집합).
    창 = [묶음 시작 - margin, 묶음 끝 + margin]. 세션이 창과 안 겹치면 None."""
    lo, hi = b_start - margin, b_end + margin
    s_start = ms(s.get("start"))
    if s_start is None: return None
    evs = s.get("events") or []
    s_end = ms(s.get("end")) or max([ms(e.get("at")) or s_start for e in evs] + [ms(f.get("end")) or s_start for f in s.get("files") or []] + [s_start])
    if s_end < lo or s_start > hi: return None
    presence, people = [], set()
    for p in s.get("presence") or []:
        a, z = ms(p.get("start")), ms(p.get("end")) or s_end
        if a is None or z < lo or a > hi: continue
        ids = [str(x) for x in p.get("user_ids") or []]
        people.update(ids)
        presence.append({"startMs": max(a, lo) - b_start, "endMs": min(z, hi) - b_start, "userIds": ids})
    events, names = [], {}
    for e in evs:
        at = ms(e.get("at"))
        if at is None: continue
        uid = str(e.get("user_id") or "")
        t = e.get("type")
        if t == "name" and at <= hi:   # 창 끝까지 알게 된 마지막 이름(창 앞에서 안 이름도)
            names[uid] = {"username": e.get("username"), "global_name": e.get("global_name"), "nick": e.get("nick")}
        if at < lo or at > hi: continue
        x = {"atMs": at - b_start, "type": t, "userId": uid}
        if t in ("present", "join", "state"): x.update(_flags(e))
        elif t == "name":
            x.update(_names(e))
            if isinstance(e.get("before"), dict): x["before"] = _names(e["before"])
        if t in ("present", "join"): people.add(uid)
        events.append(x)
    out = {"id": s.get("id"), "guild": s.get("guild"), "channel": s.get("channel"), "startMs": s_start - b_start, "endMs": s_end - b_start,
           "presence": presence, "events": events}
    return out, names, people


def bundle_meta(items: list, b_start: int, b_end: int, root: Path, margin: int = MARGIN_MS):
    """묶음의 meta.discord. items = [(시작 ms, 끝 ms, 사람별 .json dict 또는 None)], root = discord-bot 폴더(세션 파일 상대 경로의 기준).
    세션은 사람별 .json의 session이 가리키는 것만 읽는다."""
    sessions, targets, seen, bot_self = [], [], set(), set()
    users: dict = {}        # id → 이름 셋(세션 users → 창 안 마지막 name 이벤트 → 사람별 .json 순으로 덮음)
    bots: dict = {}
    people: list = []       # 나온 차례
    speech: dict = {}

    def add(uid):
        if uid and uid not in people: people.append(uid)

    for start, end, m in items:
        if not m: continue
        uid = str(m.get("user_id") or "")
        if not uid: continue
        add(uid)
        speech[uid] = speech.get(uid, 0) + max(0, end - start)
        rel = m.get("session")
        if rel and rel not in seen:
            seen.add(rel)
            s = load(root / rel)
            if not s or s.get("kind") != "discord-bot-session": continue
            got = session_meta(s, b_start, b_end, margin)
            if got is None: continue
            sm, names, ppl = got
            sessions.append(sm)
            if s.get("target_user_id"): targets.append(str(s["target_user_id"]))
            if s.get("bot_user_id"): bot_self.add(str(s["bot_user_id"]))
            for k, u in (s.get("users") or {}).items():
                users.setdefault(str(k), dict(u))
                bots[str(k)] = bool(u.get("bot"))
            for k, u in names.items(): users[k] = {**users.get(k, {}), **u}
            for k in sorted(ppl): add(k)
    for start, end, m in items:   # 세션이 없는 옛 녹음: 사람별 .json의 이름
        if not m: continue
        uid = str(m.get("user_id") or "")
        if uid and uid not in users:
            users[uid] = {"username": m.get("username") or None, "global_name": m.get("global_name"), "nick": m.get("nick")}
            bots[uid] = bool(m.get("bot"))
    participants = []
    for uid in people:
        if uid in bot_self: continue
        u = users.get(uid, {})
        participants.append({"userId": uid, "username": u.get("username"), "globalName": u.get("global_name"), "nick": u.get("nick"),
                             "bot": bots.get(uid, False), "speechMs": speech.get(uid, 0)})
    sessions.sort(key=lambda x: x["startMs"])
    return {"sessions": sessions, "targetUserId": targets[0] if targets else None, "participants": participants}
