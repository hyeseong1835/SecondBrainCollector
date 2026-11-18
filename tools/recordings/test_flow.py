"""python -m unittest test_flow — analyze.py 겹치기 실행(2026-11-18): 무리 나누기·차례·겹침·실패 격리·취소 정리(가짜 파이프라인, 서버·전사 없음)"""
import argparse, contextlib, io, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parent))
import analyze as A
import bundles as B
import eta as E
import pipelines as P

T0 = 1_800_000_000_000


def bundles(d: Path, n: int, files: int = 1, sec: int = 20, source: str = "phone") -> list:
    out = []
    for i in range(n):
        items = [B.Item(source, f"{i}-{k}.m4a", T0 + i * 1_000_000 + k * 30_000, T0 + i * 1_000_000 + k * 30_000 + sec * 1000, d / f"{i}-{k}.m4a") for k in range(files)]
        out.append(B.Bundle(source, items))
    return out


class GroupTests(unittest.TestCase):
    def test_make_groups(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = bundles(Path(d), 5, files=1)
            audio = [[20], [20], [100], [2000], [20]]   # 조각 추정 1, 1, 4, 67, 1
            with mock.patch.object(A, "GROUP_CHUNKS", 6):
                self.assertEqual(A.make_groups(jobs, audio), [[0, 1, 2], [3], [4]])   # 큰 묶음은 혼자(나누지 않음)
            with mock.patch.object(A, "GROUP_FILES", 2):
                self.assertEqual(A.make_groups(jobs, audio), [[0, 1], [2], [3], [4]])
        self.assertEqual([A.est_chunks(x) for x in (0, 1, 30, 31, 95)], [1, 1, 1, 2, 4])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.pipe = P.Pipeline("fake", "1", "", False, self.d / "run.py", {}, self.d, multi=True)
        self.calls, self.finished, self.log = [], [], []
        self.lock = threading.Lock()
        self.many = lambda items: [([{"startMs": 0, "endMs": 1, "speaker": None, "text": Path(it["wav"]).parent.name}], set(), {}) for it in items]
        self.finish = lambda b, wavs, parts, llm, spk, min_chars, announce=True, diar=None: None
        self.diarize = None   # 화자 분리 갈래(없으면 끔)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_many(self, p, settings, items, tmp, tools=None, on_progress=None, run_options=None, tag="g"):
        with self.lock: self.calls.append((tag, [Path(it["wav"]).name for it in items], [it["words"] for it in items]))
        return self.many(items)

    def fake_finish(self, b, wavs, parts, llm, spk, min_chars, announce=True, diar=None):
        self.finish(b, wavs, parts, llm, spk, min_chars, announce, diar)
        with self.lock: self.finished.append(b.items[0].rel)
        assert all(w.exists() for w in wavs), "wav는 마무리가 끝날 때까지 남는다"
        return {"segments": [s for p in parts for s in p], "speechMs": 1, "transcript": "x", "summary": None}

    def run_jobs(self, jobs, stage="transcribe", group_chunks=None, to_wav=None):
        args = argparse.Namespace(plan=False, dry_run=True, stage=stage, no_auto_remove=True, min_summary_chars=60)
        real = E.Flow
        out = io.StringIO()

        def make_wav(src, dst):
            dst.write_bytes(b"RIFF"); return 20_000
        to_wav = to_wav or make_wav
        patches = [mock.patch.object(A, "CHOSEN", A.Chosen(self.pipe, 1, {}, {})), mock.patch.object(A, "ETA", None),
                   mock.patch.object(A.E, "Flow", lambda plan, k, groups: real(plan, k, groups, history=[], save=False)),
                   mock.patch.object(A.runlock, "set_plan", lambda keys: None), mock.patch.object(A.P, "run_many", self.fake_many),
                   mock.patch.object(A, "to_wav", to_wav), mock.patch.object(A, "finish_bundle", self.fake_finish),
                   mock.patch.object(A, "bundle_hints", lambda call, b, tmp: None), mock.patch.object(A, "Llm", lambda: mock.Mock()),
                   mock.patch.object(A, "speakers_ready", lambda: self.diarize is not None),
                   mock.patch.object(A, "make_diar_pool", lambda: __import__("concurrent.futures").futures.ThreadPoolExecutor(2)),
                   mock.patch.object(A, "diar_fn", lambda: self.diarize)]
        if group_chunks: patches.append(mock.patch.object(A, "GROUP_CHUNKS", group_chunks))
        with contextlib.ExitStack() as st:
            for p in patches: st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            A.run_jobs(args, lambda *x: None, jobs, 0, 0)
        return out.getvalue()

    def test_one_call_per_group_and_order(self):
        jobs = bundles(self.d, 5, files=2) + bundles(self.d, 1, files=1, source="discord-bot")
        jobs[-1].items[0] = B.Item("discord-bot", "bot.m4a", T0 + 9_000_000, T0 + 9_020_000, self.d / "bot.m4a", "u1", "철수")
        out = self.run_jobs(jobs, group_chunks=4)   # 묶음마다 조각 2개 → 무리마다 묶음 둘
        self.assertEqual([c[0] for c in self.calls], ["g0", "g1", "g2"])
        self.assertEqual(self.calls[0][1], ["p0.wav", "p1.wav", "p0.wav", "p1.wav"])
        self.assertEqual(self.calls[2][2], [True, True, False])   # 화자 구분 출처는 단어 단위, 디스코드 봇은 아님
        self.assertEqual(self.finished, [b.items[0].rel for b in jobs])   # 마무리는 묶음 차례대로
        self.assertIn("끝: 묶음 6개 완료, 실패 0개", out)

    def test_overlap_post_with_next_transcribe(self):
        """무리 0을 마무리하는 동안 무리 1이 전사된다(겹치기). --stage all이면 겹치지 않는다"""
        for stage, want in (("transcribe", True), ("all", False)):
            with self.subTest(stage=stage):
                self.calls.clear(); self.finished.clear()
                started, seen = threading.Event(), []
                many0 = self.many

                def many(items, many0=many0):
                    if self.calls[-1][0] == "g1": started.set()
                    return many0(items)
                self.many = many

                def finish(b, *a):
                    if b.items[0].rel.startswith("0-"): seen.append(started.wait(2 if want else 0.5))
                self.finish = finish
                self.run_jobs(bundles(self.d, 2, files=2), stage=stage, group_chunks=2)
                self.assertEqual(seen, [want])
                self.many = many0

    def test_diarize_runs_beside_transcription(self):
        """화자 분리는 준비가 끝나면 갈래에서(전사를 기다리지 않고), 마무리는 그 결과를 받는다. 단어 단위가 아닌 출처(디스코드 봇)는 안 함"""
        trans_started, order, got = threading.Event(), [], {}

        def diarize(path):
            order.append(("diarize", Path(path).name))
            return [(0, 1000, 0)], {0: [1.0]}
        self.diarize = diarize
        base = self.many

        def many(items):
            trans_started.set(); time.sleep(0.2); order.append(("transcribed",)); return base(items)
        self.many = many

        def finish(b, wavs, parts, llm, spk, min_chars, announce=True, diar=None):
            got[b.items[0].rel] = diar[1].result()[0] if diar else None
        self.finish = finish
        jobs = bundles(self.d, 2) + bundles(self.d, 1, source="discord-bot")
        self.run_jobs(jobs)
        self.assertEqual(got, {jobs[0].items[0].rel: [(0, 1000, 0)], jobs[1].items[0].rel: [(0, 1000, 0)], jobs[2].items[0].rel: None})
        self.assertEqual(order.index(("transcribed",)), 2)   # 화자 분리 둘이 전사가 끝나기 전에

    def test_group_failure_falls_back_per_bundle(self):
        base = self.many

        def many(items):
            names = [Path(it["wav"]).parent for it in items]
            if len(set(names)) > 1: raise RuntimeError("whisper 실패: 무리째")
            if any(b"bad" in Path(it["wav"]).read_bytes() for it in items): raise RuntimeError("whisper 실패: 이 묶음")
            return base(items)
        self.many = many
        jobs = bundles(self.d, 3)
        orig = A.prep_job

        def prep(j, call):
            orig(j, call)
            if j.i == 1: j.wavs[0].write_bytes(b"bad")
        with mock.patch.object(A, "prep_job", prep):
            out = self.run_jobs(jobs)
        self.assertEqual([c[0] for c in self.calls], ["g0", "g0-0", "g0-1", "g0-2"])
        self.assertEqual(self.finished, [jobs[0].items[0].rel, jobs[2].items[0].rel])
        self.assertIn("실패 1개", out)
        self.assertIn("이 묶음", out)

    def test_prep_failure_isolated(self):
        def to_wav(src, dst):
            if "1-0" in src.name: raise OSError("ffmpeg 실패")
            dst.write_bytes(b"RIFF"); return 20_000
        out = self.run_jobs(bundles(self.d, 3), to_wav=to_wav)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0][1]), 2)   # 실패한 묶음은 전사에 넣지 않는다
        self.assertIn("ffmpeg 실패", out)
        self.assertEqual(len(self.finished), 2)

    def test_not_multi_pipeline_runs_files_in_turn(self):
        self.pipe.multi = False
        got = []

        def transcribe(wav, tmp, words=False, hints=None, used=None, on_progress=None):
            got.append((wav.name, tmp == wav.parent)); return [{"startMs": 0, "endMs": 1, "speaker": None, "text": "네"}]
        with mock.patch.object(A, "transcribe", transcribe):
            self.run_jobs(bundles(self.d, 2, files=2))
        self.assertEqual(got, [("p0.wav", True), ("p1.wav", True)] * 2)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.finished), 2)

    def test_cancel_kills_and_cleans(self):
        """마무리 중 중단(KeyboardInterrupt)이면 갈래를 멈추고 파이프라인을 끝내고 임시 폴더를 지운다"""
        killed, dirs, base = threading.Event(), [], self.many

        def many(items):
            dirs.extend(Path(it["wav"]).parent for it in items)
            if len(self.calls) > 1: killed.wait(5)   # 둘째 무리는 죽일 때까지 돈다
            return base(items)
        self.many = many

        def finish(b, *a):
            raise KeyboardInterrupt
        self.finish = finish
        t = time.time()
        with mock.patch.object(A.P, "kill_all", killed.set):
            with self.assertRaises(KeyboardInterrupt):
                self.run_jobs(bundles(self.d, 3), group_chunks=1)
        self.assertTrue(killed.is_set())
        self.assertLess(time.time() - t, 4)
        self.assertTrue(dirs and not any(x.exists() for x in dirs))


class KillTests(unittest.TestCase):
    def test_kill_all_ends_tree(self):
        """파이프라인이 띄운 손자(whisper-cli 자리)까지 끝난다"""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pid_file = d / "child.pid"
            (d / "run.py").write_text("\n".join([
                "import subprocess, sys, time, pathlib",
                f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
                f"pathlib.Path(r'{pid_file}').write_text(str(c.pid))",
                "time.sleep(60)"]), encoding="utf-8")
            p = P.Pipeline("slow", "1", "", False, d / "run.py", {}, d, multi=True)
            err = []
            t = threading.Thread(target=lambda: err.append(self.catch(lambda: P.run_many(p, {}, [{"wav": d / "a.wav"}], d))))
            t.start()
            for _ in range(100):
                if pid_file.exists() and pid_file.read_text(): break
                time.sleep(0.1)
            P.kill_all()
            t.join(10)
            self.assertFalse(t.is_alive())
            self.assertIsInstance(err[0], RuntimeError)
            import runlock
            self.assertFalse(runlock.pid_alive(int(pid_file.read_text())))

    @staticmethod
    def catch(f):
        try:
            f()
        except Exception as e:
            return e


if __name__ == "__main__":
    unittest.main()
