"""통화 이름 기록 시험: python -m unittest test_callnames (가짜 API·임시 폴더만. 서버·실제 목소리 저장소는 건드리지 않음)
서버 CallNameBook(SecondBrainApi tests CallNameRuleTests)과 같은 경우를 같은 답으로 본다."""
import datetime as dt, tempfile, unittest
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
        CN.BOOK = CN.Book()

    def test_load_and_fail(self):
        calls, logs = [], []
        def call(method, path, body=None):
            calls.append((method, path)); return BOOK_ROWS
        CN.load(call, log=logs.append)
        self.assertEqual(calls, [("GET", "/api/people/call-names")])
        self.assertEqual(len(CN.BOOK), 4)
        self.assertEqual(CN.partner(Path("통화 CK 김청아 누나_260915_100000.m4a")), "김청아")
        def broken(method, path, body=None): raise OSError("404")
        CN.load(broken, log=logs.append)
        self.assertEqual(len(CN.BOOK), 0)                                   # 옛 서버·오프라인: 이름 그대로
        self.assertEqual(CN.partner(Path("통화 CK 김청아 누나_260915_100000.m4a")), "CK 김청아 누나")
        self.assertIn("못 읽음", logs[-1])


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
        CN.BOOK = CN.Book()

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
        self.A.load_call_names(broken)
        self.assertEqual(len(CN.BOOK), 0)


if __name__ == "__main__":
    unittest.main()
