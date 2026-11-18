"""화자 라벨 반영 시험: python -m unittest test_labels (임시 폴더의 목소리 저장소, 가짜 API·가짜 임베딩. sherpa-onnx·실제 녹음·서버를 쓰지 않음)"""
import json, tempfile, unittest, unittest.mock
from pathlib import Path
import numpy as np
import voices as V
import labels as L

RNG = np.random.default_rng(11)


def vec():
    return V.unit(RNG.standard_normal(V.DIM))


class FakeApi:
    def __init__(self, labels):
        self.labels, self.calls = labels, []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET": return self.labels
        if path.endswith("/applied"): return {"applied": len(body["items"])}
        return {"clusters": len(body["groups"])}

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


class FakeEmbed:
    def __init__(self, emb=None, sec=12.0):
        self.emb, self.sec, self.seen = emb, sec, []

    def __call__(self, label):
        self.seen.append(label["id"])
        if self.sec < V.MIN_TURN_SEC: return None, self.sec, "짧아 표본 안 만듦"
        return self.emb, self.sec, None


def lb(i, **kw):
    return {"id": i, "target": "speaker", "cluster": None, "device": "phone", "file": "2026-11-14/a.m4a",
            "pipeline": {"name": "whisper-cpp", "version": "2", "settingsVersion": 1}, "speaker": "화자1", "seq": None, "person": "민수",
            "kind": "confirm", "ranges": [{"seq": 0, "startMs": 0, "endMs": 8000}], "members": [], "source": "phone",
            "startedAt": "2026-11-14T13:05:00+09:00", "files": [], **kw}


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "voices"
        self.store = V.Store(self.dir)
        self.eval = Path(self.tmp.name) / "label-eval.jsonl"
        p = unittest.mock.patch.object(L, "eval_path", lambda: self.eval)
        p.start(); self.addCleanup(p.stop)

    def tearDown(self): self.tmp.cleanup()

    def unknown(self, file, label, cluster, emb=None, sec=20.0, source="phone"):
        return self.store.add(emb if emb is not None else vec(), person=None, source=source, file=file, at="2026-11-14T10:00:00+09:00",
                              sec=sec, label=label, cluster=cluster)

    def run_sync(self, labels, dry_run=False, embed=None):
        api = FakeApi(labels)
        out = L.sync(api, dry_run=dry_run, log=lambda *_: None, store=self.store, embed=embed or FakeEmbed(vec()), at_exit=False)
        return api, out

    def test_cluster_confirm_when_members_overlap(self):
        self.unknown("a.m4a", "화자1", 3); self.unknown("b.m4a", "화자2", 3)
        api, out = self.run_sync([lb(1, target="cluster", cluster=3, members=[{"device": "phone", "file": "a.m4a", "label": "화자1"},
                                                                                 {"device": "phone", "file": "b.m4a", "label": "화자2"}])])
        self.assertEqual([s["person"] for s in V.Store(self.dir).samples], ["민수", "민수"])
        self.assertIn("표본 2개", out[0][1])
        self.assertEqual(api.writes()[0], ("POST", "/api/recordings/speaker-labels/applied", {"items": [{"id": 1, "note": out[0][1]}]}))

    def test_cluster_skipped_when_renumbered(self):
        self.unknown("x.m4a", "화자1", 3); self.unknown("y.m4a", "화자1", 3)
        _, out = self.run_sync([lb(1, target="cluster", cluster=3, members=[{"device": "phone", "file": "a.m4a", "label": "화자1"},
                                                                               {"device": "phone", "file": "b.m4a", "label": "화자2"}])])
        self.assertIn("묶음이 바뀜", out[0][1])
        self.assertTrue(all(s["person"] is None for s in self.store.samples))

    def test_speaker_label_moves_unknown_sample_without_embedding(self):
        e = vec()
        old = self.unknown("2026-11-14/a.m4a", "화자1", 5, emb=e, sec=33.0)
        embed = FakeEmbed(vec())
        _, out = self.run_sync([lb(2)], embed=embed)
        self.assertEqual(embed.seen, [])   # 표본이 있으면 다시 뽑지 않는다
        s = V.Store(self.dir)
        self.assertEqual(len(s.samples), 1)
        self.assertEqual((s.samples[0]["person"], s.samples[0]["confirmed"], s.samples[0]["labelId"], s.samples[0]["sec"]), ("민수", True, 2, 33.0))
        self.assertNotIn("cluster", s.samples[0])
        self.assertAlmostEqual(float(s.emb[0] @ e), 1.0, places=5)
        self.assertIn(f"표본 바꿈 #{old['id']}", out[0][1])

    def test_short_reembed_makes_no_sample(self):
        _, out = self.run_sync([lb(3, target="segment", seq=4, speaker="아빠")], embed=FakeEmbed(None, sec=1.2))
        self.assertEqual(self.store.samples, [])
        self.assertIn("짧아", out[0][1])

    def test_guess_right_and_wrong_write_eval(self):
        dad = vec()
        for _ in range(2): self.store.add(dad, person="아빠", source="call", file="call/x.m4a")
        _, out = self.run_sync([lb(4, speaker="아빠", person=None, kind="guess_right"),
                                lb(5, file="2026-11-14/b.m4a", speaker="아빠", person=None, kind="guess_wrong")], embed=FakeEmbed(dad, sec=9.0))
        rows = [json.loads(x) for x in self.eval.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["labelId"], r["kind"], r["guessed"], r["person"]) for r in rows], [(4, "guess_right", "아빠", "아빠"), (5, "guess_wrong", "아빠", None)])
        self.assertAlmostEqual(rows[0]["sim"], 1.0, places=3)
        self.assertEqual(rows[0]["top"][0][0], "아빠")
        s = V.Store(self.dir)
        self.assertEqual(len(s.rows("아빠")), 3)   # 맞음은 표본 하나 더, 틀림(이름 없음)은 평가만
        self.assertIn("평가 기록만", out[1][1])

    def test_dry_run_writes_nothing(self):
        self.unknown("2026-11-14/a.m4a", "화자1", 5)
        self.unknown("a.m4a", "화자1", 3); self.unknown("b.m4a", "화자2", 3)
        api, out = self.run_sync([lb(6), lb(7, speaker="아빠", person=None, kind="guess_right")], dry_run=True)
        self.assertEqual(api.writes(), [])
        self.assertFalse(self.dir.exists())
        self.assertFalse(self.eval.exists())
        self.assertEqual(len(out), 2)

    def test_push_clusters_has_no_embeddings(self):
        self.unknown("a.m4a", "화자1", 3, sec=7.5); self.unknown("laptop-mic/2026-11-14/b.m4a", "화자2", 3, source="laptop-mic")
        self.unknown("c.m4a", "화자1", 9)   # 녹음 하나뿐인 묶음은 빠진다
        api, _ = self.run_sync([])
        put = [c for c in api.writes() if c[0] == "PUT"]
        self.assertEqual(len(put), 1)
        body = put[0][2]
        self.assertEqual(put[0][1], "/api/recordings/speaker-clusters")
        self.assertEqual([g["cluster"] for g in body["groups"]], [3])
        ms = body["groups"][0]["members"]
        self.assertEqual({(m["device"], m["file"], m["label"]) for m in ms}, {("phone", "a.m4a", "화자1"), ("laptop", "laptop-mic/2026-11-14/b.m4a", "화자2")})
        self.assertEqual(set(ms[0]), {"device", "file", "label", "sec", "source", "at"})
        text = json.dumps(body, ensure_ascii=False)
        self.assertLess(len(text), 600)   # 192차원 벡터가 들어가면 훨씬 길다
        self.assertEqual([c for c in api.writes() if c[0] == "POST"], [])   # 라벨이 없으면 반영 표시 안 함

    def test_server_error_does_not_raise(self):
        def bad(*_): raise OSError("404")
        self.assertEqual(L.sync(bad, log=lambda *_: None, store=self.store, at_exit=False), [])

    def test_pieces_split_by_member_offsets(self):
        x = lb(8, ranges=[{"seq": 0, "startMs": 1000, "endMs": 3000}, {"seq": 1, "startMs": 19000, "endMs": 42000}],
               files=[{"file": "a.m4a", "startedAt": "2026-11-14T12:00:00+09:00", "endedAt": "2026-11-14T12:00:20+09:00", "offsetMs": 0},
                      {"file": "b.m4a", "startedAt": "2026-11-14T12:00:40+09:00", "endedAt": "2026-11-14T12:01:00+09:00", "offsetMs": 40000}])
        self.assertEqual(L.pieces(x), [("a.m4a", 1000, 3000), ("a.m4a", 19000, 20000), ("b.m4a", 0, 2000)])
        self.assertEqual(L.local_path("call", "call/2026-11-14/통화 a.m4a"), L.ROOT / "call" / "2026-11-14/통화 a.m4a")


class AliasStore(V.Store):
    """별칭이 있는 저장소(claude/discord-speakers의 Store.canonical 흉내)"""
    aliases = {"아버지": "아빠", "민수형": "민수"}

    def canonical(self, name):
        return self.aliases.get(name, name)


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "voices"
        self.eval = Path(self.tmp.name) / "label-eval.jsonl"
        p = unittest.mock.patch.object(L, "eval_path", lambda: self.eval)
        p.start(); self.addCleanup(p.stop)

    def tearDown(self): self.tmp.cleanup()

    def sync(self, store, labels, emb=None):
        return L.sync(FakeApi(labels), log=lambda *_: None, store=store, embed=FakeEmbed(emb if emb is not None else vec()), at_exit=False)

    def test_canonical_used_for_samples_cluster_and_eval(self):
        s = AliasStore(self.dir)
        dad = vec()
        for _ in range(2): s.add(dad, person="아빠", source="call", file="call/x.m4a")
        for f in ("a.m4a", "b.m4a"): s.add(vec(), person=None, source="phone", file=f, label="화자1", cluster=3, sec=9.0)
        with unittest.mock.patch.object(V, "grade", lambda src: {"call": "전화", "discord-output": "디스코드"}.get(src, "대면"), create=True):
            self.sync(s, [lb(1, target="cluster", cluster=3, person="민수형", members=[{"device": "phone", "file": "a.m4a", "label": "화자1"}]),
                          lb(2, file="c.m4a", speaker="아버지", person=None, kind="guess_right")], emb=dad)
        got = V.Store(self.dir)
        self.assertEqual(len(got.rows("민수")), 2)
        self.assertEqual(len(got.rows("아빠")), 3)
        self.assertEqual(got.rows("아버지") + got.rows("민수형"), [])
        r = json.loads(self.eval.read_text(encoding="utf-8"))
        self.assertEqual((r["guessed"], r["guessedRaw"], r["person"], r["personRaw"], r["grade"]), ("아빠", "아버지", "아빠", "아버지", "대면"))
        self.assertAlmostEqual(r["sim"], 1.0, places=3)

    def test_without_canonical_or_grade_names_stay(self):
        s = V.Store(self.dir)
        self.assertFalse(hasattr(s, "canonical"))
        saved = V.__dict__.get("grade")   # 갈래가 있는 voices.py(병합 뒤)에서도 없는 경우를 본다
        if saved is not None: delattr(V, "grade"); self.addCleanup(setattr, V, "grade", saved)
        self.sync(s, [lb(3, file="c.m4a", speaker="아버지", person=None, kind="guess_right")])
        r = json.loads(self.eval.read_text(encoding="utf-8"))
        self.assertEqual((r["guessed"], r["guessedRaw"], r["person"]), ("아버지", "아버지", "아버지"))
        self.assertNotIn("grade", r)
        self.assertEqual(len(V.Store(self.dir).rows("아버지")), 1)

    def test_people_values_with_extra_columns(self):
        m = vec()
        self.assertEqual(L._top({"아빠": (m, 2, 0.9)}, m)[0][0], "아빠")

    def test_device_for_discord_output_and_unknown_source(self):
        s = V.Store(self.dir)
        for f, src in (("d/a.m4a", "discord-output"), ("e/b.m4a", "new-source")):
            s.add(vec(), person=None, source=src, file=f, label="화자1", cluster=4, sec=8.0)
        logs = []
        body = L.cluster_body(s, logs.append)
        self.assertEqual([m["device"] for m in body["groups"][0]["members"]], ["laptop", "laptop"])
        self.assertTrue(any("new-source" in x for x in logs))


if __name__ == "__main__":
    unittest.main()
