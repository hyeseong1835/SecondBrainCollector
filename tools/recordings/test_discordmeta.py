"""discordmeta.py 시험(2026-11-19): 사람별 .json → 화자 id·기본 이름, 세션 파일 → 묶음 meta(presence·이벤트 자르기·이름·참여자), 옛 녹음 호환.
가짜 파일·임시 폴더만 쓴다."""
import datetime as dt, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import discordmeta as DM

KST = dt.timezone(dt.timedelta(hours=9))
T0 = dt.datetime(2026, 11, 19, 13, 5, 0, tzinfo=KST)


def iso(sec):
    return (T0 + dt.timedelta(seconds=sec)).isoformat(timespec="milliseconds")


def ms(sec):
    return int((T0 + dt.timedelta(seconds=sec)).timestamp() * 1000)


SESSION_REL = "2026-11-19/20261119-130500-session-1a2b3c4d.session.json"


def session(end=600):
    flags = {"self_mute": False, "self_deaf": False, "mute": False, "deaf": False, "stream": False, "video": False}
    return {
        "kind": "discord-bot-session", "schema": 1, "id": "20261119-130500-1a2b3c4d",
        "guild": {"id": "111", "name": "우리 서버"}, "channel": {"id": "222", "name": "일반"},
        "bot_user_id": "333", "target_user_id": "444", "start": iso(0), "end": iso(end) if end else None,
        "users": {"444": {"username": "hs", "global_name": "혜성", "nick": None, "bot": False},
                  "555": {"username": "abc", "global_name": "에이2", "nick": "닉", "bot": False},
                  "777": {"username": "musicbot", "global_name": None, "nick": None, "bot": True}},
        "events": [
            {"at": iso(0), "type": "present", "user_id": "444", **flags},
            {"at": iso(0), "type": "name", "user_id": "444", "username": "hs", "global_name": "혜성", "nick": None},
            {"at": iso(100), "type": "join", "user_id": "555", **flags, "self_mute": True},
            {"at": iso(100), "type": "name", "user_id": "555", "username": "abc", "global_name": "에이", "nick": None},
            {"at": iso(150), "type": "join", "user_id": "777", **flags},
            {"at": iso(200), "type": "state", "user_id": "555", **flags},
            {"at": iso(500), "type": "name", "user_id": "555", "username": "abc", "global_name": "에이2", "nick": "닉",
             "before": {"username": "abc", "global_name": "에이", "nick": None}},
            {"at": iso(550), "type": "leave", "user_id": "555"},
        ],
        "presence": [
            {"start": iso(0), "end": iso(100), "user_ids": ["444"]},
            {"start": iso(100), "end": iso(150), "user_ids": ["444", "555"]},
            {"start": iso(150), "end": iso(550), "user_ids": ["444", "555", "777"]},
            {"start": iso(550), "end": iso(end) if end else None, "user_ids": ["444", "777"]},
        ],
        "files": [{"file": "2026-11-19/a.m4a", "user_id": "555", "start": iso(210), "end": iso(260)}],
    }


def side(uid, **kw):
    m = {"source": "discord-bot", "user_id": uid, "speaker": kw.pop("speaker", "x"), "username": "abc", "guild_id": "111", "guild": "우리 서버",
         "channel_id": "222", "channel": "일반"}
    m.update(kw)
    return m


class Who(unittest.TestCase):
    def test_new_sidecar_default_is_global_name(self):
        self.assertEqual(DM.who(side("555", speaker="닉", global_name="에이", nick="닉", session=SESSION_REL)), ("555", "에이", "discord:555"))

    def test_new_sidecar_without_global_name_uses_username(self):
        self.assertEqual(DM.who(side("555", global_name=None, nick="닉")), ("555", "abc", "discord:555"))

    def test_old_sidecar_keeps_old_display_name(self):
        self.assertEqual(DM.who(side("555", speaker="닉네임")), ("555", "닉네임", "discord:555"))

    def test_missing(self):
        self.assertEqual(DM.who(None), ("", None, None))


class BundleMeta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        p = self.root / SESSION_REL
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(session(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sessions_presence_events_clipped_to_bundle(self):
        # 묶음 = 210~260초(파일 하나), 창 = 앞뒤 60초 → 150~320초
        items = [(ms(210), ms(260), side("555", global_name="에이", session=SESSION_REL))]
        m = DM.bundle_meta(items, ms(210), ms(260), self.root)
        self.assertEqual(m["targetUserId"], "444")
        (s,) = m["sessions"]
        self.assertEqual((s["id"], s["guild"]["name"], s["channel"]["id"]), ("20261119-130500-1a2b3c4d", "우리 서버", "222"))
        self.assertEqual((s["startMs"], s["endMs"]), (-210_000, 390_000))
        self.assertEqual([(p["startMs"], p["endMs"], p["userIds"]) for p in s["presence"]],
                         [(-60_000, -60_000, ["444", "555"]), (-60_000, 110_000, ["444", "555", "777"])])
        self.assertEqual([(e["atMs"], e["type"], e["userId"]) for e in s["events"]], [(-60_000, "join", "777"), (-10_000, "state", "555")])
        self.assertFalse(s["events"][1]["selfMute"])
        # 참여자: 파일 주인 먼저, 그다음 창 안 presence의 사람. 봇 자신(333) 없음, 다른 봇은 bot=True
        self.assertEqual([p["userId"] for p in m["participants"]], ["555", "444", "777"])
        p555 = m["participants"][0]
        self.assertEqual((p555["globalName"], p555["nick"], p555["speechMs"]), ("에이", None, 50_000))   # 창 끝(320초)까지 마지막 이름 = 바뀌기 전
        self.assertTrue(m["participants"][2]["bot"])

    def test_name_change_inside_window(self):
        items = [(ms(480), ms(520), side("555", global_name="에이2", nick="닉", session=SESSION_REL))]
        m = DM.bundle_meta(items, ms(480), ms(520), self.root)
        ev = [e for e in m["sessions"][0]["events"] if e["type"] == "name"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["globalName"], ev[0]["nick"], ev[0]["before"]["globalName"]), ("에이2", "닉", "에이"))
        self.assertEqual(m["participants"][0]["globalName"], "에이2")

    def test_open_session_end_from_last_event(self):
        p = self.root / SESSION_REL
        p.write_text(json.dumps(session(end=None), ensure_ascii=False), encoding="utf-8")
        m = DM.bundle_meta([(ms(540), ms(560), side("555", session=SESSION_REL, global_name="에이2"))], ms(540), ms(560), self.root)
        s = m["sessions"][0]
        self.assertEqual(s["endMs"], ms(550) - ms(540))   # 마지막 이벤트(leave 550초)
        self.assertEqual(s["presence"][-1]["endMs"], ms(550) - ms(540))

    def test_old_recordings_without_session(self):
        items = [(ms(0), ms(10), side("555", speaker="닉")), (ms(20), ms(30), side("444", speaker="혜성", username="hs")), (ms(40), ms(45), None)]
        m = DM.bundle_meta(items, ms(0), ms(45), self.root)
        self.assertEqual(m["sessions"], [])
        self.assertIsNone(m["targetUserId"])
        self.assertEqual([(p["userId"], p["username"], p["speechMs"]) for p in m["participants"]], [("555", "abc", 10_000), ("444", "hs", 10_000)])

    def test_missing_or_broken_session_file(self):
        (self.root / "2026-11-19" / "bad.session.json").write_text("{", encoding="utf-8")
        items = [(ms(0), ms(10), side("555", session="2026-11-19/none.session.json")), (ms(0), ms(10), side("444", session="2026-11-19/bad.session.json"))]
        m = DM.bundle_meta(items, ms(0), ms(10), self.root)
        self.assertEqual(m["sessions"], [])
        self.assertEqual(len(m["participants"]), 2)

    def test_session_outside_window_skipped(self):
        items = [(ms(5000), ms(5010), side("555", session=SESSION_REL))]
        self.assertEqual(DM.bundle_meta(items, ms(5000), ms(5010), self.root)["sessions"], [])


class AnalyzeBody(unittest.TestCase):
    """analyze.finish_bundle이 디스코드 봇 구간에 speakerId를, 본문에 meta를 붙인다(다른 원천은 meta None)."""
    def test_finish_bundle_discord_bot(self):
        import analyze as A, bundles as B
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        day = root / "discord-bot" / "2026-11-19"
        day.mkdir(parents=True)
        (root / "discord-bot" / SESSION_REL).write_text(json.dumps(session(), ensure_ascii=False), encoding="utf-8")
        f = day / "a.m4a"
        f.write_bytes(b"")
        f.with_suffix(".json").write_text(json.dumps(side("555", global_name="에이", session=SESSION_REL), ensure_ascii=False), encoding="utf-8")
        track, speaker = A.discord_bot_who(A.discord_bot_meta(f))
        it = B.Item("discord-bot", "2026-11-19/a.m4a", ms(210), ms(260), f, track, speaker)
        b = B.Bundle("discord-bot", [it])
        old_root, old_chosen = A.ROOT, A.CHOSEN
        A.ROOT = root
        A.CHOSEN = type("C", (), {"ref": staticmethod(lambda: {"name": "x", "version": "1", "settingsVersion": 1})})()
        try:
            body = A.finish_bundle(b, [None], [[{"startMs": 0, "endMs": 1000, "text": "안녕"}]], None, {"d": None}, 999, announce=False)   # 화자 분리기 없음 → 목소리 등록 건너뜀
        finally:
            A.ROOT, A.CHOSEN = old_root, old_chosen
        self.assertEqual(body["segments"][0]["speaker"], "에이")
        self.assertEqual(body["segments"][0]["speakerId"], "discord:555")
        self.assertEqual(body["meta"]["discord"]["sessions"][0]["guild"]["id"], "111")
        self.assertEqual(body["meta"]["discord"]["participants"][0]["userId"], "555")


if __name__ == "__main__":
    unittest.main()
