"""python -m unittest test_whisper_lite_1 (가짜 whisper-cli, 실제 전사는 하지 않음)"""
import importlib.util, io, json, sys, tempfile, unittest, wave
from contextlib import redirect_stdout
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import pipelines as P

HERE = Path(__file__).parent
WL1 = HERE / "pipelines" / "whisper-lite@1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = load("wl1_transcribe", WL1 / "transcribe.py")
SETTINGS = P.defaults(P.load(WL1))


class FakeProc:
    """whisper-cli 대신: 조각마다 'processing' 줄, 끝에 시간 줄을 표준 오류로 내고 -oj 결과를 쓴다"""
    def __init__(self, test, cmd, cwd, **kw):
        test.calls.append(cmd)
        retry = "-tp" in cmd
        names = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]
        lines = [] if "-ng" in cmd else ["ggml_vulkan: 0 = AMD Radeon RX 6600 (AMD proprietary driver) | uma: 0\n",
                                         "whisper_backend_init_gpu: using Vulkan0 backend\n"]
        for n in names:
            lines.append(f"main: processing '{n}' (16000 samples, 1.0 sec), 4 threads ...\n")
            text = test.texts.get((n, retry), test.texts.get((n, False), ""))
            data = {"transcription": [{"offsets": {"from": 1000, "to": 2000}, "text": text}]}
            (Path(cwd) / (Path(n).stem + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            lines.append(f"whisper_vad_detect_speech: vad time = {100 * (names.index(n) + 1):.2f} ms processing 16000 samples\n")   # 실행 안에서 쌓임
        lines += ["whisper_print_timings:   encode time =   100.50 ms /     2 runs (   50.25 ms per run)\n",
                  "whisper_print_timings:    total time =  1000.00 ms\n"]
        self.stderr = iter(lines)
        self.code = test.exit_code

    def wait(self):
        return self.code


class ChunkTests(unittest.TestCase):
    def test_pack(self):
        self.assertEqual(T.pack([(0, 10_000), (12_000, 30_000), (40_000, 45_000)], 30_000),
                         [[(0, 10_000), (12_000, 30_000)], [(40_000, 45_000)]])   # 10 + 0.1 + 18 = 28.1초 → 한 조각, 5초 더하면 넘음
        self.assertEqual(T.pack([(0, 70_000)], 30_000), [[(0, 30_000)], [(30_000, 60_000)], [(60_000, 70_000)]])   # 긴 구간은 잘라 넣음
        self.assertEqual(T.pack([], 30_000), [])

    def test_to_orig(self):
        pieces = [(1000, 3000), (10_000, 12_000)]   # 조각 안: 0~2000 = 1~3초, 2000~2100 무음, 2100~4100 = 10~12초
        self.assertEqual([T.to_orig(pieces, t) for t in (0, 1999, 2050, 2100, 4100, 9000)], [1000, 2999, 3000, 10_000, 12_000, 12_000])
        self.assertEqual(T.to_orig([(5000, 8000)], 500), 5500)   # 조각 하나(whisper VAD)는 시작 + 시각

    def test_chunk_segments(self):
        data = {"transcription": [{"offsets": {"from": 1900, "to": 2300}, "text": " 걸친 말 "}, {"offsets": {"from": 0, "to": 10}, "text": "시청해 주셔서 감사합니다"}]}
        self.assertEqual(T.chunk_segments(data, [(1000, 3000), (10_000, 12_000)]), [{"startMs": 2900, "endMs": 10_200, "text": "걸친 말"}])


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tools = root / "tools"
        (self.tools / "src" / "whisper.cpp" / "build" / "bin").mkdir(parents=True)
        (self.tools / "models").mkdir()
        for p in (self.tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe", self.tools / "models" / "ggml-batisay-ko-turbo-q8_0.bin",
                  self.tools / "models" / "ggml-silero-v5.1.2.bin"):
            p.write_bytes(b"x")
        self.wav = root / "a.wav"
        with wave.open(str(self.wav), "wb") as w:   # 70초 무음 → 조각 3개(30초 안팎)
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(np.zeros(16000 * 70, np.int16).tobytes())
        self.work = root / "work"; self.work.mkdir()
        self.calls, self.texts, self.exit_code = [], {}, 0
        self.orig = T.subprocess.Popen
        T.subprocess.Popen = lambda cmd, cwd=None, **kw: FakeProc(self, cmd, cwd, **kw)

    def tearDown(self):
        T.subprocess.Popen = self.orig
        self.tmp.cleanup()

    def run_it(self, **settings):
        buf = io.StringIO()
        with redirect_stdout(buf):
            segs, stats, used = T.transcribe(self.wav, self.work, {**SETTINGS, "vadEngine": "whisper", **settings}, True, self.tools, None)
        lines = [json.loads(l.removeprefix("@@progress ")) for l in buf.getvalue().splitlines() if l.startswith("@@progress ")]
        return segs, stats, lines

    def test_args_retry_and_progress(self):
        bad = " ".join(["데스라인 벤션 2차 강화입니다."] * 10)
        self.texts = {("c0000.wav", False): "안녕하세요", ("c0001.wav", False): bad, ("c0001.wav", True): "버스 안내 방송",
                      ("c0002.wav", False): "끝"}
        segs, stats, prog = self.run_it()
        self.assertEqual([(s["startMs"], s["text"], s.get("suspect")) for s in segs],
                         [(1000, "안녕하세요", None), (21_050, "버스 안내 방송", None), (41_050, "끝", None)])   # 무음이라 20초 지점에서 자름
        first, retry = self.calls
        arg = lambda cmd, k: cmd[cmd.index(k) + 1]
        self.assertEqual((arg(first, "-mc"), arg(first, "-bs"), arg(first, "-bo"), arg(first, "-t"), arg(first, "-vt")), ("0", "1", "1", "4", "0.5"))
        self.assertIn("-nf", first); self.assertNotIn("-ng", first); self.assertNotIn("-np", first)
        self.assertIn("-sow", first)   # 단어 단위(화자 구분)는 그대로
        self.assertEqual(arg(retry, "-tp"), "0.4")
        self.assertEqual([retry[i + 1] for i, a in enumerate(retry) if a == "-f"], ["c0001.wav"])
        self.assertEqual([(p["percent"], p["text"]) for p in prog],
                         [(0, "조각 0/3"), (0, "조각 1/3"), (30, "조각 2/3"), (60, "조각 3/3"), (90, "다시 돌리는 중 1/1"), (100, "끝")])
        self.assertEqual((stats["retried"], stats["device"], len(stats["passSec"])), (1, "AMD Radeon RX 6600", 2))
        w = stats["whisper"]
        self.assertEqual((w["encode"], w["total"]), ({"ms": 201.0, "runs": 4}, {"ms": 2000.0, "runs": 0}))   # 실행 두 번을 더함
        self.assertEqual(w["vad"]["ms"], 400.0)   # 실행마다 마지막 값(첫 실행 조각 3개 300 + 다시 1개 100)
        self.assertEqual(w["startup"]["runs"], 0)

    def test_cpu_only_beam_fallback(self):
        self.texts = {("c0000.wav", False): "네"}
        _, stats, _ = self.run_it(useGpu=False, threads=8, beamSize=5, bestOf=5, fallback=True)
        [cmd] = self.calls
        self.assertIn("-ng", cmd); self.assertNotIn("-nf", cmd)
        self.assertEqual((cmd[cmd.index("-t") + 1], cmd[cmd.index("-bs") + 1]), ("8", "5"))
        self.assertEqual(stats["device"], "CPU")

    @unittest.skipIf(importlib.util.find_spec("sherpa_onnx") is None, "sherpa-onnx 없음")
    def test_sherpa_vad_chunks(self):
        """sherpa: VAD 한 번 → 말소리만 이은 조각, whisper-cli는 --vad 없이, 시각은 녹음 시각으로"""
        old = T.find_vad_onnx, T.speech_spans
        T.find_vad_onnx = lambda tools: tools / "models" / "silero_vad.onnx"
        T.speech_spans = lambda samples, model, s: [(1000, 11_000), (20_000, 35_000), (50_000, 60_000)]
        try:
            self.texts = {("c0000.wav", False): "첫 조각", ("c0001.wav", False): "둘째"}
            segs, stats, _ = self.run_it(vadEngine="sherpa")
        finally:
            T.find_vad_onnx, T.speech_spans = old
        [cmd] = self.calls
        self.assertNotIn("--vad", cmd)
        self.assertEqual((stats["chunks"], stats["vad"]["engine"], stats["vad"]["speechSec"]), (2, "sherpa", 35.0))
        # 조각 0 = 1~11초 + 무음 0.1초 + 20~35초(25.1초), 조각 1 = 50~60초. 가짜 결과는 조각 안 1~2초
        self.assertEqual([(s["startMs"], s["endMs"], s["text"]) for s in segs], [(2000, 3000, "첫 조각"), (51_000, 52_000, "둘째")])
        with wave.open(str(self.work / "c0000.wav")) as w:
            self.assertEqual(w.getnframes(), 16000 * 25 + 1600)
        self.assertEqual(stats["failures"], {})

    def test_whisper_fails(self):
        self.exit_code = 3
        with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()):
            T.transcribe(self.wav, self.work, {**SETTINGS, "vadEngine": "whisper"}, False, self.tools, None)
        self.assertIn("whisper 실패", str(e.exception.code))

    def test_manifest_and_seed(self):
        p = P.load(WL1)
        self.assertEqual((p.key, p.auto_recommend, p.hints), ("whisper-lite@1", False, True))
        root = Path(self.tmp.name) / "settings"
        self.assertEqual(P.seed(p, root), [1, 2])
        self.assertEqual(P.read(p, 1, root), SETTINGS)   # seed 1 = 매니페스트 기본값(GPU)
        self.assertEqual(P.read(p, 2, root), {**SETTINGS, "useGpu": False, "threads": 8})   # seed 2 = CPU만

    def test_quality_same_as_wc2(self):
        """품질 검사·필터는 whisper-cpp@2를 그대로 베꼈다(첫 설명 줄만 다름)"""
        for f in ("quality.py", "vocab.py"):
            a = (HERE / "pipelines" / "whisper-cpp@2" / f).read_text(encoding="utf-8").splitlines()
            b = (WL1 / f).read_text(encoding="utf-8").splitlines()
            self.assertEqual(a[1:], b[2:], f)


if __name__ == "__main__":
    unittest.main()
