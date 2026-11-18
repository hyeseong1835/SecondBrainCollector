"""python -m unittest test_multi_input — 여러 입력(--inputs, 2026-11-18): 입력마다 결과가 하나씩 돌린 것과 같은가(가짜 whisper, 실제 전사 안 함)"""
import importlib.util, io, json, sys, tempfile, threading, unittest, wave
from contextlib import redirect_stdout
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import pipelines as P

HERE = Path(__file__).parent
WL1, WC2 = HERE / "pipelines" / "whisper-lite@1", HERE / "pipelines" / "whisper-cpp@2"
BAD = " ".join(["데스라인 벤션 2차 강화입니다."] * 10)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


L = load("multi_wl1", WL1 / "transcribe.py")
C = load("multi_wc2", WC2 / "transcribe.py")


def text_for(path: Path, retry: bool, prompt: str) -> str:
    """조각 wav 내용(가장 큰 진폭)으로 정하는 글: 이름이 달라도 같은 조각이면 같은 글(진짜 whisper처럼)"""
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    amp = int(np.abs(x.astype(np.int32)).max()) if len(x) else 0
    base = {0: "", 1000: "안녕하세요 세컨 브레인", 2000: BAD, 3000: "끝"}.get(amp, f"진폭 {amp}")
    if retry and amp == 2000: base = "버스 안내 방송"
    return base + (" (힌트)" if prompt else "")


def write_wav(path: Path, parts: list, rate: int = 16000):
    """parts = [(초, 진폭)]"""
    x = np.concatenate([np.full(int(sec * 16000), amp, np.int16) for sec, amp in parts])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(x.tobytes())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.root = Path(self.tmp.name)
        self.tools = root / "tools"
        for p in ("src/whisper.cpp/build/bin/whisper-cli.exe", "models/ggml-batisay-ko-turbo-q8_0.bin", "models/ggml-silero-v5.1.2.bin"):
            (self.tools / p).parent.mkdir(parents=True, exist_ok=True); (self.tools / p).write_bytes(b"x")
        self.wavs = []
        for name, parts in (("a", [(25, 1000), (25, 2000), (20, 3000)]), ("b", [(10, 1000)]), ("c", [(40, 2000)]), ("d", [(5, 0)])):
            write_wav(root / f"{name}.wav", parts); self.wavs.append(root / f"{name}.wav")
        self.hints = {"prompt": "세컨브레인", "ids": [7], "fixes": [{"id": 9, "wrong": "세컨 브레인", "right": "SecondBrain"}]}
        (root / "h.json").write_text(json.dumps(self.hints, ensure_ascii=False), encoding="utf-8")
        self.calls = []
        self.lock = threading.Lock()

    def tearDown(self):
        self.tmp.cleanup()

    def work(self, name):
        d = self.root / name
        d.mkdir(exist_ok=True)
        return d

    def cases(self):
        """(wav, words, hints) 넷: 단어 단위·힌트가 섞여 (단어, 프롬프트) 무리가 셋"""
        return [(self.wavs[0], True, None), (self.wavs[1], False, self.hints), (self.wavs[2], True, None), (self.wavs[3], True, self.hints)]

    def spec(self, cases, broken=False):
        rows = [{"wav": str(w), "words": words, "hints": str(self.root / "h.json") if h else None} for w, words, h in cases]
        if broken: rows.insert(1, {"wav": str(self.root / "없음.wav"), "words": True, "hints": None})
        return rows


class FakeProc:
    """whisper-lite@1용 가짜 whisper-cli(Popen 자리): 조각 내용으로 글을 정한다"""
    def __init__(self, test, cmd, cwd):
        with test.lock: test.calls.append(cmd)
        retry, prompt = "-tp" in cmd, cmd[cmd.index("--prompt") + 1] if "--prompt" in cmd else ""
        names = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]
        lines = ["whisper_backend_init_gpu: using Vulkan0 backend\n"]
        for n in names:
            lines.append(f"main: processing '{n}' (16000 samples, 1.0 sec), 4 threads ...\n")
            data = {"transcription": [{"offsets": {"from": 1000, "to": 2000}, "text": text_for(Path(cwd) / n, retry, prompt)}]}
            (Path(cwd) / (Path(n).stem + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.stderr = (l for l in lines)

    def kill(self): pass
    def poll(self): return 0
    def wait(self): return 0


class WhisperLiteTests(Base):
    def setUp(self):
        super().setUp()
        self.orig = L.subprocess.Popen
        L.subprocess.Popen = lambda cmd, cwd=None, **kw: FakeProc(self, cmd, cwd)
        self.settings = {**P.defaults(P.load(WL1)), "vadEngine": "whisper"}

    def tearDown(self):
        L.subprocess.Popen = self.orig
        super().tearDown()

    def quiet(self, f, *a):
        with redirect_stdout(io.StringIO()) as buf:
            res = f(*a)
        return res, [json.loads(l.removeprefix("@@progress ")) for l in buf.getvalue().splitlines() if l.startswith("@@progress ")]

    def test_many_same_as_one_by_one(self):
        for run in ({"parallel": 1}, {"parallel": 3}):
            with self.subTest(run=run):
                one = []
                for k, (wav, words, h) in enumerate(self.cases()):
                    (segs, stats, used), _ = self.quiet(L.transcribe, wav, self.work(f"one{k}{run['parallel']}"), self.settings, words, self.tools, h, run)
                    one.append((segs, used, {x: stats[x] for x in ("chunks", "retried", "suspectChunks", "failures", "prompt", "fixedSegments")}))
                self.calls.clear()
                many, prog = self.quiet(L.transcribe_many, self.spec(self.cases()), self.work(f"many{run['parallel']}"), self.settings, self.tools, run)
                got = [(segs, used, {x: stats[x] for x in ("chunks", "retried", "suspectChunks", "failures", "prompt", "fixedSegments")}) for segs, stats, used in many]
                self.assertEqual(got, one)
                self.assertTrue(any(s["text"] == "버스 안내 방송" for s in one[0][0]))   # 다시 돌리기를 탔다
                self.assertTrue(any("original" in s for s in one[1][0]))   # 고침 짝
                self.assertEqual(many[0][1]["multi"], {"inputs": 4, "chunks": sum(x[2]["chunks"] for x in one)})
                # (단어, 프롬프트) 무리 셋: (단어, 없음) = a·c, (글, 힌트) = b, (단어, 힌트) = d → 첫 전사 whisper-cli 무리별(parallel 1이면 무리마다 한 번)
                first = [c for c in self.calls if "-tp" not in c]
                if run["parallel"] == 1:
                    self.assertEqual(len(first), 3)
                    self.assertEqual([n for i, n in enumerate(first[0]) if first[0][i - 1] == "-f"], ["i000_c0000.wav", "i000_c0001.wav", "i000_c0002.wav", "i002_c0000.wav", "i002_c0001.wav"])
                self.assertEqual(prog[0], {"percent": 0, "text": f"조각 0/{many[0][1]['multi']['chunks']}(파일 4개)" + (" · 작업자 3" if run["parallel"] == 3 else ""),
                                           **({"workers": 3} if run["parallel"] == 3 else {})})
                self.assertEqual(prog[-1]["percent"], 100)
                pct = [p["percent"] for p in prog]
                self.assertEqual(pct, sorted(pct))   # 무리를 넘어가도 진행도가 뒤로 가지 않는다

    def test_broken_input_isolated(self):
        many, _ = self.quiet(L.transcribe_many, self.spec(self.cases(), broken=True), self.work("m"), self.settings, self.tools, {"parallel": 2})
        self.assertIsInstance(many[1], Exception)
        self.assertEqual([isinstance(x, Exception) for x in many], [False, True, False, False, False])
        (one, _, _), _ = self.quiet(L.transcribe, self.wavs[0], self.work("o"), self.settings, True, self.tools, None, {"parallel": 2})
        self.assertEqual(many[0][0], one)

    def test_main_inputs_writes_each_out(self):
        s = self.root / "s.json"; s.write_text(json.dumps(self.settings), encoding="utf-8")
        spec = self.spec(self.cases(), broken=True)
        for k, r in enumerate(spec): r["out"] = str(self.root / f"o{k}.json")
        i = self.root / "in.json"; i.write_text(json.dumps({"inputs": spec}, ensure_ascii=False), encoding="utf-8")
        argv, old = sys.argv, L.tools_dir
        L.tools_dir = lambda: self.tools
        try:
            sys.argv = ["transcribe.py", "--inputs", str(i), "--settings", str(s)]
            with redirect_stdout(io.StringIO()): L.main()
        finally:
            sys.argv, L.tools_dir = argv, old
        outs = [json.loads(Path(r["out"]).read_text(encoding="utf-8")) for r in spec]
        self.assertIn("error", outs[1])
        self.assertEqual([("segments" in o) for o in outs], [True, False, True, True, True])
        self.assertEqual(outs[2]["vocabUsed"][0], 7)
        self.assertFalse((self.root / "in.chunks").exists())   # 작업 폴더 정리


class WhisperCppTests(Base):
    def setUp(self):
        super().setUp()
        self.orig = C.whisper
        C.whisper = self.fake
        self.settings = P.defaults(P.load(WC2))

    def tearDown(self):
        C.whisper = self.orig
        super().tearDown()

    def fake(self, base, names, work, progress=None, run=1, weights=None, key="", on_workers=None):
        self.calls.append((base, list(names)))
        prompt = base[base.index("--prompt") + 1] if "--prompt" in base else ""
        for i, n in enumerate(names):
            data = {"transcription": [{"offsets": {"from": 1000, "to": 2000}, "text": text_for(work / n, "-tp" in base, prompt)}]}
            (work / (Path(n).stem + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            if progress: progress(i + 1, len(names), 0.0)
        return {"mode": "fixed"}

    def test_many_same_as_one_by_one(self):
        one = []
        for k, (wav, words, h) in enumerate(self.cases()):
            segs, stats, used = C.transcribe(wav, self.work(f"one{k}"), self.settings, words, self.tools, h, None, {"parallel": 2})
            one.append((segs, used, {x: stats[x] for x in ("chunks", "retried", "suspectChunks", "failures", "prompt", "fixedSegments")}))
        self.calls.clear()
        events = []
        many = C.transcribe_many(self.spec(self.cases(), broken=True), self.work("many"), self.settings, self.tools,
                                 lambda pct, text, workers=None: events.append((round(pct, 1), text)), {"parallel": 2})
        self.assertIsInstance(many[1], Exception)
        got = [(segs, used, {x: stats[x] for x in ("chunks", "retried", "suspectChunks", "failures", "prompt", "fixedSegments")}) for segs, stats, used in many[:1] + many[2:]]
        self.assertEqual(got, one)
        self.assertTrue(any(s["text"] == "버스 안내 방송" for s in one[0][0]))
        first = [c for c in self.calls if "-tp" not in c[0]]
        self.assertEqual(len(first), 3)   # (단어, 프롬프트) 무리마다 풀 한 번
        retry = [c for c in self.calls if "-tp" in c[0]]
        self.assertEqual([n for _, names in retry for n in names], ["i000_c0001.wav", "i003_c0000.wav", "i003_c0001.wav"])   # a의 2000 조각, c 전부(b·d는 걸리지 않음)
        self.assertEqual(len(retry), 1)   # 걸린 조각이 모두 (단어, 없음) 무리라 다시 돌리기 한 번
        pct = [p for p, _ in events]
        self.assertEqual(pct, sorted(pct))
        self.assertTrue(events[0][1].endswith("(파일 5개) · 작업자 2"))


class RunManyTests(unittest.TestCase):
    """pipelines.run_many: 여러 입력 계약(가짜 파이프라인 스크립트)"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d = self.root / "fake@1"; d.mkdir()
        (d / "pipeline.json").write_text(json.dumps({"name": "fake", "version": "1", "entry": "t.py", "multiInput": True, "hints": True,
                                                    "settings": {"x": {"type": "number", "default": 1}}}), encoding="utf-8")
        (d / "t.py").write_text("\n".join([
            "import argparse, json, sys",
            "ap = argparse.ArgumentParser(); ap.add_argument('--inputs'); ap.add_argument('--settings'); ap.add_argument('--wav'); ap.add_argument('--out'); ap.add_argument('--words', action='store_true'); ap.add_argument('--hints')",
            "a = ap.parse_args()",
            "spec = json.load(open(a.inputs, encoding='utf-8'))['inputs']",
            "if any('죽음' in r['wav'] for r in spec): sys.exit('전체 실패')",
            "print('@@progress ' + json.dumps({'percent': 50, 'text': '조각 1/2', 'workers': 2}), flush=True)",
            "for k, r in enumerate(spec):",
            "    body = {'error': '깨짐'} if '깨짐' in r['wav'] else {'segments': [{'startMs': k, 'endMs': k + 5, 'text': ('단어 ' if r['words'] else '') + ('힌트 ' if r['hints'] else '') + r['wav'][-5:]}], 'vocabUsed': [k], 'stats': {'k': k}}",
            "    open(r['out'], 'w', encoding='utf-8').write(json.dumps(body, ensure_ascii=False))",
        ]), encoding="utf-8")
        self.p = P.load(d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_contract(self):
        self.assertTrue(self.p.multi)
        self.assertEqual(P.listing({self.p.key: self.p}, root=self.root / "settings")[0]["multiInput"], True)
        h = self.root / "h.json"; h.write_text("{}", encoding="utf-8")
        ev = []
        res = P.run_many(self.p, {"x": 1}, [{"wav": Path("a.wav"), "words": True, "hints": h}, {"wav": Path("깨짐.wav")}, {"wav": Path("b.wav")}],
                         self.root, on_progress=ev.append)
        self.assertEqual(ev, [{"percent": 50.0, "text": "조각 1/2", "workers": 2}])
        self.assertEqual(res[0], ([{"startMs": 0, "endMs": 5, "speaker": None, "text": "단어 힌트 a.wav"}], {0}, {"k": 0}))
        self.assertIsInstance(res[1], RuntimeError)
        self.assertEqual(res[2][0][0]["text"], "b.wav")
        with self.assertRaises(RuntimeError):
            P.run_many(self.p, {"x": 1}, [{"wav": Path("죽음.wav")}], self.root)
        self.assertEqual(P.ACTIVE, set())

    def test_single_only_pipeline_refused(self):
        self.p.multi = False
        with self.assertRaises(ValueError):
            P.run_many(self.p, {}, [{"wav": Path("a.wav")}], self.root)


class ManifestTests(unittest.TestCase):
    def test_both_pipelines_declare_multi_input(self):
        for d in (WL1, WC2):
            self.assertTrue(P.load(d).multi, d.name)


if __name__ == "__main__":
    unittest.main()
