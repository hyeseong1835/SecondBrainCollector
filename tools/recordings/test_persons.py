"""사람 id 시험: python -m unittest test_persons (가짜 API·가짜 임베딩·임시 폴더만. 서버·실제 목소리 저장소·녹음 폴더는 건드리지 않음)
사람 표 옛 이름 → 목소리 별칭('나' 막기·서버 실패), 목소리 동명이인 신호(voices.voice_checks), 올리는 본문 모양."""
import contextlib, io, json, tempfile, unittest, unittest.mock
from pathlib import Path
import numpy as np
import voices as V
import persons as PS
import callnames as CN

RNG = np.random.default_rng(19)


def voice():
    return V.unit(RNG.standard_normal(V.DIM))


def near(base, sim):
    """base와 코사인 유사도가 대략 sim인 단위 벡터(test_voices.near와 같다)"""
    r = RNG.standard_normal(V.DIM)
    r = V.unit(r - (r @ base) * base)
    return V.unit(sim * base + np.sqrt(1 - sim * sim) * r)


DIR = {"me": {"id": 1, "name": "나"}, "people": [
    {"id": 2, "name": "김청아", "formerNames": ["CK 김청아 누나", "청아", "나", "김청아", " 김 청아"]},   # '나'·같은 이름(공백 무시)은 뺀다
    {"id": 3, "name": "민수 (CK)", "formerNames": ["민수"]},          # 옛 이름 "민수"를 지금 다른 사람(4)이 label로 쓴다 → 뺀다
    {"id": 4, "name": "민수", "formerNames": []},
    {"id": 5, "name": "지은 (YJ)", "formerNames": ["지은"]},          # 나눈 동명이인: 두 사람의 옛 이름 → 뺀다
    {"id": 6, "name": "지은 (CK)", "formerNames": ["지은"]},
    {"id": 7, "name": "혜성", "formerNames": ["hyes"]},               # 저장소에서 사람이 '나'로 이어 둔 이름 → 건드리지 않음
    {"id": 8, "name": "아빠", "formerNames": ["아버지"]},
    {"id": "x", "name": "글자 id"}, {"id": 9}, "깨진 줄",               # 모양이 어긋난 줄은 건너뜀
]}


class FakeApi:
    def __init__(self, directory=None, fail=None):
        self.directory, self.fail, self.calls = directory, fail, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail: raise OSError(self.fail)
        if path == "/api/persons/directory": return self.directory
        if path == "/api/people/names": return []
        if path == "/api/persons/voice-checks": return {"count": len(body["items"])}
        raise AssertionError(path)

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class DirectoryTests(unittest.TestCase):
    def tearDown(self):
        PS.DIRECTORY = None

    def test_former_pairs_rules(self):
        d = PS.Directory(DIR)
        self.assertEqual(len(d), 7)
        self.assertEqual(d.former_pairs(), [("CK 김청아 누나", "김청아"), ("청아", "김청아"), ("hyes", "혜성"), ("아버지", "아빠")])
        self.assertEqual((d.id_of("민수(ck)"), d.id_of("나"), d.id_of("모르는 사람")), (3, 1, None))   # label은 공백·대소문자 무시

    def test_sync_moves_old_name_samples_and_guards_me(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp))
            a, b, me = voice(), voice(), voice()
            st.add(near(a, 0.9), person="CK 김청아 누나", source="call", file="call/1")
            st.add(near(a, 0.9), person="청아", source="discord-bot", file="bot/1", display="청아")
            st.add(near(b, 0.9), person="민수", source="call", file="call/2")
            st.add(near(b, 0.9), person="지은", source="call", file="call/3")
            st.add(near(me, 0.9), person="나", source="call", file="call/4")      # '나' 표본(있으면)은 그대로
            st.aliases["hyes"] = V.ME_NAME
            st.aliases["아빠"] = "아버지"                                          # 반대 방향(예전에 사람이 정함) → 뒤집는다
            st.add(near(b, 0.9), person="아버지", source="call", file="call/5")
            n = PS.sync_aliases(st, PS.Directory(DIR), log=lambda *_: None)
            self.assertEqual(n, 3)
            self.assertEqual([s["person"] for s in st.samples], ["김청아", "김청아", "민수", "지은", "나", "아빠"])
            self.assertEqual(st.samples[1]["display"], "청아")
            self.assertEqual(st.aliases, {"hyes": "나", "CK 김청아 누나": "김청아", "청아": "김청아", "아버지": "아빠"})
            self.assertEqual(st.canonical("나"), "나")
            self.assertEqual(V.Store(Path(tmp)).aliases, st.aliases)                 # 저장됨
            self.assertEqual(PS.sync_aliases(st, PS.Directory(DIR), log=lambda *_: None), 0)   # 두 번째는 할 것 없음

    def test_former_name_already_alias_of_other_person(self):
        st = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        st.aliases["청아"] = "민수"                 # 이미 다른 사람(지금 label)의 별칭: 덮어쓰지 않는다
        logs = []
        PS.sync_aliases(st, PS.Directory(DIR), save=False, log=logs.append)
        self.assertEqual(st.canonical("청아"), "민수")
        self.assertTrue(any("이미 민수의 별칭" in m for m in logs))
        self.assertEqual(st.canonical("CK 김청아 누나"), "김청아")

    def test_server_failure_does_nothing(self):
        st = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        st.add(voice(), person="CK 김청아 누나", source="call", file="call/1")
        for err, word in [("HTTP Error 404: Not Found", "옛 서버"), ("offline", "못 읽음")]:
            logs = []
            self.assertIsNone(PS.load(FakeApi(fail=err), log=logs.append))
            self.assertIsNone(PS.DIRECTORY)
            self.assertIn(word, logs[-1])
            self.assertEqual(PS.sync_aliases(st, log=lambda *_: None), 0)
            self.assertEqual((st.aliases, st.samples[0]["person"]), ({}, "CK 김청아 누나"))
        PS.load(FakeApi({"unexpected": True}), log=lambda *_: None)            # 모양이 어긋나면 빈 사람 표
        self.assertEqual((len(PS.DIRECTORY), PS.DIRECTORY.former_pairs()), (0, []))

    def test_load_keeps_directory(self):
        api = FakeApi(DIR)
        d = PS.load(api, log=lambda *_: None)
        self.assertIs(PS.DIRECTORY, d)
        self.assertEqual(api.calls, [("GET", "/api/persons/directory", None)])


class MeGuardTests(unittest.TestCase):
    """'나'는 어떤 별칭 맞추기로도 다른 이름에 접히지 않는다"""
    def test_set_alias_refuses_me(self):
        st = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        st.add(voice(), person="나", file="x")
        with self.assertRaises(ValueError): st.set_alias("나", "김청아")
        self.assertEqual((st.aliases, st.samples[0]["person"]), ({}, "나"))
        self.assertEqual(st.set_alias("hyes", "나"), 0)                         # 이름 → '나'는 사람이 정할 때(speakers.py alias)만
        self.assertEqual(st.canonical("hyes"), "나")

    def test_stale_me_alias_is_dropped_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "voices.json").write_text(json.dumps({"version": 1, "aliases": {"나": "김청아", "청아": "김청아"}, "samples": []}), encoding="utf-8")
            st = V.Store(Path(tmp))
            self.assertEqual(st.aliases, {"청아": "김청아"})
            self.assertEqual(st.canonical("나"), "나")
            st.aliases["나"] = "김청아"                                          # 누가 직접 넣어도 canonical은 '나'
            self.assertEqual(st.canonical("나"), "나")

    def test_callnames_sync_skips_me_both_ways(self):
        st = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        st.aliases["내 디코"] = "나"
        rows = [{"id": 1, "person": "김청아", "name": "나", "from": None, "to": None},          # 이름이 '나'
                {"id": 2, "person": "나", "name": "내 폰", "from": None, "to": None},            # 사람이 '나'
                {"id": 3, "person": "김민수", "name": "내 디코", "from": None, "to": None}]       # 사람이 '나'로 이어 둔 이름
        logs = []
        self.assertEqual(CN.sync_aliases(st, CN.Book(rows), save=False, log=logs.append, discord=CN.Book(platform=CN.DISCORD_P)), 0)
        self.assertEqual((st.aliases, logs), ({"내 디코": "나"}, []))


class VoiceCheckTests(unittest.TestCase):
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")

    def add(self, person, base, n, prefix, sim=0.9, sec=30, source="call"):
        for i in range(n):
            self.store.add(near(base, sim), person=person, source=source, file=f"{prefix}{i}.m4a", sec=sec)

    def test_two_voices_under_one_name_flagged(self):
        a, b = voice(), voice()
        self.add("김민수", a, 3, "call/a", sec=30)
        self.add("김민수", b, 2, "call/b", sec=20)
        [it] = V.voice_checks(self.store)
        self.assertEqual((it["person"], it["samples"], it["files"]), ("김민수", 5, 5))
        self.assertLess(it["similarity"], V.VOICE_CHECK_APART)
        self.assertEqual(it["groups"], [{"files": ["call/a0.m4a", "call/a1.m4a", "call/a2.m4a"], "sec": 90.0},
                                        {"files": ["call/b0.m4a", "call/b1.m4a"], "sec": 40.0}])   # 말 많은 무리부터

    def test_one_voice_not_flagged(self):
        a = voice()
        self.add("엄마", a, 5, "call/m", sim=0.85)
        self.add("아빠", voice(), 6, "call/d", sim=0.7)   # 같은 사람인데 표본끼리 0.5 안팎(잡음 많음): 나머지 평균과는 닮아 안 걸림
        self.assertEqual(V.voice_checks(self.store), [])

    def test_me_and_auto_names_never_flagged(self):
        for name in ("나", "화자1"):
            self.add(name, voice(), 2, f"call/{name}a", sec=30)
            self.add(name, voice(), 2, f"call/{name}b", sec=30)
        self.assertEqual(V.voice_checks(self.store), [])

    def test_short_or_single_file_or_cross_grade_not_flagged(self):
        a = voice()
        self.add("짧음", a, 3, "call/s", sec=30)
        self.add("짧음", voice(), 1, "call/t", sec=V.VOICE_CHECK_MIN_SEC - 1)          # 다른 목소리지만 말이 짧다
        self.store.add(near(a, 0.9), person="한 녹음", source="call", file="call/one.m4a", sec=30)
        self.store.add(voice(), person="한 녹음", source="call", file="call/one.m4a", sec=30)   # 녹음이 하나뿐
        self.add("갈래", a, 2, "call/g", sec=30)
        self.add("갈래", voice(), 2, "discord-bot/g", sec=30, source="discord-bot")    # 전화 ↔ 디스코드는 견주지 않는다(같은 사람도 낮게 나옴)
        self.assertEqual(V.voice_checks(self.store), [])

    def test_short_noise_sample_left_out(self):
        a, b = voice(), voice()
        self.add("x", a, 4, "call/a", sec=30)
        self.add("x", b, 2, "call/b", sec=20)
        self.store.add(voice(), person="x", source="call", file="call/n.m4a", sec=3)   # 짧은 잡음 표본은 어느 무리에도 안 들어가도 된다
        [it] = V.voice_checks(self.store)
        self.assertEqual([len(g["files"]) for g in it["groups"]], [4, 2])

    def test_link_matches_voice_groups(self):
        for _ in range(10):
            vs = [voice() for _ in range(3)]
            e = np.array([near(vs[i % 3], RNG.uniform(0.5, 0.95)) for i in range(9)])
            mine = sorted(sorted(g) for g in V.link(e @ e.T))
            ref = sorted(sorted(g) for g in V.voice_groups({i: e[i] for i in range(9)}, list(range(9))))
            self.assertEqual(mine, ref)
        self.assertEqual((V.link(np.zeros((0, 0))), V.link(np.ones((1, 1)))), ([], [[0]]))


class PushTests(unittest.TestCase):
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        a, b = voice(), voice()
        for i in range(2): self.store.add(near(a, 0.9), person="민수 (CK)", source="call", file=f"call/a{i}", sec=30)
        for i in range(2): self.store.add(near(b, 0.9), person="민수 (CK)", source="call", file=f"call/b{i}", sec=30)
        for i in range(2): self.store.add(near(a, 0.9), person="연락처에만", source="call", file=f"call/c{i}", sec=30)
        for i in range(2): self.store.add(near(b, 0.9), person="연락처에만", source="call", file=f"call/d{i}", sec=30)

    def tearDown(self):
        PS.DIRECTORY = None

    def test_payload_shape(self):
        api = FakeApi(DIR)
        PS.load(api, log=lambda *_: None)
        body = PS.push_voice_checks(api, self.store, log=lambda *_: None)
        [(method, path, sent)] = api.posts()
        self.assertEqual((method, path), ("POST", "/api/persons/voice-checks"))
        self.assertIs(sent, body)
        by = {it["person"]: it for it in sent["items"]}
        self.assertEqual(list(by["민수 (CK)"]), ["person", "personId", "samples", "files", "similarity", "groups"])
        self.assertEqual(by["민수 (CK)"]["personId"], 3)
        self.assertNotIn("personId", by["연락처에만"])                          # 사람 표에 없는 이름은 label 글자만
        self.assertEqual(set(by["연락처에만"]["groups"][0]), {"files", "sec"})
        self.assertIsInstance(by["연락처에만"]["similarity"], float)
        json.dumps(sent)                                                      # 그대로 JSON이 된다(numpy 값 없음)

    def test_empty_list_clears_and_dry_run_does_not_send(self):
        api = FakeApi(DIR)
        empty = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        PS.push_voice_checks(api, empty, log=lambda *_: None)
        self.assertEqual(api.posts(), [("POST", "/api/persons/voice-checks", {"items": []})])   # 걸린 게 없어도 보낸다(지난 신호 지우기)
        logs = []
        body = PS.push_voice_checks(api, self.store, dry_run=True, log=logs.append)
        self.assertEqual((len(api.posts()), len(body["items"])), (1, 2))
        self.assertIn("(dry-run)", logs[0])

    def test_at_exit_once_and_failures_are_swallowed(self):
        with unittest.mock.patch.object(PS.atexit, "register") as reg, unittest.mock.patch.object(PS, "_AT_EXIT", False):
            PS.push_at_exit(FakeApi(DIR)); PS.push_at_exit(FakeApi(DIR))
            self.assertEqual(reg.call_count, 1)
        import speakers as sp
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(sp, "VOICES", Path(tmp)):
            self.store.folder = Path(tmp); self.store.save()
            api = FakeApi(DIR)
            PS._push_at_exit(api, log=lambda *_: None)                         # 사람 표를 못 읽은 실행: 올리지 않음
            self.assertEqual(api.calls, [])
            PS.load(api, log=lambda *_: None)
            PS._push_at_exit(api, log=lambda *_: None)
            self.assertEqual(len(api.posts()[0][2]["items"]), 2)               # 저장소를 새로 읽어 올림
            logs = []
            PS._push_at_exit(FakeApi(fail="HTTP Error 500"), log=logs.append)  # 실패해도 예외 없이 경고만
            self.assertIn("건너뜀", logs[-1])

    def test_cli_counts_without_server_and_pushes(self):
        import speakers as sp
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            items = sp.voice_checks(store=self.store)
        self.assertEqual(len(items), 2)
        self.assertIn("목소리 동명이인 신호 2개", out.getvalue())
        api = FakeApi(DIR)
        with contextlib.redirect_stdout(io.StringIO()):
            sp.voice_checks(as_json=True, push=True, store=self.store, call=api)
        self.assertEqual([c[1] for c in api.calls], ["/api/persons/directory", "/api/persons/voice-checks"])
        old = FakeApi(fail="HTTP Error 404: Not Found")
        with contextlib.redirect_stdout(io.StringIO()):
            sp.voice_checks(push=True, store=self.store, call=old)          # 옛 서버: 올리지 않음(예외 없음)
        self.assertEqual([c[1] for c in old.calls], ["/api/persons/directory"])


class AnalyzeTests(unittest.TestCase):
    """analyze.load_call_names: 사람 표 별칭을 이름 구간보다 먼저, 이름 구간이 비어도"""
    def tearDown(self):
        PS.DIRECTORY = None
        CN.BOOK, CN.DISCORD = CN.Book(), CN.Book(platform=CN.DISCORD_P)

    def test_load_call_names_applies_directory(self):
        import analyze as A, speakers as sp
        with tempfile.TemporaryDirectory() as tmp:
            voices_dir = Path(tmp) / "voices"
            st = V.Store(voices_dir)
            st.add(voice(), person="CK 김청아 누나", source="call", file="call/1")
            st.save()
            with unittest.mock.patch.object(A, "ROOT", Path(tmp)), unittest.mock.patch.object(sp, "VOICES", voices_dir), \
                    unittest.mock.patch.object(CN, "_state_path", lambda: Path(tmp) / "discord-names.json"), contextlib.redirect_stdout(io.StringIO()):
                A.load_call_names(FakeApi(DIR))
            self.assertEqual(V.Store(voices_dir).samples[0]["person"], "김청아")
            self.assertEqual(PS.DIRECTORY.id_of("김청아"), 2)                   # 이 실행 동안 둔다(끝에 올릴 때 personId)
            st.save()                                                          # 되돌려 두고 dry-run(저장 안 함)
            with unittest.mock.patch.object(A, "ROOT", Path(tmp)), unittest.mock.patch.object(sp, "VOICES", voices_dir), \
                    unittest.mock.patch.object(CN, "_state_path", lambda: Path(tmp) / "discord-names.json"), contextlib.redirect_stdout(io.StringIO()):
                A.load_call_names(FakeApi(DIR), save=False)
            self.assertEqual(V.Store(voices_dir).samples[0]["person"], "CK 김청아 누나")

    def test_plan_does_not_read_directory(self):
        import analyze as A
        api = FakeApi(DIR)
        with contextlib.redirect_stdout(io.StringIO()):
            A.load_call_names(api, sync=False)
        self.assertEqual([c[1] for c in api.calls], ["/api/people/names"])
        self.assertIsNone(PS.DIRECTORY)


if __name__ == "__main__":
    unittest.main()
