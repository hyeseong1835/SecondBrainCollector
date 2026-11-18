"""예상 시간 시험: python -m unittest test_eta (임시 폴더에서만, 전사는 돌리지 않음)"""
import argparse, contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parent))
import eta as E


class Clock:
    def __init__(self): self.t = 1_800_000_000.0
    def __call__(self): return self.t
    def tick(self, s): self.t += s


def run_file(e: E.Eta, clock: Clock, k: int, audio: float, wav: float, work: float):
    e.start_file(k); clock.tick(wav); e.file_audio(audio); clock.tick(work); e.end_file()


class FitTests(unittest.TestCase):
    def test_too_few(self):
        self.assertIsNone(E.fit([(60, 30), (60, 30)]))
        self.assertIsNone(E.fit([]))

    def test_linear_recovers_rate_and_per_file(self):
        a, b = E.fit([(x, 0.5 * x + 4) for x in (10, 60, 200, 30, 400)])
        self.assertAlmostEqual(a, 0.5, places=6); self.assertAlmostEqual(b, 4, places=6)

    def test_same_length_uses_ratio(self):
        a, b = E.fit([(60, 30), (60, 33), (60, 27)])
        self.assertAlmostEqual(a, 0.5, delta=0.01); self.assertEqual(b, 0.0)

    def test_negative_intercept_falls_back_to_ratio(self):
        a, b = E.fit([(10, 1), (100, 60), (200, 130)])   # 직선이면 b < 0
        self.assertEqual(b, 0.0)
        self.assertGreater(a, 0)

    def test_recent_weighs_more(self):
        old, new = [(60, 60)] * 20, [(60, 30)] * 5   # 기기가 빨라졌다(병렬 등)
        a, _ = E.fit(old + new)
        self.assertLess(a, 0.8)   # 단순 평균(0.9)보다 새 쪽(0.5)으로
        a_hist, _ = E.fit(old + new, n_history=20)   # 앞 20개가 지난 기록이면 더 새 쪽으로
        self.assertLess(a_hist, a)

    def test_all_zero_audio(self):
        a, b = E.fit([(0, 5), (0, 7), (0, 6)])
        self.assertEqual(a, 0.0); self.assertAlmostEqual(b, 6.0, delta=0.1)


class HistoryTests(unittest.TestCase):
    def test_key_includes_settings_and_run_options(self):
        ref = {"name": "whisper-cpp", "version": "2", "settingsVersion": 3}
        self.assertEqual(E.key(ref), "whisper-cpp@2#3")
        self.assertEqual(E.key(ref, {"parallel": 2, "z": "x", "b": True}), "whisper-cpp@2#3?b=true&parallel=2&z=x")
        self.assertNotEqual(E.key(ref, {"parallel": 2}), E.key(ref, {"parallel": 3}))

    def test_save_load_keep(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "eta-history.json"
            self.assertEqual(E.load_history("k", path), [])
            E.save_history("k", [(60, 30)], path, now=1)
            E.save_history("j", [(10, 5)], path, now=2)
            E.save_history("k", [(float(i), 1.0) for i in range(E.KEEP)], path, now=3)
            got = E.load_history("k", path)
            self.assertEqual(len(got), E.KEEP)
            self.assertEqual(got[0], (0.0, 1.0))   # 오래된 (60, 30)은 밀려났다
            self.assertEqual(E.load_history("j", path), [(10.0, 5.0)])

    def test_broken_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "eta-history.json"
            path.write_text("{깨짐", encoding="utf-8")
            self.assertEqual(E.load_history("k", path), [])
            E.save_history("k", [(60, 30)], path)   # 깨진 파일은 새로 쓴다
            self.assertEqual(E.load_history("k", path), [(60.0, 30.0)])
            path.write_text(json.dumps({"k": [[60, 30], "x", [1], [-1, 5], [5, 0]]}), encoding="utf-8")
            self.assertEqual(E.load_history("k", path), [(60.0, 30.0)])


class EtaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "eta-history.json"
        self.clock = Clock()

    def tearDown(self): self.tmp.cleanup()

    def make(self, plan, history=None):
        return E.Eta(plan, "p@1#1", history=history, clock=self.clock, path=self.path)

    def test_no_history_waits_for_few_files(self):
        e = self.make([[60, 60, 60, 60, 60, 60]])
        snap = e.snapshot()
        self.assertIsNone(snap["remainingSec"]); self.assertIsNone(snap["endsAt"]); self.assertIsNone(snap["rate"])
        self.assertEqual((snap["filesDone"], snap["filesTotal"], snap["audioTotalSec"], snap["samples"]), (0, 6, 360, 0))
        e.start_bundle(0)
        for k in range(2): run_file(e, self.clock, k, 60, 1, 29)
        self.assertIsNone(e.snapshot()["remainingSec"])   # 2개로는 아직
        run_file(e, self.clock, 2, 60, 1, 29)
        snap = e.snapshot()
        self.assertEqual(snap["filesDone"], 3); self.assertEqual(snap["audioDoneSec"], 180)
        self.assertAlmostEqual(snap["remainingSec"], 90, delta=0.5)   # 남은 3개 × 30초
        self.assertEqual(snap["endsAt"][:4], "2027")   # 1.8e9 + 90초의 지역 시각
        self.assertIsNone(snap["fileRemainingSec"])    # 파일 사이

    def test_history_gives_estimate_from_start(self):
        e = self.make([[100, 100], [50]], history=[(100, 50)] * 5)
        snap = e.snapshot()
        self.assertAlmostEqual(snap["remainingSec"], 125, delta=0.5)
        self.assertEqual((snap["rate"], snap["historySamples"]), (0.5, 5))

    def test_current_file_and_percent(self):
        e = self.make([[100, 100]], history=[(100, 50)] * 5)
        e.start_bundle(0); e.start_file(0); self.clock.tick(2); e.file_audio(100)
        snap = e.snapshot()
        self.assertAlmostEqual(snap["fileRemainingSec"], 48, delta=0.01)   # 예상 50 − 경과 2
        self.assertAlmostEqual(snap["remainingSec"], 98, delta=0.01)        # + 다음 파일 50
        self.clock.tick(10)   # 실제로는 예상보다 2배 느리다: 10초에 20%
        snap = e.snapshot(percent=20)
        pace, model = 10 * 0.8 / 0.2, 50 * 0.8
        self.assertAlmostEqual(snap["fileRemainingSec"], 0.2 * pace + 0.8 * model, delta=0.01)
        self.assertIsNone(e.snapshot(percent=20, transcribing=False)["fileRemainingSec"])
        self.clock.tick(100)   # 예상을 넘기면 0(음수 아님)
        self.assertEqual(e.snapshot()["fileRemainingSec"], 0)

    def test_bundle_extra_spread_and_saved(self):
        e = self.make([[30, 90], [60]])
        e.start_bundle(0)
        run_file(e, self.clock, 0, 30, 0, 15)
        run_file(e, self.clock, 1, 90, 0, 45)
        self.clock.tick(40)   # 화자 구분·올리기 40초 → 오디오 비율로 10·30
        e.end_bundle(ok=True)
        self.assertEqual(e.run, [(30, 25.0), (90, 75.0)])
        self.assertEqual(E.load_history("p@1#1", self.path), [(30.0, 25.0), (90.0, 75.0)])
        snap = e.snapshot()
        self.assertEqual((snap["filesDone"], snap["audioDoneSec"]), (2, 120))

    def test_failed_bundle_not_learned_but_counted_done(self):
        e = self.make([[60], [60], [60], [60]], history=[(60, 30)] * 3)
        e.start_bundle(0); run_file(e, self.clock, 0, 60, 0, 500); e.end_bundle(ok=False)
        self.assertEqual(e.run, []); self.assertEqual(E.load_history("p@1#1", self.path), [])
        snap = e.snapshot()
        self.assertEqual(snap["filesDone"], 1)
        self.assertAlmostEqual(snap["remainingSec"], 90, delta=0.01)   # 실패한 500초는 배우지 않음

    def test_unknown_lengths_filled_with_mean(self):
        e = self.make([[100, 0], [50]], history=[(60, 30)] * 3)
        snap = e.snapshot()
        self.assertEqual(snap["unknownFiles"], 1)
        self.assertEqual(snap["audioTotalSec"], 225)   # 0 → 아는 것의 평균 75
        e.start_bundle(0); run_file(e, self.clock, 0, 100, 0, 50); e.start_file(1); e.file_audio(20)   # wav를 만들면 실제 길이로
        self.assertEqual(e.snapshot()["audioTotalSec"], 170)

    def test_all_unknown(self):
        e = self.make([[0, 0]], history=[(60, 30)] * 3)
        snap = e.snapshot()
        self.assertIsNone(snap["remainingSec"])
        self.assertEqual(snap["unknownFiles"], 2)

    def test_workers(self):
        """파이프라인이 낸 작업자 수: 지금·최소·최대·시간 가중 평균. 안 왔으면 칸 없음"""
        c = Clock()
        e = E.Eta([[60.0]], None, history=[], clock=c, save=False)
        self.assertNotIn("workers", e.snapshot())
        e.workers(2); c.t += 10; e.workers(4); c.t += 30; e.workers(3)
        self.assertEqual(e.snapshot()["workers"], {"now": 3, "min": 2, "max": 4, "avg": 3.5})   # (2×10 + 4×30) / 40
        c.t += 40
        self.assertEqual(e.snapshot()["workers"]["avg"], 3.25)                                # + 3×40 → 260 / 80

    def test_human(self):
        self.assertEqual([E.human(x) for x in (None, 4.6, 125, 3600 + 300)], ["계산 중", "5초", "2분", "1시간 5분"])


class FlowTests(unittest.TestCase):
    """겹치기 실행(Flow): 무리가 다 끝나야 표본, 앞 무리가 끝난 뒤 걸린 시간을 오디오 비율로"""
    def test_group_samples_and_active_group(self):
        c = Clock()
        e = E.Flow([[60.0, 20.0], [20.0], [100.0]], None, groups=[[0, 1], [2]], history=[(60, 30)] * 3, clock=c, save=False)
        snap = e.snapshot()
        self.assertAlmostEqual(snap["remainingSec"], 100)   # a = 0.5, b = 0 → 200초 × 0.5
        e.start_group([0, 1]); c.tick(20)
        s = e.snapshot(50.0, file=1, files=1)
        self.assertAlmostEqual(s["fileRemainingSec"], 0.5 * 20 + 0.5 * 25)   # 경과로 늘인 20초와 예상 50×(1−0.5)를 반씩
        self.assertAlmostEqual(s["remainingSec"], s["fileRemainingSec"] + 50)
        e.end_transcribe(); c.tick(5)
        e.finish(0, ok=True)
        self.assertEqual(len(e.run), 0)   # 무리의 묶음 1이 아직
        self.assertEqual(e.snapshot()["filesDone"], 2)
        e.finish(1, ok=False)   # 실패한 묶음 파일은 표본에서 뺀다
        self.assertEqual(e.run, [(60.0, 25 * 60 / 80), (20.0, 25 * 20 / 80)])
        e.start_group([2]); c.tick(10); e.end_transcribe(); e.finish(2)
        self.assertEqual(e.run[-1], (100.0, 10.0))
        self.assertEqual(e.snapshot()["remainingSec"], 0)

    def test_sequential_files_progress(self):
        """여러 입력이 아닌 파이프라인: 무리 안 파일 k/n과 파일 안 percent로 무리 진행"""
        c = Clock()
        e = E.Flow([[60.0, 60.0]], None, history=[(60, 30)] * 3, clock=c, save=False)
        e.start_group([0]); c.tick(30)
        s = e.snapshot(0.0001, file=2, files=2)   # 파일 1 끝, 파일 2 막 시작 → 진행 약 절반
        self.assertAlmostEqual(s["fileRemainingSec"], 30, delta=0.1)


class AnalyzeEtaTests(unittest.TestCase):
    """analyze.run_jobs(겹치기 실행)가 진행 줄에 eta를 붙이는 모양(전사·서버 없이, 가짜 여러 입력 파이프라인)"""
    def test_progress_lines_carry_eta(self):
        import analyze as A, bundles as B, pipelines as P
        clock = Clock()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "eta-history.json"
            key = E.key({"name": "fake", "version": "1", "settingsVersion": 1}, {"parallel": 2, "flow": "multi"})
            E.save_history(key, [(60, 30)] * 3, path)
            T0 = 1_800_000_000_000
            mk = lambda start, ms: B.Item("phone", f"{start}.m4a", T0 + start, T0 + start + ms, Path(d) / f"{start}.m4a")
            jobs = [B.Bundle("phone", [mk(0, 60_000), mk(70_000, 60_000)]), B.Bundle("phone", [mk(900_000, 1)]), B.Bundle("phone", [mk(990_000, 60_000)])]
            pipe = P.Pipeline("fake", "1", "", False, Path(d) / "run.py", {}, Path(d), multi=True)
            calls = []

            def fake_many(p, settings, items, tmp, tools=None, on_progress=None, run_options=None, tag="g"):
                calls.append([Path(it["wav"]).name for it in items])
                clock.tick(15); on_progress({"percent": 50.0, "text": "조각 2/4", "workers": 2}); clock.tick(15)
                return [RuntimeError("깨진 파일") if k == 2 else ([{"startMs": 0, "endMs": 1, "speaker": None, "text": "네"}], {k}, {}) for k in range(len(items))]

            def fake_finish(b, wavs, parts, llm, spk, min_chars, announce=True, diar=None):
                return {"segments": [s for p in parts for s in p], "speechMs": 1, "transcript": "네", "summary": None}

            args = argparse.Namespace(plan=False, dry_run=True, stage="transcribe", no_auto_remove=True, min_summary_chars=60)
            out = io.StringIO()
            real = E.Flow
            with mock.patch.object(A, "PROGRESS", True), mock.patch.object(A, "ETA", None),                  mock.patch.object(A, "CHOSEN", A.Chosen(pipe, 1, {}, {"parallel": 2})),                  mock.patch.object(A.E, "Flow", lambda plan, k, groups: real(plan, k, groups, clock=clock, path=path)),                  mock.patch.object(A.runlock, "set_plan", lambda keys: None), mock.patch.object(A.P, "run_many", fake_many),                  mock.patch.object(A, "to_wav", lambda src, dst: 60_000), mock.patch.object(A, "finish_bundle", fake_finish),                  mock.patch.object(A, "bundle_hints", lambda call, b, tmp: None), mock.patch.object(A, "speakers_ready", lambda: False),                  contextlib.redirect_stdout(out):
                A.run_jobs(args, lambda *x: None, jobs, 0, 0)
            lines = out.getvalue().splitlines()
            ev = [json.loads(l[len("@@progress "):]) for l in lines if l.startswith("@@progress ")]
            by = lambda name: [e for e in ev if e["event"] == name]
            self.assertEqual(calls, [["p0.wav", "p1.wav", "p0.wav", "p0.wav"]])   # 묶음 셋이 한 무리, 파이프라인 한 번
            plan = by("plan")[0]
            self.assertEqual((plan["groups"], plan["multiInput"]), (1, True))
            self.assertEqual((plan["eta"]["filesTotal"], plan["eta"]["unknownFiles"], plan["eta"]["audioTotalSec"], plan["eta"]["rate"]), (4, 1, 240, 0.5))
            self.assertAlmostEqual(plan["eta"]["remainingSec"], 120)
            start = by("start")[0]
            self.assertEqual((start["i"], start["last"], start["bundles"], start["files"]), (1, 3, 3, 4))
            half = [e for e in by("step") if e.get("percent") == 50.0][0]
            self.assertEqual((half["file"], half["files"], half["groupFiles"], half["workers"]), (1, 1, 4, 2))
            self.assertIsNotNone(half["eta"]["fileRemainingSec"])
            self.assertTrue(all("eta" in e for e in by("start") + by("done") + by("error")))
            self.assertEqual([e["i"] for e in by("done")], [1, 3])   # 차례대로, 실패한 묶음 2는 error
            self.assertEqual(len(by("error")), 1)
            self.assertIn("깨진 파일", by("error")[0]["message"])
            last = by("done")[-1]["eta"]
            self.assertEqual((last["filesDone"], last["remainingSec"]), (4, 0))
            self.assertNotIn("eta", by("end")[0])
            self.assertEqual(by("end")[0]["failed"], 1)
            self.assertEqual(len(E.load_history(key, path)), 3 + 3)   # 실패한 묶음은 빼고


if __name__ == "__main__":
    unittest.main()
