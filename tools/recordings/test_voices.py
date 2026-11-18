"""사람 목소리 시험: python -m unittest test_voices (가짜 임베딩, 임시 폴더에서만. 실제 목소리 저장소·녹음은 건드리지 않음)"""
import tempfile, unittest, unittest.mock
from pathlib import Path
import numpy as np
import voices as V
import speakers as sp

RNG = np.random.default_rng(7)


def voice():
    """한 사람의 '진짜' 목소리 방향"""
    return V.unit(RNG.standard_normal(V.DIM))


def near(base, sim):
    """base와 코사인 유사도가 대략 sim인 단위 벡터(직교 잡음을 섞는다)"""
    r = RNG.standard_normal(V.DIM)
    r = V.unit(r - (r @ base) * base)
    return V.unit(sim * base + np.sqrt(1 - sim * sim) * r)


def turns_of(durs: dict) -> list:
    """{묶음번호: 말한 초} → 화자 분리 turns [(시작ms, 끝ms, 묶음번호)]"""
    out, t = [], 0
    for k, sec in durs.items():
        out.append((t, t + int(sec * 1000), k)); t += int(sec * 1000) + 500
    return out


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "voices"

    def tearDown(self): self.tmp.cleanup()

    def test_roundtrip_and_remove(self):
        s = V.Store(self.dir)
        a, b = voice(), voice()
        s.add(near(a, 0.8), person="엄마", source="call", file="call/1.m4a", sec=30)
        s.add(near(a, 0.8), person="엄마", source="call", file="call/2.m4a", sec=20)
        x = s.add(near(b, 0.8), person="아빠", source="call", file="call/3.m4a", sec=40)
        s.save()
        self.assertTrue((self.dir / "voices.npy").exists())
        self.assertEqual((self.dir / "voices.npy").stat().st_size, 128 + 3 * V.DIM * 4)   # 표본 하나 768바이트
        r = V.Store(self.dir)
        self.assertEqual(len(r.samples), 3)
        self.assertEqual(r.emb.dtype, np.float32)
        ppl = r.people()
        self.assertEqual(ppl["엄마"][1], 2)
        self.assertGreater(float(ppl["엄마"][0] @ a), 0.85)   # 평균은 표본 하나보다 진짜 목소리에 가깝다
        self.assertEqual(r.remove_sample(x["id"]), 1)
        self.assertNotIn("아빠", r.people())
        self.assertEqual(r.remove_file("call/1.m4a"), 1)
        self.assertEqual(r.remove_person("엄마"), 1)
        self.assertEqual((len(r.samples), r.emb.shape), (0, (0, V.DIM)))
        r.save()
        self.assertEqual(len(V.Store(self.dir).samples), 0)
        # 새 id는 지운 것과 겹치지 않는다
        self.assertGreater(V.Store(self.dir).add(a, person="x")["id"], x["id"])

    def test_short_npy_is_trimmed(self):
        s = V.Store(self.dir)
        s.add(voice(), person="a"); s.add(voice(), person="b"); s.save()
        np.save(self.dir / "voices.npy", s.emb[:1])   # json 쓰기 전에 끊긴 것처럼
        r = V.Store(self.dir)
        self.assertEqual((len(r.samples), len(r.emb)), (1, 1))

    def test_summary_and_close_people(self):
        s = V.Store(self.dir)
        a = voice()
        for i in range(3): s.add(near(a, 0.85), person="CK 김청아 누나", file=f"c{i}", sec=10)
        for i in range(2): s.add(near(a, 0.85), person="CK26 김청아님", file=f"d{i}", sec=10)
        s.add(near(voice(), 0.85), person="아빠", file="e", sec=10)
        sm = s.summary()
        self.assertEqual(sm[0]["person"], "CK 김청아 누나")
        self.assertEqual((sm[0]["samples"], sm[0]["sec"]), (3, 30.0))
        close = s.close_people()
        self.assertEqual([(a_, b_) for a_, b_, _ in close], [("CK 김청아 누나", "CK26 김청아님")])


class CallTests(unittest.TestCase):
    def setUp(self):
        self.me, self.mom = voice(), voice()
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")

    def test_number_partner_skipped(self):
        self.assertTrue(V.is_number("01050546445"))
        self.assertTrue(V.is_number("053-573-9000"))
        self.assertFalse(V.is_number("CK 김청아 누나"))
        embs = {0: near(self.me, 0.9), 1: near(self.mom, 0.9)}
        s, why = V.enroll_call(self.store, "01050546445", "f", None, embs, turns_of({0: 30, 1: 30}), {0: "나", 1: "01050546445"})
        self.assertIsNone(s)

    def test_one_partner_cluster(self):
        embs = {0: near(self.me, 0.9), 1: near(self.mom, 0.9)}
        got, why = V.call_sample(embs, turns_of({0: 30, 1: 25}), {0: "나", 1: "엄마"})
        self.assertIsNone(why)
        self.assertEqual(got[1], 25)
        self.assertAlmostEqual(float(got[0] @ embs[1]), 1, places=5)

    def test_rules(self):
        names = {0: "나", 1: "엄마", 2: "엄마"}
        # '나'가 없으면 누가 상대인지 모른다
        self.assertIsNone(V.call_sample({1: self.mom}, turns_of({1: 30}), {1: "엄마"})[0])
        # 상대 쪽에 서로 다른 목소리 둘이 다 길게(바꿔 줌·스피커폰)
        other = voice()
        got, why = V.call_sample({0: self.me, 1: near(self.mom, 0.9), 2: near(other, 0.9)}, turns_of({0: 30, 1: 20, 2: 20}), names)
        self.assertIsNone(got); self.assertIn("여럿", why)
        # 둘째 목소리가 짧으면(섞여 든 잡음·내 말 조각) 빼고 큰 무리로 등록
        e1 = near(self.mom, 0.9)
        got, why = V.call_sample({0: self.me, 1: e1, 2: near(other, 0.9)}, turns_of({0: 30, 1: 20, 2: 5}), names)
        self.assertEqual(got[1], 20); self.assertAlmostEqual(float(got[0] @ e1), 1, places=5)
        # 잘게 나뉜 같은 목소리 둘은 합쳐 등록(말한 길이로 가중)
        e1, e2 = near(self.mom, 0.9), near(self.mom, 0.9)
        got, why = V.call_sample({0: self.me, 1: e1, 2: e2}, turns_of({0: 30, 1: 30, 2: 10}), names)
        self.assertEqual(got[1], 40)
        self.assertGreater(float(got[0] @ e1), float(got[0] @ e2))
        # 짧은 묶음(맞장구·잡음)은 빼고, 남은 말이 짧으면 등록 안 함
        got, why = V.call_sample({0: self.me, 1: self.mom, 2: other}, turns_of({0: 30, 1: 8, 2: 2}), names)
        self.assertIsNone(got); self.assertIn("짧음", why)
        # 상대 무리가 이 통화의 '나' 묶음과 닮으면 분리가 애매(실제 통화에서 '나' 조각이 상대 쪽에 섞였을 때 0.69)
        got, why = V.call_sample({0: self.me, 1: near(self.me, 0.7)}, turns_of({0: 30, 1: 30}), {0: "나", 1: "엄마"})
        self.assertIsNone(got); self.assertIn("안 갈림", why)

    def test_enroll_consistency_and_redo(self):
        st, call = self.store, lambda emb, f: V.enroll_call(self.store, "엄마", f, "2026-11-16T10:00:00+09:00",
                                                            {0: near(self.me, 0.9), 1: emb}, turns_of({0: 30, 1: 30}), {0: "나", 1: "엄마"})
        for i in range(V.CHECK_AFTER):
            s, msg = call(near(self.mom, 0.8), f"call/{i}.m4a")
            self.assertIsNotNone(s, msg)
        self.assertEqual(s["at"], "2026-11-16T10:00:00+09:00")
        # 다른 사람이 받은 통화는 기존 평균과 달라 등록 안 함
        s, msg = call(near(voice(), 0.8), "call/x.m4a")
        self.assertIsNone(s); self.assertIn("기존 목소리와 다름", msg)
        # 같은 통화를 다시 전사하면 표본을 바꿔 넣는다(늘지 않음)
        s, msg = call(near(self.mom, 0.8), "call/0.m4a")
        self.assertIsNotNone(s)
        self.assertEqual(len(st.rows("엄마")), V.CHECK_AFTER)


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        self.mom, self.dad, self.friend = voice(), voice(), voice()
        for i in range(3): self.store.add(near(self.mom, 0.95), person="엄마", file=f"m{i}")
        for i in range(3): self.store.add(near(self.dad, 0.95), person="아빠", file=f"d{i}")
        self.store.add(near(self.friend, 0.95), person="친구", file="f0")   # 표본 하나뿐

    def test_sure_guess_none(self):
        ppl = self.store.people()
        self.assertEqual(V.match(ppl, near(self.mom, 0.8)), ("엄마", unittest.mock.ANY, False))
        self.assertTrue(V.match(ppl, near(self.mom, 0.52))[2])            # 중간 → 추정
        self.assertIsNone(V.match(ppl, near(self.mom, 0.2)))               # 낮으면 그대로
        self.assertTrue(V.match(ppl, near(self.friend, 0.95))[2])         # 표본 하나뿐이면 추정까지만
        self.assertIsNone(V.match({}, self.mom))

    def test_name_groups(self):
        stranger = voice()
        # 묶음 0 = 나, 1·2 = 화자1(엄마가 잘게 나뉨), 3 = 화자2(엄마와 조금 닮은 다른 묶음), 4 = 화자3(모르는 사람)
        embs = {0: voice(), 1: near(self.mom, 0.8), 2: near(self.mom, 0.8), 3: near(self.mom, 0.5), 4: near(stranger, 0.9)}
        names = {0: "나", 1: "화자1", 2: "화자1", 3: "화자2", 4: "화자3"}
        turns = turns_of({0: 30, 1: 20, 2: 20, 3: 15, 4: 12})
        out, guessed, unnamed = V.name_groups(embs, turns, names, self.store.people())
        self.assertEqual(out, {0: "나", 1: "엄마", 2: "엄마", 3: "화자2", 4: "화자3"})   # 한 사람엔 더 닮은 화자만
        self.assertEqual(guessed, set())
        self.assertEqual(sorted(unnamed), ["화자2", "화자3"])
        # 추정 이름은 구간 speakerGuess로
        out, guessed, _ = V.name_groups({0: voice(), 1: near(self.dad, 0.5)}, turns_of({0: 10, 1: 10}), {0: "나", 1: "화자1"}, self.store.people())
        self.assertEqual((out[1], guessed), ("아빠", {"아빠"}))
        segs = [{"startMs": 0, "endMs": 5000}, {"startMs": 10600, "endMs": 15000}]
        sp.label_segments(segs, turns_of({0: 10, 1: 10}), out, guessed)
        self.assertEqual([(s["speaker"], s.get("speakerGuess")) for s in segs], [("나", None), ("아빠", True)])
        merged = sp.group_words([{**segs[1], "text": "a"}, {"startMs": 15100, "endMs": 15500, "speaker": "아빠", "speakerGuess": True, "text": "b"}])
        self.assertEqual((len(merged), merged[0]["speakerGuess"]), (1, True))

    def test_short_group_not_named(self):
        out, _, unnamed = V.name_groups({1: near(self.mom, 0.9)}, turns_of({1: 1}), {1: "화자1"}, self.store.people())
        self.assertEqual(out[1], "화자1")


class UnknownTests(unittest.TestCase):
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        self.roommate, self.other = voice(), voice()

    def add(self, file, **voices_):
        return V.add_unknown(self.store, file, None, "laptop-mic", {k: (v, 20.0) for k, v in voices_.items()})

    def test_group_across_recordings(self):
        self.add("laptop-mic/1.m4a", 화자1=near(self.roommate, 0.85), 화자2=near(self.other, 0.85))
        self.add("laptop-mic/2.m4a", 화자1=near(self.roommate, 0.85))
        self.add("laptop-mic/3.m4a", 화자2=near(self.roommate, 0.85))
        g = V.unknown_groups(self.store)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["files"], ["laptop-mic/1.m4a", "laptop-mic/2.m4a", "laptop-mic/3.m4a"])
        self.assertEqual(len(V.unknown_groups(self.store, min_files=1)), 2)
        # 같은 녹음을 다시 처리하면 바꿔 넣는다
        self.add("laptop-mic/1.m4a", 화자1=near(self.roommate, 0.85))
        self.assertEqual(sum(1 for s in self.store.samples if s["file"] == "laptop-mic/1.m4a"), 1)
        # 이름을 정하면 등록된 사람이 된다
        n = V.confirm_unknown(self.store, g[0]["cluster"], "룸메이트")
        self.assertEqual(n, 3)
        self.assertEqual(self.store.people()["룸메이트"][1], 3)
        self.assertEqual(V.unknown_groups(self.store), [])

    def test_same_recording_never_one_cluster(self):
        e = near(self.roommate, 0.95)
        self.add("a", 화자1=e, 화자2=near(self.roommate, 0.95))   # 한 녹음 안 두 화자는 아무리 닮아도 다른 묶음
        self.assertEqual(len({s["cluster"] for s in self.store.samples}), 2)

    def test_short_not_kept_and_recluster(self):
        V.add_unknown(self.store, "a", None, "phone", {"화자1": (self.other, 2.0)})
        self.assertEqual(self.store.samples, [])
        for i in range(3): self.add(f"r{i}", 화자1=near(self.roommate, 0.85))
        for s in self.store.samples: s["cluster"] = None   # 기준을 바꿔 처음부터
        self.assertEqual(V.recluster(self.store), 1)
        self.assertEqual(len(V.unknown_groups(self.store)[0]["files"]), 3)


class ApplyTests(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp) / "voices")
            me = sp.Me(voice())
            embs = {0: near(me.vec, 0.9), 1: voice()}
            names, guessed, msg = sp.apply_voices(st, "call", "call/a.m4a", None, turns_of({0: 30, 1: 30}), embs, {0: "나", 1: "엄마"}, me, "엄마", save=False)
            self.assertIn("엄마 등록", msg)
            self.assertFalse((Path(tmp) / "voices").exists())
            sp.apply_voices(st, "laptop-mic", "laptop-mic/b.m4a", None, turns_of({0: 30, 1: 30}), {0: embs[0], 1: voice()}, {0: "나", 1: "화자1"}, me, save=False)
            self.assertFalse((Path(tmp) / "voices").exists())
            sp.apply_voices(st, "call", "call/a.m4a", None, turns_of({0: 30, 1: 30}), embs, {0: "나", 1: "엄마"}, me, "엄마")
            self.assertEqual(len(V.Store(Path(tmp) / "voices").samples), 1)


class GradeTests(unittest.TestCase):
    """음질 갈래(2026-11-18): 같은 갈래 평균 먼저, 갈래 표본이 없으면 전체 평균으로 추정까지만"""
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        # 청아: 전화 목소리와 디스코드 목소리가 꽤 다르다(같은 사람, 다른 음질)
        self.a_call, self.a_disc, self.b_call = voice(), voice(), voice()
        for i in range(3): self.store.add(near(self.a_call, 0.95), person="청아", source="call", file=f"call/a{i}")
        for i in range(3): self.store.add(near(self.a_disc, 0.95), person="청아", source="discord-bot", file=f"bot/a{i}")
        for i in range(3): self.store.add(near(self.b_call, 0.95), person="아빠", source="call", file=f"call/b{i}")

    def test_grade(self):
        self.assertEqual([V.grade(x) for x in ("call", "discord-bot", "discord-output", "phone", "laptop-mic", None, "etc")],
                         ["call", "discord", "discord", "face", "face", None, "etc"])

    def test_people_by_grade(self):
        ppl = self.store.people(grade="discord")
        self.assertEqual((ppl["청아"][1], ppl["청아"][2]), (3, False))   # 디스코드 표본 3개의 평균
        self.assertGreater(float(ppl["청아"][0] @ self.a_disc), 0.9)
        self.assertEqual((ppl["아빠"][1], ppl["아빠"][2]), (3, True))    # 디스코드 표본이 없어 전체 평균
        self.assertEqual(len(self.store.people()["청아"]), 2)             # grade 없이 부르면 예전 모양 그대로
        self.assertEqual(len(self.store.rows("청아", "discord")), 3)

    def test_match_prefers_same_grade(self):
        e = near(self.a_disc, 0.8)
        m = V.match(self.store.people(grade="discord"), e)
        self.assertEqual((m[0], m[2]), ("청아", False))   # 같은 갈래 평균이면 확정
        m = V.match(self.store.people(), e)   # 전체 평균(전화+디스코드 섞임)이면 덜 닮아 추정
        self.assertTrue(m is None or m[2])
        # 그 갈래 표본이 없는 사람은 아무리 닮아도 추정까지만
        self.assertEqual(V.match(self.store.people(grade="discord"), near(self.b_call, 0.9)), ("아빠", unittest.mock.ANY, True))

    def test_apply_voices_uses_recording_grade(self):
        embs = {1: near(self.a_disc, 0.8)}
        names, guessed, msg = sp.apply_voices(self.store, "discord-output", "discord-output/x.m4a", None, turns_of({1: 20}), embs, {1: "화자1"}, None, save=False)
        self.assertEqual((names[1], guessed), ("청아", set()))
        names, guessed, _ = sp.apply_voices(self.store, "laptop-mic", "laptop-mic/x.m4a", None, turns_of({1: 20}), embs, {1: "화자1"}, None, save=False)
        self.assertNotEqual((names[1], guessed), ("청아", set()))   # 대면 표본이 없으니 확정 이름은 안 붙는다


class AliasTests(unittest.TestCase):
    def test_alias_moves_samples_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp) / "voices")
            a = voice()
            st.add(near(a, 0.9), person="CK 김청아 누나", source="call", file="call/1")
            st.add(near(a, 0.9), person="청아", source="discord-bot", file="bot/1", display="청아")
            self.assertEqual(st.set_alias("청아", "CK 김청아 누나"), 1)
            self.assertEqual(st.people()["CK 김청아 누나"][1], 2)
            self.assertEqual(st.samples[1]["display"], "청아")
            self.assertEqual(st.canonical("청아"), "CK 김청아 누나")
            self.assertEqual(st.canonical("아빠"), "아빠")
            # 사람 이름 쪽을 다시 별칭으로 돌리면 앞 별칭도 따라간다
            st.set_alias("CK 김청아 누나", "김청아")
            self.assertEqual(st.aliases, {"청아": "김청아", "CK 김청아 누나": "김청아"})
            with self.assertRaises(ValueError): st.set_alias("김청아", "청아")   # 돌고 도는 별칭
            st.save()
            r = V.Store(Path(tmp) / "voices")
            self.assertEqual(r.aliases["청아"], "김청아")
            self.assertTrue(r.remove_alias("청아")); self.assertFalse(r.remove_alias("청아"))
            self.assertEqual(r.people()["김청아"][1], 2)   # 옮긴 표본은 그대로

    def test_call_enroll_follows_alias(self):
        st, me, mom = V.Store(Path(tempfile.gettempdir()) / "never-saved"), voice(), voice()
        st.aliases["엄마폰"] = "엄마"
        s, msg = V.enroll_call(st, "엄마폰", "call/1", None, {0: near(me, 0.9), 1: near(mom, 0.9)}, turns_of({0: 30, 1: 30}), {0: "나", 1: "엄마폰"})
        self.assertEqual(s["person"], "엄마", msg)


class BotTests(unittest.TestCase):
    """디스코드 봇 녹음(사람별 파일)으로 목소리 등록"""
    def setUp(self):
        self.store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        self.friend, self.me = voice(), voice()

    def enroll(self, embs, durs, display="민수", file="2026-11-18/a.m4a", me=None):
        return V.enroll_bot(self.store, display, file, "2026-11-18T10:00:00+09:00", embs, turns_of(durs), me)

    def test_enroll_and_rules(self):
        e1, e2 = near(self.friend, 0.9), near(self.friend, 0.9)
        s, msg = self.enroll({0: e1, 1: e2}, {0: 20, 1: 10})   # 잘게 나뉜 한 사람은 합쳐 등록
        self.assertIsNotNone(s, msg)
        self.assertEqual((s["person"], s["source"], s["display"], s["sec"]), ("민수", "discord-bot", "민수", 30.0))
        # 다시 전사하면 바꿔 넣는다
        self.enroll({0: near(self.friend, 0.9)}, {0: 20})
        self.assertEqual(len(self.store.rows("민수")), 1)
        # 말이 짧으면 안 함
        s, msg = self.enroll({0: self.friend}, {0: 8}, file="b.m4a"); self.assertIsNone(s); self.assertIn("짧음", msg)
        # 한 사람 파일에 다른 목소리도 길게(한 마이크에 둘)
        s, msg = self.enroll({0: near(self.friend, 0.9), 1: voice()}, {0: 20, 1: 15}, file="c.m4a"); self.assertIsNone(s); self.assertIn("여럿", msg)
        # 표시 이름 없음
        self.assertIsNone(self.enroll({0: self.friend}, {0: 30}, display=None)[0])

    def test_me_and_alias(self):
        me = sp.Me(self.me)
        s, msg = self.enroll({0: near(self.me, 0.9)}, {0: 30}, display="hyes", me=me)
        self.assertIsNone(s); self.assertIn("'나'", msg)
        self.store.aliases["hyes"] = V.ME_NAME   # 사람이 '나'로 이어 두면 '나' 기준과 덜 닮아도 안 함
        self.assertIsNone(self.enroll({0: voice()}, {0: 30}, display="hyes")[0])
        self.store.aliases["민수짱"] = "김민수"
        s, _ = self.enroll({0: self.friend}, {0: 30}, display="민수짱", me=me)
        self.assertEqual((s["person"], s["display"]), ("김민수", "민수짱"))

    def test_consistency_same_grade_only(self):
        # 전화 표본(다른 음질)이 많아도 디스코드 표본은 막지 않는다
        for i in range(V.CHECK_AFTER): self.store.add(voice(), person="민수", source="call", file=f"call/{i}")
        for i in range(V.CHECK_AFTER):
            s, msg = self.enroll({0: near(self.friend, 0.8)}, {0: 30}, file=f"bot/{i}")
            self.assertIsNotNone(s, msg)
        # 디스코드 표본이 쌓인 뒤 다른 사람 목소리는 막는다
        s, msg = self.enroll({0: voice()}, {0: 30}, file="bot/x")
        self.assertIsNone(s); self.assertIn("기존 목소리와 다름", msg)

    def test_enroll_bot_tracks(self):
        class FakeDiar:
            def __init__(s, out): s.out = out
            def diarize(s, x): return s.out.pop(0)
        friend2 = voice()
        with tempfile.TemporaryDirectory() as tmp:
            st = V.Store(Path(tmp) / "voices")
            d = FakeDiar([(turns_of({0: 30}), {0: near(self.friend, 0.9)}), (turns_of({0: 4}), {0: friend2}), (turns_of({0: 30}), {0: near(self.me, 0.9)})])
            tracks = [("민수", "bot/a", None, np.zeros(10)), ("지은", "bot/b", None, np.zeros(10)), ("hyes", "bot/c", None, np.zeros(10))]
            n, msgs = sp.enroll_bot_tracks(st, d, sp.Me(self.me), tracks, save=False)
            self.assertEqual(n, 1)
            self.assertTrue(msgs[0].startswith("민수 등록")); self.assertIn("짧음", msgs[1]); self.assertIn("'나'", msgs[2])
            self.assertFalse((Path(tmp) / "voices").exists())   # dry-run
            d.out = [(turns_of({0: 30}), {0: near(self.friend, 0.9)})]
            sp.enroll_bot_tracks(st, d, None, tracks[:1])
            self.assertEqual(V.Store(Path(tmp) / "voices").samples[-1]["display"], "민수")


class DiscordOutputTests(unittest.TestCase):
    """discord-output은 내 목소리가 없다: '나' 후보 없이 화자N + 등록 목소리 이름만(analyze.assign_speakers)"""
    def run_assign(self, source):
        import analyze as A
        me, friend = voice(), voice()
        store = V.Store(Path(tempfile.gettempdir()) / "never-saved")
        for i in range(3): store.add(near(friend, 0.95), person="민수", source="discord-bot", file=f"bot/{i}")
        turns = turns_of({0: 20, 1: 20})
        embs = {0: near(me, 0.85), 1: near(friend, 0.8)}   # 묶음 0은 '나' 기준과 아주 닮았다(내 목소리 같은 남의 목소리·되울림)
        class FakeDiar:
            def diarize(s, x): return turns, embs
        state = {"d": FakeDiar(), "me": sp.Me(me), "voices": store, "save": False}
        words = [{"startMs": 1000, "endMs": 2000, "text": "안녕"}, {"startMs": 21000, "endMs": 22000, "text": "반가워"}]
        with unittest.mock.patch.object(sp, "load_wav", lambda p: np.zeros(16000, np.float32)):
            segs, partner = A.assign_speakers(source, Path(f"{source}/2026-11-18/20261118-100000.m4a"), Path("x.wav"), words, state,
                                              rel=f"{source}/x.m4a", at=None)
        return [(s["speaker"], s.get("speakerGuess", False)) for s in segs]

    def test_no_me_in_discord_output(self):
        import analyze as A
        self.assertIn("discord-output", A.SPEAKER_SOURCES)
        self.assertEqual(self.run_assign("discord-output"), [("화자1", False), ("민수", False)])
        out = self.run_assign("laptop-mic")
        self.assertEqual(out[0], ("나", False))   # 대면 녹음은 그대로 '나'를 찾는다
        self.assertEqual(out[1], ("민수", True))   # 대면 표본이 없어 디스코드 평균으로 추정


if __name__ == "__main__":
    unittest.main()
