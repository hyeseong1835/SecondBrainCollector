"""python -m unittest test_autopar (whisper-cli 동시 실행 풀·자동 조정기. 가짜 whisper·가짜 측정값만, 실제 전사·GPU 측정은 하지 않음)"""
import importlib.util, io, json, os, sys, tempfile, threading, time, unittest, wave
from contextlib import redirect_stdout
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import pipelines as P

HERE = Path(__file__).parent
WC2, WL1 = HERE / "pipelines" / "whisper-cpp@2", HERE / "pipelines" / "whisper-lite@1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


A = load("autopar_t", WC2 / "autopar.py")
GB = A.GB


def R(**kw):
    return A.Reading(**kw)


class Clock:
    def __init__(self, t=1000.0): self.t = t
    def __call__(self): return self.t


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(A.parse({"parallel": "auto"}), {"auto": True, "n": None, "max": 0, "yield": True})
        self.assertEqual(A.parse({"parallel": "AUTO", "parallelMax": 3, "yield": False}), {"auto": True, "n": None, "max": 3, "yield": False})
        self.assertEqual(A.parse({"parallel": 3})["n"], 3)
        self.assertEqual(A.parse({"parallel": 3}, cpu_only=True)["n"], 1)          # CPU 전용 고정 수는 예전처럼 1
        self.assertTrue(A.parse({"parallel": "auto"}, cpu_only=True)["auto"])      # 자동은 CPU 전용에서도(프로세스 수만)
        self.assertEqual((A.parse(None)["n"], A.parse({})["n"]), (1, 1))            # 실행 옵션 없음(직접 실행) = 고정 1
        self.assertEqual(A.parse({"parallelMax": 2})["auto"], True)               # 파일은 있는데 parallel이 없으면 새 기본 auto
        self.assertEqual((A.parse({"parallel": 0})["n"], A.parse({"parallel": "많이"})["n"]), (1, 1))
        self.assertEqual((A.parse({"parallel": "auto", "parallelMax": 0.5})["max"], A.parse({"parallel": "auto", "parallelMax": True})["max"]), (0, 0))

    def test_state_key(self):
        a = A.state_key("whisper-cpp@2", {"x": 1, "y": 2}, False)
        self.assertEqual(a, A.state_key("whisper-cpp@2", {"y": 2, "x": 1}, False))   # 키 순서와 상관없음
        self.assertNotEqual(a, A.state_key("whisper-cpp@2", {"x": 1, "y": 3}, False))
        self.assertTrue(A.state_key("whisper-cpp@2", {}, True).endswith("+words"))
        self.assertTrue(a.startswith("whisper-cpp@2#"))

    def test_table_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a" / "autoparallel.json"
            self.assertEqual(A.load_table("k", f), {})
            A.save_table("k", {2: [1.5, 3, 100.0], 3: [1.7, 1, 200.0]}, f)
            A.save_table("other", {1: [0.5, 1, 1.0]}, f)
            self.assertEqual(A.load_table("k", f), {2: [1.5, 3, 100.0], 3: [1.7, 1, 200.0]})
            self.assertEqual(A.load_table("other", f), {1: [0.5, 1, 1.0]})
            f.write_text("{깨짐", encoding="utf-8")
            self.assertEqual(A.load_table("k", f), {})
            A.save_table("k", {1: [1.0, 1, 1.0]}, f)   # 깨진 파일은 새로 씀
            self.assertEqual(A.load_table("k", f), {1: [1.0, 1, 1.0]})
            self.assertEqual(list(Path(d, "a").glob("*.tmp")), [])

    def test_state_path_env(self):
        old = os.environ.get("SECONDBRAIN_DATA_DIR")
        os.environ["SECONDBRAIN_DATA_DIR"] = "X:/sb"
        try:
            self.assertEqual(A.state_path(), Path("X:/sb") / "analysis" / "autoparallel.json")
        finally:
            if old is None: os.environ.pop("SECONDBRAIN_DATA_DIR")
            else: os.environ["SECONDBRAIN_DATA_DIR"] = old


class TunerTests(unittest.TestCase):
    """가짜 측정값으로 결정 규칙"""
    ROOMY = dict(vram_total=8 * GB, vram_used=2 * GB, per_proc=1.2 * GB, gpu=60.0, gpu_others=2.0, cpu=40.0, mem_free=4 * GB, commit_free=10 * GB)

    def test_start(self):
        self.assertEqual(A.Tuner().start(), A.START)
        self.assertEqual(A.Tuner({2: [1.0, 1, 0], 3: [1.4, 1, 0], 4: [1.3, 1, 0]}).start(), 3)        # 표에서 가장 빠른 수
        self.assertEqual(A.Tuner({3: [1.4, 1, 0]}).start(R(vram_total=8 * GB, vram_used=5.5 * GB)), 1)   # VRAM 여유 2.5 - 0.5 → 1.5GB 하나
        self.assertEqual(A.Tuner({5: [2.0, 1, 0]}, user_max=4).start(), A.START)                         # 상한 밖 기록은 안 봄
        self.assertEqual(A.Tuner(cpu_cap=1).start(), 1)

    def test_climb_up_back_and_reprobe(self):
        c = Clock()
        t = A.Tuner(clock=c)
        self.assertEqual(t.decide(R(**self.ROOMY), 2)[:2], (2, None))                         # 처리량을 아직 못 쟀으면 그대로
        n, why, urgent = t.decide(R(rate=1.0, **self.ROOMY), 2)
        self.assertEqual((n, urgent), (3, False)); self.assertIn("늘려 봄", why)              # 2개 1.0, 3개는 모름 → 늘림
        self.assertEqual(t.decide(R(**self.ROOMY), 3)[:2], (3, None))                         # 바꾼 직후(3개 못 잼)는 그대로
        n, why, _ = t.decide(R(rate=0.98, **self.ROOMY), 3)
        self.assertEqual(n, 2); self.assertIn("같거나 느림", why)                              # 3개가 안 빨라짐 → 되돌림
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 2)[:2], (2, None))                # 3개가 느렸던 기록이 새것 → 머문다
        c.t += A.REPROBE_SEC + 1
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 2)[0], 3)                         # 오래되면 다시 재 본다

    def test_keeps_climbing_while_faster(self):
        t = A.Tuner(clock=Clock())
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 1)[0], 2)
        self.assertEqual(t.decide(R(rate=1.6, **self.ROOMY), 2)[0], 3)
        self.assertEqual(t.decide(R(rate=1.62, **self.ROOMY), 3)[:2], (3, None))   # 1.6 → 1.62는 5% 안: 평평한 곳에서 더 오르지 않는다
        self.assertEqual(t.decide(R(rate=1.55, **self.ROOMY), 3)[0], 2)            # 평균이 2개 아래로 → 줄임

    def test_growth_gates(self):
        t = A.Tuner({3: [9.0, 1, time.time()]})   # 3개가 더 빨랐다는 기록이 있어도
        t.decide(R(rate=1.0, **self.ROOMY), 2)
        for over, label in ((dict(vram_used=6.5 * GB), "VRAM"), (dict(gpu=99.0), "GPU"), (dict(cpu=95.0), "CPU"),
                            (dict(commit_free=2 * GB), "커밋"), (dict(mem_free=0.8 * GB), "메모리")):
            self.assertEqual(t.decide(R(rate=1.0, **{**self.ROOMY, **over}), 2)[:2], (2, None), label)
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 2)[0], 3)

    def test_urgent_shrink(self):
        t = A.Tuner()
        for over, word in ((dict(mem_free=0.3 * GB), "메모리"), (dict(commit_free=0.2 * GB), "커밋"), (dict(vram_used=7.9 * GB), "VRAM")):
            n, why, urgent = t.decide(R(**{**self.ROOMY, **over}), 3)
            self.assertEqual((n, urgent), (2, True)); self.assertIn(word, why)
        self.assertEqual(t.decide(R(**{**self.ROOMY, "mem_free": 0.1 * GB}), 1)[:2], (1, None))   # 1개 아래로는 안 줄임

    def test_yield(self):
        t = A.Tuner(clock=Clock())
        n, why, urgent = t.decide(R(rate=1.0, **{**self.ROOMY, "gpu_others": 55.0}), 3)
        self.assertEqual((n, urgent), (2, False)); self.assertIn("다른 프로그램", why)
        self.assertEqual(t.decide(R(rate=1.0, **{**self.ROOMY, "gpu_others": 55.0}), 1)[:2], (1, None))
        self.assertEqual(t.table, {})                                                        # 나눠 쓴 처리량은 표에 안 넣음
        t.decide(R(rate=1.0, **{**self.ROOMY, "gpu_others": 15.0}), 1)                         # YIELD_GPU/3(10%) 넘음 → 안 넣음
        self.assertEqual(t.table, {})
        off = A.Tuner(yield_gpu=False, clock=Clock())
        self.assertEqual(off.decide(R(rate=1.0, **{**self.ROOMY, "gpu_others": 55.0}), 3)[0], 3)   # yield 끔: 그대로

    def test_caps(self):
        t = A.Tuner(user_max=2, clock=Clock())
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 3)[:2], (2, "상한 2"))
        self.assertEqual(t.decide(R(rate=1.0, **self.ROOMY), 2)[:2], (2, None))
        t = A.Tuner(clock=Clock()); t.lower(2)
        self.assertEqual(t.cap(), 2)
        self.assertEqual(A.Tuner().cap(R(vram_total=8 * GB, vram_used=1 * GB, per_proc=1 * GB), 2), 8)   # 2 + (7 - 0.5) // 1 = 8, HARD_MAX
        self.assertEqual(A.Tuner().cap(R(vram_total=8 * GB, vram_used=7.6 * GB, per_proc=1 * GB), 4), 4)  # 여유가 없어도 이미 뜬 것은 둔다
        self.assertEqual(A.Tuner().cap(None, 0), A.HARD_MAX)

    def test_no_measurements_rate_only(self):
        """측정 실패(전부 None)면 처리량만으로"""
        t = A.Tuner(clock=Clock())
        self.assertEqual(t.decide(R(rate=1.0), 1)[0], 2)
        self.assertEqual(t.decide(R(rate=1.9), 2)[0], 3)
        self.assertEqual(t.decide(R(rate=1.8), 3)[0], 2)


class GpuCounterTests(unittest.TestCase):
    def test_parse_instance(self):
        self.assertEqual(A.parse_instance("pid_123_luid_0x00000000_0x0000ABCD_phys_0_eng_3_engtype_3D"),
                         {"pid": 123, "luid": "0x00000000_0x0000abcd", "eng": "3_3D"})
        self.assertEqual(A.parse_instance("luid_0x00000000_0x00614771_phys_0"), {"luid": "0x00000000_0x00614771"})
        self.assertEqual(A.parse_instance("pid_9_luid_0x0_0x1_phys_0_eng_1_engtype_Video Decode")["eng"], "1_Video Decode")
        self.assertEqual(A.parse_instance("엉망"), {})

    def test_fill_gpu(self):
        L1, L2 = "luid_0x00000000_0x00000001", "luid_0x00000000_0x00000002"   # L1 = 외장(우리), L2 = 내장
        eng = [(f"pid_10_{L1}_phys_0_eng_0_engtype_3D", 40.0), (f"pid_11_{L1}_phys_0_eng_0_engtype_3D", 35.0),
               (f"pid_20_{L1}_phys_0_eng_0_engtype_3D", 12.0), (f"pid_20_{L1}_phys_0_eng_1_engtype_Compute", 5.0),
               (f"pid_30_{L2}_phys_0_eng_0_engtype_3D", 90.0)]
        adapter = [(f"{L1}_phys_0", 5 * GB), (f"{L2}_phys_0", 0.1 * GB)]
        proc = [(f"pid_10_{L1}_phys_0", 1.3 * GB), (f"pid_11_{L1}_phys_0", 1.1 * GB), (f"pid_12_{L1}_phys_0", 0.05 * GB), (f"pid_20_{L1}_phys_0", 2 * GB)]
        r = R()
        A.fill_gpu(r, eng, adapter, proc, {10, 11, 12}, {"0x00000000_0x00000001": 8 * GB, "0x00000000_0x00000002": 0.125 * GB})
        self.assertEqual((r.vram_total, r.vram_used, r.vram_free), (8 * GB, 5 * GB, 3 * GB))
        self.assertAlmostEqual(r.per_proc, 1.2 * GB, delta=1)          # 막 뜬 pid 12(50MB)는 뺌
        self.assertEqual((r.gpu, r.gpu_ours, r.gpu_others), (87.0, 75.0, 12.0))   # 3D 엔진 합(40+35+12), 내장 GPU는 안 봄
        r = R()
        A.fill_gpu(r, eng, adapter, [], set(), {})   # 우리 프로세스가 아직 없으면 전용 메모리를 가장 많이 쓰는 어댑터, VRAM 총량 모름
        self.assertEqual((r.vram_used, r.vram_total, r.gpu_others, r.gpu_ours), (5 * GB, None, 87.0, 0.0))
        r = R(); A.fill_gpu(r, [], [], [], set(), {})
        self.assertIsNone(r.gpu)

    @unittest.skipUnless(os.name == "nt", "Windows만")
    def test_sampler_smoke(self):
        """진짜 PDH·DXGI: 읽기만(값이 없어도 깨지지 않는다)"""
        s = A.Sampler.create()
        if s is None: self.skipTest("PDH 없음")
        try:
            r = s.read(set())
            self.assertIsInstance(r, A.Reading)
            if r.mem_free is not None: self.assertGreater(r.mem_free, 0)
        finally:
            s.close()


class FakeWhisper:
    """launch 대신: 조각마다 delay초 쉬고 결과를 적는다. kill되면 그 자리에서 멈추고 종료 코드 1. fail = {조각: 남은 실패 횟수}면 그 조각에서 종료 코드 3"""
    def __init__(self, delay=0.01, fail=None):
        self.delay, self.fail = delay, dict(fail or {})
        self.lock = threading.Lock()
        self.out, self.runs, self.launches, self.peak, self.live, self.killed = {}, {}, [], 0, 0, 0

    class Proc:
        def __init__(self): self.ev = threading.Event(); self.pid = None
        def kill(self): self.ev.set()
        def poll(self): return 1 if self.ev.is_set() else None

    def launch(self, w, batch, started):
        p = self.Proc()
        with self.lock:
            self.launches.append((w, list(batch))); self.live += 1; self.peak = max(self.peak, self.live)
        started(p)
        try:
            for n in batch:
                if p.ev.wait(self.delay):
                    with self.lock: self.killed += 1
                    return 1, "죽음"
                with self.lock:
                    if self.fail.get(n, 0) > 0:
                        self.fail[n] -= 1; return 3, f"{n}에서 실패"
                    self.out[n] = f"글 {n}"; self.runs[n] = self.runs.get(n, 0) + 1
            return 0, ""
        finally:
            with self.lock: self.live -= 1

    def done(self, n):
        with self.lock: return n in self.out

    def reset(self, n):
        with self.lock: self.out.pop(n, None)


class ScriptTuner:
    """decide를 차례대로 돌려주는 가짜 조정기(그다음은 그대로)"""
    def __init__(self, start, script):
        self.n0, self.script, self.table, self.measured, self.lowered, self.readings = start, list(script), {}, set(), [], []
    def start(self, r=None): return self.n0
    def lower(self, n): self.lowered.append(n)
    def decide(self, r, n):
        self.readings.append((r, n))
        return self.script.pop(0) if self.script else (n, None, False)


class PoolTests(unittest.TestCase):
    def names(self, k): return [f"c{i:04d}" for i in range(k)]

    def pool(self, fw, names, opts, tuner=None, tick=0.03, **kw):
        logs = []
        p = A.Pool(names, fw.launch, fw.done, fw.reset, {n: 30_000 for n in names}, opts, tuner, None, tick=tick, log=logs.append, **kw)
        p.logs = logs
        return p

    def test_fixed_all_once(self):
        fw = FakeWhisper()
        names = self.names(85)
        p = self.pool(fw, names, A.parse({"parallel": 2}))
        p.run()
        self.assertEqual(sorted(fw.out), names)
        self.assertTrue(all(v == 1 for v in fw.runs.values()))
        self.assertEqual(sorted(len(b) for _, b in fw.launches), [5, 40, 40])
        self.assertEqual(fw.peak, 2)
        st = p.stats()
        self.assertEqual((st["mode"], st["min"], st["max"], st["avg"], st["batches"], st["recovered"]), ("fixed", 2, 2, 2.0, 3, 0))
        self.assertNotIn("rates", st)

    def test_fixed_more_workers_than_chunks_and_empty(self):
        fw = FakeWhisper()
        p = self.pool(fw, self.names(3), A.parse({"parallel": 5})); p.run()
        self.assertEqual((sorted(fw.out), len(fw.launches)), (self.names(3), 3))
        p = self.pool(FakeWhisper(), [], A.parse({"parallel": "auto"}), ScriptTuner(2, [])); p.run()
        self.assertEqual(p.stats()["batches"], 0)

    def test_auto_guided_batches(self):
        fw = FakeWhisper(delay=0.002)
        p = self.pool(fw, self.names(120), A.parse({"parallel": "auto"}), ScriptTuner(2, []), tick=10)
        p.run()
        sizes = [len(b) for _, b in fw.launches]
        self.assertEqual(sorted(fw.out), self.names(120))
        self.assertTrue(all(A.MIN_BATCH <= s <= A.BATCH for s in sizes[:-1]), sizes)
        self.assertEqual(sizes[0], 30)                     # 120 / (2 × 2)
        self.assertLess(len(sizes), 12)                    # 너무 잘게 나누지 않는다(띄우는 비용)

    def test_auto_grow_and_soft_shrink(self):
        """조정기가 3 → 5 → 2: 늘리면 바로 띄우고, 줄이면 묶음 끝에 그만둔다. 모든 조각이 한 번씩"""
        fw = FakeWhisper(delay=0.004)
        tuner = ScriptTuner(3, [(5, "늘림", False), (2, "줄임", False)])
        names = self.names(200)
        seen = []
        p = self.pool(fw, names, A.parse({"parallel": "auto"}), tuner, on_workers=seen.append)
        p.run()
        self.assertEqual(sorted(fw.out), names)
        self.assertTrue(all(v == 1 for v in fw.runs.values()))
        self.assertEqual(fw.killed, 0)
        self.assertEqual(seen[:3], [3, 5, 2])
        self.assertEqual(fw.peak, 5)
        st = p.stats()
        self.assertEqual((st["mode"], st["start"], st["min"], st["max"], st["final"]), ("auto", 3, 2, 5, 2))
        self.assertEqual([c[1:] for c in st["changes"]], [[3, "시작"], [5, "늘림"], [2, "줄임"]])
        self.assertTrue(2 <= st["avg"] <= 5)
        self.assertIn("작업자 5개: 늘림", p.logs)

    def test_auto_urgent_shrink_kills_and_requeues(self):
        """급히 줄이기: 가장 늦게 띄운 작업자를 죽이고 못 끝낸 조각을 되돌려 다른 작업자가 끝낸다(빠짐·겹침 없음)"""
        fw = FakeWhisper(delay=0.01)
        tuner = ScriptTuner(3, [(1, "VRAM 여유 0.2GB", True)])
        names = self.names(90)
        p = self.pool(fw, names, A.parse({"parallel": "auto"}), tuner)
        p.run()
        self.assertEqual(sorted(fw.out), names)
        self.assertGreaterEqual(fw.killed, 2)                 # 3 → 1: 둘을 죽임
        self.assertTrue(all(v >= 1 for v in fw.runs.values()))
        self.assertEqual(p.stats()["final"], 1)
        late = [w for w, _ in fw.launches if w >= 3]
        self.assertEqual(late, [])                            # 새로 띄우지 않고 남은 작업자 0이 마저

    def test_auto_failure_recovers(self):
        """자동: 작업자가 여럿일 때 whisper-cli 하나가 실패하면 조각을 되돌리고 상한을 낮춰 이어 간다"""
        fw = FakeWhisper(fail={"c0045": 1})
        tuner = ScriptTuner(3, [])
        names = self.names(60)
        p = self.pool(fw, names, A.parse({"parallel": "auto"}), tuner, tick=10)
        p.run()
        self.assertEqual(sorted(fw.out), names)
        st = p.stats()
        self.assertEqual((st["recovered"], st["final"]), (1, 2))
        self.assertEqual(tuner.lowered, [2])
        self.assertIn("whisper-cli 실패", st["changes"][-1][2])

    def test_auto_failure_alone_or_repeated_fails(self):
        fw = FakeWhisper(fail={"c0001": 1})
        with self.assertRaises(SystemExit) as e:
            self.pool(fw, self.names(5), A.parse({"parallel": "auto"}), ScriptTuner(1, []), tick=10).run()
        self.assertEqual(str(e.exception.code), "whisper 실패: c0001에서 실패")
        fw = FakeWhisper(fail={"c0010": 99})   # 계속 실패하면 MAX_RECOVER번 뒤 실패
        with self.assertRaises(SystemExit):
            self.pool(fw, self.names(40), A.parse({"parallel": "auto"}), ScriptTuner(4, []), tick=10).run()

    def test_fixed_failure_kills_others(self):
        fw = FakeWhisper(delay=0.05, fail={"c0040": 1})
        with self.assertRaises(SystemExit) as e:
            self.pool(fw, self.names(80), A.parse({"parallel": 2})).run()
        self.assertEqual(str(e.exception.code), "whisper 실패: c0040에서 실패")
        self.assertEqual(fw.killed, 1)          # 작업자 0도 죽임
        self.assertEqual(len(fw.launches), 2)   # 다음 묶음을 띄우지 않음

    def test_exception_reraised(self):
        def launch(w, batch, started):
            raise OSError("못 띄움")
        with self.assertRaises(OSError):
            A.Pool(["a", "b"], launch, lambda n: False, lambda n: None, None, A.parse({"parallel": 2}), log=lambda m: None).run()

    def test_step_measures_rate_and_passes_pids(self):
        """step: 창의 처리량(오디오 초/초)·우리 pid를 측정에 넣어 조정기에 준다. 바꾼 뒤 WARMUP초·대기열이 빈 창은 None"""
        clock = Clock(0.0)
        tuner = ScriptTuner(2, [])

        class Sampler:
            def __init__(self): self.pids = []
            def read(self, pids): self.pids.append(set(pids)); return A.Reading(gpu=50.0)
        sm = Sampler()
        done = set()
        p = A.Pool(self.names(30), None, lambda n: n in done, None, {n: 30_000 for n in self.names(30)}, A.parse({"parallel": "auto"}),
                   tuner, sm, clock=clock, log=lambda m: None)
        p.target = 2
        p.workers = {0: {"thread": None, "proc": type("P", (), {"pid": 77, "poll": lambda s: None})(), "batch": self.names(10), "kill": False, "retire": False}}
        for _ in range(10): p.queue.popleft()
        clock.t = 4.0; done.update(self.names(2)); p.step()
        self.assertIsNone(tuner.readings[-1][0].rate)                            # 시작 뒤 WARMUP(6초) 안이라 창이 짧다
        clock.t = 20.0; done.update(self.names(10)); p.step()
        r = tuner.readings[-1][0]
        self.assertAlmostEqual(r.rate, 8 * 30 / (20.0 - 6.0))                   # 4~20초에 끝난 8개 × 30초 ÷ (20 - 6)
        self.assertEqual((r.gpu, sm.pids[-1]), (50.0, {77}))
        p.queue.clear(); p.queue_empty_at = 21.0
        clock.t = 40.0; p.step()
        self.assertIsNone(tuner.readings[-1][0].rate)                            # 대기열이 빈 끝물은 안 잰다

    def test_short_file_no_tuner(self):
        """조각이 한 묶음(MIN_BATCH) 이하면 자동이어도 조정기·측정기 없이 1개, 표를 안 건드린다"""
        fw = FakeWhisper(delay=0.0)
        made = []
        old = A.LazySampler
        A.LazySampler = lambda *a, **k: made.append(1)
        try:
            st = A.run_pool(self.names(A.MIN_BATCH), fw.launch, fw.done, fw.reset, None, {"parallel": "auto"}, key="")
        finally:
            A.LazySampler = old
        self.assertEqual((st["mode"], st["max"], st["batches"], made), ("auto", 1, 1, []))
        self.assertIn("한 묶음", st["note"])

    def test_lazy_sampler(self):
        gate = threading.Event()

        class S:
            closed = False
            def read(self, pids): return A.Reading(cpu=12.0)
            def close(self): S.closed = True

        def create(cpu_only):
            gate.wait(5); return S()
        ls = A.LazySampler(False, create)
        self.assertIsNone(ls.read(set()).cpu)    # 준비 전: 빈 값(기다리지 않음)
        gate.set(); ls.t.join(5)
        self.assertEqual(ls.read(set()).cpu, 12.0)
        ls.close()
        self.assertTrue(S.closed)
        self.assertIsNone(A.LazySampler(False, lambda c: None).read(set()).cpu)   # 측정 실패면 계속 빈 값

    def test_run_pool_saves_table(self):
        """run_pool: 자동이면 표를 읽어 시작 수를 정하고 끝나면 쓴다. 고정이면 표를 안 건드린다"""
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("SECONDBRAIN_DATA_DIR"); os.environ["SECONDBRAIN_DATA_DIR"] = d
            try:
                A.save_table("k", {3: [2.0, 1, time.time()]})
                fw = FakeWhisper(delay=0.0)
                t = A.Tuner(A.load_table("k"))
                t.add(3, 2.2)
                st = A.run_pool(self.names(12), fw.launch, fw.done, fw.reset, None, {"parallel": "auto"}, key="k", tuner=t, sampler=None)
                self.assertEqual(st["start"], 3)
                self.assertAlmostEqual(A.load_table("k")[3][0], 2.2 * A.ALPHA + 2.0 * (1 - A.ALPHA), places=3)
                self.assertEqual(sorted(fw.out), self.names(12))
                st = A.run_pool(self.names(4), fw.launch, fw.done, fw.reset, None, {"parallel": 2}, key="k2")
                self.assertEqual(st["mode"], "fixed")
                self.assertEqual(A.load_table("k2"), {})
            finally:
                if old is None: os.environ.pop("SECONDBRAIN_DATA_DIR")
                else: os.environ["SECONDBRAIN_DATA_DIR"] = old

    def test_real_processes_kill_requeue(self):
        """진짜 하위 프로세스: 작업자 3개가 돌다 급히 1개로 줄여도 .json이 다 생기고 반쯤 쓴 파일이 남지 않는다"""
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            child = work / "child.py"
            child.write_text("\n".join(["import sys, time", "a = sys.argv[1:]",
                                        "for i, x in enumerate(a):",
                                        "    if x == '-of':",
                                        "        time.sleep(0.05); open(a[i + 1] + '.json', 'w').write('{\"ok\": 1}')", ""]), encoding="utf-8")
            import subprocess

            def launch(w, batch, started):
                cmd = [sys.executable, str(child)]
                for n in batch: cmd += ["-of", n]
                p = subprocess.Popen(cmd, cwd=str(work)); started(p)
                return p.wait(), ""

            def done(n):
                try: json.loads((work / f"{n}.json").read_text()); return True
                except (OSError, ValueError): return False
            names = self.names(40)
            tuner = ScriptTuner(3, [(1, "메모리 여유 0.3GB", True)])
            p = A.Pool(names, launch, done, lambda n: (work / f"{n}.json").unlink(missing_ok=True), None, A.parse({"parallel": "auto"}), tuner,
                       tick=0.3, log=lambda m: None)
            p.run()
            self.assertTrue(all(done(n) for n in names))
            self.assertEqual(p.stats()["final"], 1)


class PipelineSameResultTests(unittest.TestCase):
    """두 파이프라인: parallel 1과 auto(실제 Pool, 가짜 whisper)의 구간이 같다"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_env = os.environ.get("SECONDBRAIN_DATA_DIR"); os.environ["SECONDBRAIN_DATA_DIR"] = str(root / "sb")
        self.tools = root / "tools"
        for p in ("src/whisper.cpp/build/bin/whisper-cli.exe", "models/ggml-batisay-ko-turbo-q8_0.bin", "models/ggml-silero-v5.1.2.bin"):
            (self.tools / p).parent.mkdir(parents=True, exist_ok=True); (self.tools / p).write_bytes(b"")
        self.wav = root / "in.wav"
        x = np.full(16000 * 400, 1000, np.int16)   # 400초 → 조각 14개쯤
        for k in range(1, 20): x[16000 * 25 * k:16000 * 25 * k + 1600] = 0
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(x.tobytes())

    def tearDown(self):
        if self.old_env is None: os.environ.pop("SECONDBRAIN_DATA_DIR", None)
        else: os.environ["SECONDBRAIN_DATA_DIR"] = self.old_env
        self.tmp.cleanup()

    @staticmethod
    def text(stem, retry):
        k = int(stem[1:])
        return " ".join(["데스라인 벤션 2차 강화입니다."] * 10) if k % 5 == 3 and not retry else f"조각 {k} 말{' 다시' if retry else ''}"

    def test_whisper_cpp_2(self):
        T = load("wc2_same", WC2 / "transcribe.py")
        settings = P.defaults(P.load(WC2))

        def fake(cmd, work, on_percent=None, started=None):
            if started: started(type("P", (), {"pid": None, "kill": lambda s: None, "poll": lambda s: 0})())
            retry = "-tp" in cmd
            for i, x in enumerate(cmd):
                if x == "-of":
                    time.sleep(0.003)
                    data = {"transcription": [{"offsets": {"from": 500, "to": 1500}, "text": self.text(cmd[i + 1], retry)}]}
                    (work / f"{cmd[i + 1]}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    if on_percent: on_percent(100.0)
            return 0, ""
        T.run_whisper = fake
        self.addCleanup(setattr, T.A.Sampler, "create", T.A.Sampler.create)
        T.A.Sampler.create = staticmethod(lambda cpu_only=False: None)
        out = {}
        for par in (1, "auto", 3):
            work = Path(self.tmp.name) / f"w{par}"; work.mkdir()
            events = []
            segs, stats, _ = T.transcribe(self.wav, work, settings, False, self.tools, None, lambda *a: events.append(a), {"parallel": par})
            out[par] = (segs, {k: v for k, v in stats.items() if k not in ("parallel", "workers")})
            self.assertEqual(stats["parallel"], par)
            if par != 1: self.assertTrue(all(len(e) == 3 and e[1].endswith(f" · 작업자 {e[2]}") for e in events[1:] if not (par == "auto" and e is events[0])))
            else: self.assertTrue(all(len(e) == 2 for e in events))
        self.assertGreater(out[1][1]["chunks"], 10)
        self.assertGreater(out[1][1]["retried"], 0)
        self.assertEqual(out[1], out["auto"])
        self.assertEqual(out[1], out[3])

    def test_whisper_lite_1(self):
        T = load("wl1_same", WL1 / "transcribe.py")
        settings = {**P.defaults(P.load(WL1)), "vadEngine": "whisper"}
        test = self

        class Proc:
            def __init__(self, cmd, cwd=None, **kw):
                retry = "-tp" in cmd
                names = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]
                lines = []
                for n in names:
                    lines.append(f"main: processing '{n}' (16000 samples)\n")
                    data = {"transcription": [{"offsets": {"from": 500, "to": 1500}, "text": test.text(Path(n).stem, retry)}]}
                    (Path(cwd) / f"{Path(n).stem}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.stderr = io.StringIO("".join(lines)); self.pid = None
            def kill(self): pass
            def poll(self): return 0
            def wait(self): return 0
        self.addCleanup(setattr, T.subprocess, "Popen", T.subprocess.Popen)
        T.subprocess.Popen = Proc
        self.addCleanup(setattr, T.A.Sampler, "create", T.A.Sampler.create)
        T.A.Sampler.create = staticmethod(lambda cpu_only=False: None)
        out = {}
        for par in (1, "auto"):
            work = Path(self.tmp.name) / f"l{par}"; work.mkdir()
            with redirect_stdout(io.StringIO()) as buf:
                segs, stats, _ = T.transcribe(self.wav, work, settings, False, self.tools, None, {"parallel": par})
            out[par] = (segs, {k: v for k, v in stats.items() if k not in ("parallel", "workers", "passSec", "whisper")})
            lines = [P.parse_progress(l) for l in buf.getvalue().splitlines()]
            if par == "auto": self.assertTrue(all(l["text"].endswith(f" · 작업자 {l['workers']}") for l in lines[1:] if l))
            if par == "auto": self.assertEqual((stats["workers"]["start"], stats["workers"]["retry"]["note"][:6]), (2, "조각 3개 "))   # 다시 돌리기는 한 묶음
        self.assertEqual(out[1], out["auto"])
        # CPU로만: 자동이면 프로세스 상한 = 논리 코어 ÷ threads
        work = Path(self.tmp.name) / "cpu"; work.mkdir()
        with redirect_stdout(io.StringIO()):
            _, stats, _ = T.transcribe(self.wav, work, {**settings, "useGpu": False, "threads": 64}, False, self.tools, None, {"parallel": "auto"})
        self.assertEqual(stats["workers"]["max"], 1)

    def test_autopar_copies_identical(self):
        """두 파이프라인 폴더의 autopar.py는 같은 파일(폴더를 따로 넣고 빼므로 베껴 둠)"""
        self.assertEqual((WC2 / "autopar.py").read_bytes(), (WL1 / "autopar.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
