"""이름 구간 시험: python -m unittest test_callnames (가짜 API·임시 폴더만. 서버·실제 목소리 저장소·녹음 폴더는 건드리지 않음)
서버 NameBook(SecondBrainApi tests PersonNameRuleTests)과 같은 경우를 같은 답으로 본다."""
import datetime as dt, json, tempfile, unittest
from pathlib import Path
import numpy as np
import callnames as CN
import voices as V

D = dt.date.fromisoformat


def row(i, person, name, f=None, t=None):
    return {"id": i, "person": person, "name": name, "from": f, "to": t, "phone": None, "note": None, "updatedAt": "2026-11-18T00:00:00"}


BOOK_ROWS = [
    row(1, "김청아", "CK 김청아 누나", "2026-04-01"),
    row(2, "김청아", "CK26 김청아님", "2026-03-01", "2026-03-31"),
    row(3, "누나", "누나"),
    row(4, "옆집 누나", "누나", "2026-05-01", "2026-05-31"),
]


class BookTests(unittest.TestCase):
    def setUp(self):
        self.b = CN.Book(BOOK_ROWS)

    def test_period_and_narrowest(self):
        b = self.b
        self.assertEqual(b.person("CK김청아 누나", D("2026-09-15")), "김청아")
        self.assertEqual(b.person("CK 김청아 누나", D("2026-03-15")), "CK 김청아 누나")   # 기간 밖: 그대로
        self.assertEqual(b.person("ck26 김청아님", D("2026-03-31")), "김청아")            # 끝 날짜 포함
        self.assertIsNone(b.find("CK26 김청아님", D("2026-04-01")))
        self.assertEqual(b.person("누나", D("2026-05-10")), "옆집 누나")
        self.assertEqual(b.person("누나", D("2026-06-01")), "누나")
        self.assertEqual(b.person(" 모르는 이름 ", D("2026-06-01")), "모르는 이름")
        self.assertEqual(b.person("누나"), "누나")                 # 날짜 모름: 끝이 열린 줄
        self.assertEqual(b.person("CK26 김청아님"), "김청아")

    def test_same_span_and_open_ends(self):
        b = CN.Book([row(1, "갑", "X", "2026-01-01", "2026-01-10"), row(2, "을", "X", "2026-01-05", "2026-01-14"), row(3, "병", "X", "2026-01-05", "2026-01-14")])
        self.assertEqual(b.person("X", D("2026-01-07")), "병")
        self.assertEqual(b.person("X", D("2026-01-02")), "갑")
        o = CN.Book([row(1, "갑", "Y"), row(2, "을", "Y", "2026-01-01")])
        self.assertEqual(o.person("Y", D("2026-02-01")), "을")
        self.assertEqual(o.person("Y", D("2025-12-01")), "갑")

    def test_unambiguous(self):
        # 누나는 두 사람(누나·옆집 누나) → 별칭 아님, 이름 = 사람(누나→누나)도 아님
        self.assertEqual(self.b.unambiguous(), {"CK 김청아 누나": "김청아", "CK26 김청아님": "김청아"})

    def test_file_names(self):
        p = Path("call/2026-03-10/통화 CK26 김청아님_260310_101500.m4a")
        self.assertEqual(CN.date_of(p), D("2026-03-10"))
        self.assertEqual(CN.raw_partner(p), "CK26 김청아님")
        self.assertEqual(CN.partner(p, self.b), "김청아")
        self.assertEqual(CN.partner(Path("call/x/통화 CK26 김청아님_261110_101500.m4a"), self.b), "CK26 김청아님")   # 기간 밖
        self.assertIsNone(CN.date_of(Path("2026-11-13/20261113-133217-503bb74a.m4a")))
        self.assertIsNone(CN.date_of(Path("통화 x_261399_000000.m4a")))   # 없는 날짜


class LoadTests(unittest.TestCase):
    def tearDown(self):
        CN.BOOK, CN.DISCORD = CN.Book(), CN.Book(platform=CN.DISCORD_P)

    def test_load_and_fail(self):
        calls, logs = [], []
        rows = BOOK_ROWS + [drow(9, "이윤환", "윤환", "669728088623546368", "2026-11-12")]
        def call(method, path, body=None):
            calls.append((method, path)); return rows
        CN.load(call, log=logs.append)
        self.assertEqual(calls, [("GET", "/api/people/names")])
        self.assertEqual((len(CN.BOOK), len(CN.DISCORD)), (4, 1))
        self.assertEqual(CN.partner(Path("통화 CK 김청아 누나_260915_100000.m4a")), "김청아")
        self.assertEqual(CN.discord_name("669728088623546368", "윤환"), "이윤환")
        def broken(method, path, body=None): raise OSError("offline")
        CN.load(broken, log=logs.append)
        self.assertEqual(len(CN.BOOK), 0)                                   # 오프라인: 이름 그대로
        self.assertEqual(CN.partner(Path("통화 CK 김청아 누나_260915_100000.m4a")), "CK 김청아 누나")
        self.assertIn("못 읽음", logs[-1])

    def test_old_server_falls_back_to_call_names(self):
        calls = []
        def call(method, path, body=None):
            calls.append(path)
            if path == "/api/people/names": raise OSError("HTTP Error 404: Not Found")
            return BOOK_ROWS
        CN.load(call, log=lambda *_: None)
        self.assertEqual(calls, ["/api/people/names", "/api/people/call-names"])
        self.assertEqual((len(CN.BOOK), len(CN.DISCORD)), (4, 0))


def drow(i, person, name, account, f=None, t=None, auto=False):
    return {"id": i, "platform": "discord", "account": account, "person": person, "name": name, "from": f, "to": t, "auto": auto, "note": None}


class DiscordTests(unittest.TestCase):
    def test_platforms_are_separate_and_assigned_only(self):
        rows = BOOK_ROWS + [
            drow(10, "윤환", "윤환", "1", "2026-11-12", "2026-11-17", auto=True),          # 자동뿐: 지정 이름 없음 → 전역 이름
            drow(11, "윤환2", "윤환2", "1", "2026-11-18", auto=True),
            drow(12, "김도영", "kingdoyoung", "2", "2026-11-13"),                          # 확정: 지정 이름
            {"id": 13, "platform": "kakao", "person": "누나", "name": ".누나", "from": None, "to": None, "auto": True},
        ]
        pb, dc = CN.Book(rows), CN.Book(rows, CN.DISCORD_P)
        self.assertEqual(len(pb), 4)                                         # 전화번호부엔 디스코드·카톡 줄이 없다
        self.assertIsNone(pb.find("윤환"))
        self.assertEqual(CN.discord_name("1", "윤환2", D("2026-11-19"), dc), "윤환2")   # 지정 전: 받은 기본 이름 그대로
        self.assertEqual(CN.discord_name("2", "도영", D("2026-11-01"), dc), "김도영")   # 첫 줄보다 이르면 가장 가까운 줄
        self.assertEqual(CN.discord_name("3", "누구", None, dc), "누구")
        self.assertEqual(dc.find_account("1", D("2026-11-15"))["name"], "윤환")
        self.assertEqual(dc.unambiguous(assigned_only=True), {"kingdoyoung": "김도영"})

    def test_sync_aliases_includes_assigned_discord_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp))
            e = V.unit(np.ones(V.DIM, np.float32))
            st.add(e, person="kingdoyoung", source="discord-bot", file="d/1")
            st.add(e, person="윤환", source="discord-bot", file="d/2")
            dc = CN.Book([drow(12, "김도영", "kingdoyoung", "2", "2026-11-13"), drow(10, "윤환", "윤환", "1", "2026-11-12", auto=True),
                          drow(14, "다른사람", "CK26 김청아님", "4", "2026-11-13")], CN.DISCORD_P)   # 전화번호부와 다른 사람: 별칭 안 만듦
            n = CN.sync_aliases(st, CN.Book(BOOK_ROWS), save=False, log=lambda *_: None, discord=dc)
            self.assertEqual(n, 2)                                           # CK 김청아 누나 → 김청아, kingdoyoung → 김도영
            self.assertEqual(st.canonical("kingdoyoung"), "김도영")
            self.assertEqual(st.canonical("윤환"), "윤환")                     # 지정 안 한 계정은 그대로
            self.assertEqual(st.canonical("CK26 김청아님"), "CK26 김청아님")

    def test_observations_from_bot_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-11-19"; day.mkdir()
            old = {"source": "discord-bot", "user_id": "686027641148669954", "speaker": "김도영", "username": "kingdoyoung_", "start": "2026-11-18T05:00:47+09:00"}
            new = {"source": "discord-bot", "user_id": "555", "speaker": "에이", "username": "abc", "global_name": "에이", "nick": "별명", "bot": False, "start": "2026-11-19T13:05:12+09:00"}
            nog = {"source": "discord-bot", "user_id": "556", "speaker": "def", "username": "def", "global_name": None, "bot": False, "start": "2026-11-19T13:06:00+09:00"}
            bot = {"source": "discord-bot", "user_id": "333", "speaker": "봇", "username": "bot", "global_name": "봇", "bot": True, "start": "2026-11-19T13:06:00+09:00"}
            ses = {"kind": "discord-bot-session", "schema": 1, "users": {"555": {"username": "abc", "global_name": "에이", "bot": False}, "333": {"bot": True}},
                   "events": [{"at": "2026-11-19T13:05:00+09:00", "type": "name", "user_id": "555", "username": "abc", "global_name": None, "nick": None},
                              {"at": "2026-11-20T00:10:00+09:00", "type": "name", "user_id": "555", "username": "abc", "global_name": "에이", "nick": None, "before": {"global_name": None}},
                              {"at": "2026-11-19T13:05:00+09:00", "type": "name", "user_id": "333", "username": "bot", "global_name": "봇"},
                              {"at": "2026-11-19T13:06:00+09:00", "type": "join", "user_id": "556"}]}
            for n, d in [("a.json", old), ("b.json", new), ("c.json", nog), ("d.json", bot), ("x-session-1.session.json", ses)]:
                (day / n).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            (day / "broken.json").write_text("{", encoding="utf-8")
            obs = CN.discord_observations(sorted(day.iterdir()))
            self.assertEqual([(o["account"], o["name"], o["date"], o["track"]) for o in obs], [
                ("686027641148669954", "김도영", "2026-11-18", False),   # 옛 파일: speaker(별명일 수 있음) → 만들기만
                ("555", "abc", "2026-11-19", True),                      # 세션 이벤트: 처음 앎(전역 이름 없음 → username), 13:05:00
                ("555", "에이", "2026-11-19", True),                      # 사람별 .json 13:05:12(같은 날은 시각 순)
                ("556", "def", "2026-11-19", True),                      # 전역 이름 없음 → username
                ("555", "에이", "2026-11-20", True),                      # 세션 이벤트: 바뀜
            ])

            # 보내기: 지난번 뒤로 바뀐 파일만, 성공해야 상태를 쓴다
            state = Path(tmp) / "state.json"
            sent = []
            def call(method, path, body=None):
                sent.append((method, path, len(body["items"]))); return {"created": 2, "updated": 1}
            r = CN.observe_discord(call, tmp, state=state, log=lambda *_: None, today=D("2026-11-25"))
            self.assertEqual(r["files"], 0)                                   # 처음엔 최근 이틀 폴더만(11-24부터)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["lastFolder"], "2026-11-24")
            state.unlink()
            r = CN.observe_discord(call, tmp, state=state, log=lambda *_: None, today=D("2026-11-20"))
            self.assertEqual((r["files"], r["items"], r["created"], r["updated"]), (6, 5, 2, 1))
            self.assertEqual(sent, [("POST", "/api/people/names/observe", 5)])
            self.assertEqual(CN.observe_discord(call, tmp, state=state, log=lambda *_: None)["files"], 0)
            self.assertEqual(CN.observe_discord(call, tmp, state=state, all_files=True, dry_run=True)["items"], 5)
            def broken(method, path, body=None): raise OSError("HTTP Error 404: Not Found")
            before = state.read_text(encoding="utf-8")
            (day / "e.json").write_text(json.dumps({**new, "user_id": "557"}), encoding="utf-8")
            self.assertEqual(CN.observe_discord(broken, tmp, state=state, log=lambda *_: None)["created"], 0)
            self.assertEqual(state.read_text(encoding="utf-8"), before)        # 못 보냈으면 상태를 두고 다음에 다시
            self.assertEqual(CN.observe_discord(call, tmp, state=state, log=lambda *_: None)["files"], 1)


class AliasTests(unittest.TestCase):
    def test_sync_aliases_moves_old_contact_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp))
            e = V.unit(np.ones(V.DIM, np.float32))
            st.add(e, person="CK26 김청아님", source="call", file="call/1")
            st.add(e, person="CK 김청아 누나", source="call", file="call/2")
            st.add(e, person="누나", source="call", file="call/3")
            st.aliases["청아"] = "CK 김청아 누나"            # 사람이 정한 디스코드 별칭은 따라간다
            n = CN.sync_aliases(st, CN.Book(BOOK_ROWS), log=lambda *_: None)
            self.assertEqual(n, 2)
            self.assertEqual([s["person"] for s in st.samples], ["김청아", "김청아", "누나"])
            self.assertEqual(st.canonical("청아"), "김청아")
            self.assertEqual(st.canonical("누나"), "누나")   # 때에 따라 다른 사람인 이름은 별칭으로 만들지 않는다
            self.assertEqual(V.Store(Path(tmp)).aliases["CK26 김청아님"], "김청아")   # 저장됨
            self.assertEqual(CN.sync_aliases(st, CN.Book(BOOK_ROWS), log=lambda *_: None), 0)   # 두 번째는 할 것 없음

    def test_loop_and_me_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp))
            st.aliases["김청아"] = "CK 김청아 누나"          # 반대 방향 별칭: 이미 한 사람이니 그대로(돌고 도는 별칭을 만들지 않음)
            logs = []
            n = CN.sync_aliases(st, CN.Book([row(1, "김청아", "CK 김청아 누나"), row(2, "나", "내 폰")]), save=False, log=logs.append)
            self.assertEqual((n, logs), (0, []))
            self.assertEqual(st.aliases, {"김청아": "CK 김청아 누나"})   # '나'는 별칭으로 만들지 않는다


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        import analyze, bundles as B
        self.A, self.B = analyze, B
        CN.BOOK = CN.Book(BOOK_ROWS)

    def tearDown(self):
        CN.BOOK, CN.DISCORD = CN.Book(), CN.Book(platform=CN.DISCORD_P)

    def bundle(self, name):
        return self.B.Bundle("call", [self.B.Item("call", name, 0, 1000, Path("C:/x") / name)])

    def test_pick_body_uses_person_and_keeps_contact_name(self):
        self.assertEqual(self.A.pick_body(self.bundle("통화 CK 김청아 누나_260915_100000.m4a"), {}), {"caller": "김청아", "people": ["CK 김청아 누나"]})
        self.assertEqual(self.A.pick_body(self.bundle("통화 김철수_260915_100000.m4a"), {}), {"caller": "김철수"})

    def test_assign_speakers_partner_is_person(self):
        segs, partner = self.A.assign_speakers("call", Path("C:/x/통화 CK26 김청아님_260310_100000.m4a"), Path("C:/x/w.wav"), [], {})
        self.assertEqual((segs, partner), ([], "김청아"))
        _, partner = self.A.assign_speakers("call", Path("C:/x/통화 CK26 김청아님_261110_100000.m4a"), Path("C:/x/w.wav"), [], {})
        self.assertEqual(partner, "CK26 김청아님")

    def test_load_call_names_without_server(self):
        def broken(method, path, body=None): raise OSError("offline")
        root = self.A.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            self.A.ROOT = Path(tmp)                       # 실제 녹음 폴더를 읽지 않게
            try:
                self.A.load_call_names(broken)
            finally:
                self.A.ROOT = root
        self.assertEqual(len(CN.BOOK), 0)


if __name__ == "__main__":
    unittest.main()
