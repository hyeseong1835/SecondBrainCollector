"""python -m unittest test_whisper_cpp_2 (가짜 데이터만, whisper는 돌리지 않음)"""
import importlib.util, json, sys, tempfile, unittest, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import pipelines as P
import speakers as SP

HERE = Path(__file__).parent
WC2 = HERE / "pipelines" / "whisper-cpp@2"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = load("wc2_transcribe", WC2 / "transcribe.py")
Q, V = T.Q, T.V
SETTINGS = P.defaults(P.load(WC2))
seg = lambda a, b, text, **kw: {"startMs": a, "endMs": b, "text": text, **kw}


class MetricTests(unittest.TestCase):
    def test_phrase_repeat(self):
        self.assertEqual(Q.phrase_repeat([]), 0)
        self.assertEqual(Q.phrase_repeat("안녕 하세요 반가워요".split()), 1)
        self.assertEqual(Q.phrase_repeat("u/ u/ u/ u/ u/".split()), 5)
        self.assertEqual(Q.phrase_repeat(("데스라인 벤션 2차 강화입니다. " * 6 + "끝").split()), 6)
        self.assertEqual(Q.phrase_repeat("그, 그 그. 아니".split()), 3)   # 문장부호는 빼고 비교

    def test_char_run_and_compression(self):
        self.assertEqual(Q.char_run("으으 으으으 네"), 5)   # 공백은 건너뜀
        self.assertEqual(Q.char_run(""), 0)
        self.assertEqual(Q.compression(""), 0.0)
        self.assertGreater(Q.compression("데스라인 벤션 2차 강화입니다. " * 30), 2.4)
        self.assertLess(Q.compression("오늘 시험 끝나고 버스 타고 집에 가는 길인데 생각보다 문제가 어려웠어. 너는 어땠어?"), 2.4)

    def test_metrics_and_failures(self):
        ok = [seg(0, 4000, "오늘 시험 어땠어?"), seg(4000, 8000, "생각보다 어려웠어.")]
        m = Q.metrics(ok)
        self.assertEqual((m["chars"], m["speechSec"], m["phraseRepeat"]), (17, 8.0, 1))
        self.assertEqual(Q.failures(m, SETTINGS), [])
        self.assertEqual(Q.failures(Q.metrics([seg(0, 30000, "으" * 300)]), SETTINGS), ["charRun", "compression"])
        self.assertIn("phraseRepeat", Q.failures(Q.metrics([seg(0, 5000, " ".join(["u/"] * 50))]), SETTINGS))
        self.assertIn("charsPerSec", Q.failures(Q.metrics([seg(0, 1000, "가나다라마바사아자차카타파하 거너더러머버서어저처커터퍼허고노도로모보")]), SETTINGS))
        self.assertEqual(Q.failures(Q.metrics([seg(0, 100, "안녕하세요")]), SETTINGS), [])   # 짧은 글은 글자/초를 안 봄
        self.assertIsNone(Q.metrics([])["charsPerSec"])

    def test_speech_ms_overlap(self):
        self.assertEqual(Q.speech_ms([seg(0, 1000, "a"), seg(500, 1500, "b"), seg(3000, 4000, "c")]), 2500)


class FilterTests(unittest.TestCase):
    def test_collapse(self):
        self.assertEqual(Q.collapse(list("aaab")), [0, 1, 2, 3])          # 3번은 그대로
        self.assertEqual(Q.collapse(list("aaaab")), [0, 4])               # 4번 → 1번
        self.assertEqual(Q.collapse(list("xababababy")), [0, 1, 2, 9])    # 구절(2개) 4번 → 1번
        self.assertEqual(Q.collapse(list("abababab"), max_repeat=5), list(range(8)))

    def test_squeeze(self):
        self.assertEqual(Q.squeeze("으" * 300), "으")
        self.assertEqual(Q.squeeze("하하하"), "하하하")
        self.assertEqual(Q.squeeze("네네네네요"), "네요")

    def test_clean_symbols_and_runs_across_segments(self):
        segs = [seg(0, 1000, "내리실 때"), seg(1000, 2000, "n/"), seg(2000, 3000, "u/ u/ u/ u/ u/ u/"),
                seg(3000, 6000, "데스라인 벤션 2차 강화입니다. 데스라인 벤션 2차"),
                seg(6000, 9000, "강화입니다. 데스라인 벤션 2차 강화입니다. 데스라인 벤션 2차 강화입니다."),
                seg(9000, 9500, "으으으으으으"), seg(9500, 9800, "시청해 주셔서 감사합니다")]
        self.assertEqual(Q.clean(segs), [seg(0, 1000, "내리실 때"), seg(3000, 6000, "데스라인 벤션 2차 강화입니다."), seg(9000, 9500, "으")])

    def test_clean_words_mode_keeps_times_and_flags(self):
        words = [seg(i * 100, i * 100 + 90, "그", suspect=True) for i in range(5)] + [seg(500, 590, "거야")]
        self.assertEqual(Q.clean(words), [seg(0, 90, "그", suspect=True), seg(500, 590, "거야")])
        self.assertEqual(len(Q.clean(words[:3] + words[5:])), 4)   # 3번은 그대로

    def test_group_words_keeps_suspect(self):
        words = [dict(seg(0, 100, "가"), speaker=None), dict(seg(100, 200, "나", suspect=True), speaker=None),
                 dict(seg(5000, 5100, "다"), speaker=None)]
        out = SP.group_words(words)
        self.assertEqual([(s["text"], s.get("suspect")) for s in out], [("가 나", True), ("다", None)])


class ChunkTests(unittest.TestCase):
    def test_plan_chunks_cuts_at_quietest(self):
        energy = np.ones(1000)          # 100초, 0.1초 칸
        energy[250] = 0                 # 25.0~25.1초가 가장 조용
        energy[520] = 0                 # 25.1초 시작 조각에서 52.0초
        self.assertEqual(T.plan_chunks(energy, 100_000, 20_000, 30_000), [(0, 25_050), (25_050, 52_050), (52_050, 72_050), (72_050, 100_000)])
        self.assertEqual(T.plan_chunks(np.ones(100), 10_000, 20_000, 30_000), [(0, 10_000)])
        self.assertEqual(T.plan_chunks(np.ones(300), 30_000, 20_000, 30_000), [(0, 30_000)])
        self.assertEqual(T.plan_chunks(np.zeros(0), 0, 20_000, 30_000), [])

    def test_chunk_segments_offset(self):
        data = {"transcription": [{"offsets": {"from": 0, "to": 900}, "text": " 안녕 "}, {"offsets": {"from": 900, "to": 1000}, "text": " - "},
                                  {"offsets": {"from": 1000, "to": 99999}, "text": "끝"}]}
        self.assertEqual(T.chunk_segments(data, 30_000, 40_000), [seg(30_000, 30_900, "안녕"), seg(31_000, 40_000, "끝")])


class TranscribeTests(unittest.TestCase):
    """가짜 whisper: 조각 이름마다 정해 둔 글을 낸다. 오프셋·다시 돌리기·suspect·drop을 본다"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tools, self.work = root / "tools", root / "work"
        self.work.mkdir()
        for p in ("src/whisper.cpp/build/bin/whisper-cli.exe", "models/ggml-batisay-ko-turbo-q8_0.bin", "models/ggml-silero-v5.1.2.bin"):
            (self.tools / p).parent.mkdir(parents=True, exist_ok=True); (self.tools / p).write_bytes(b"")
        self.wav = root / "in.wav"
        x = np.full(16000 * 70, 1000, np.int16); x[16000 * 25:16000 * 25 + 1600] = 0   # 70초, 25.0~25.1초 무음
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(x.tobytes())
        self.calls, self.texts = [], {}
        self.orig = T.whisper
        T.whisper = self.fake

    def tearDown(self):
        T.whisper = self.orig
        self.tmp.cleanup()

    def fake(self, base, names, work):
        self.calls.append((base, names))
        retry = "-tp" in base
        for n in names:
            text = self.texts.get((n, retry), self.texts.get((n, False), ""))
            data = {"transcription": [{"offsets": {"from": 1000, "to": 2000}, "text": text}]}
            (work / (Path(n).stem + ".json")).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_it(self, hints=None, **settings):
        segs, stats, self.used = T.transcribe(self.wav, self.work, {**SETTINGS, **settings}, False, self.tools, hints)
        return segs, stats

    def test_offsets_and_retry(self):
        bad = " ".join(["데스라인 벤션 2차 강화입니다."] * 10)
        self.texts = {("c0000.wav", False): "안녕하세요", ("c0001.wav", False): bad, ("c0001.wav", True): "버스 안내 방송",
                      ("c0002.wav", False): "으" * 200}
        segs, stats = self.run_it()
        self.assertEqual([(s["startMs"], s["text"], s.get("suspect")) for s in segs],
                         [(1000, "안녕하세요", None), (26_050, "버스 안내 방송", None), (46_050, "으", True)])
        self.assertEqual((stats["chunks"], stats["retried"], stats["suspectChunks"]), (3, 2, 1))
        first, retry = self.calls
        self.assertIn("-mc", first[0]); self.assertEqual(first[0][first[0].index("-mc") + 1], "0")
        self.assertEqual(retry[1], ["c0001.wav", "c0002.wav"]); self.assertEqual(retry[0][-2:], ["-tp", "0.4"])

    def test_drop_and_no_retry(self):
        self.texts = {("c0000.wav", False): "네", ("c0001.wav", False): "u/ " * 40, ("c0002.wav", False): "끝"}
        segs, stats = self.run_it(retryTemperature=0, onSuspect="drop")
        self.assertEqual([s["text"] for s in segs], ["네", "끝"])
        self.assertEqual((len(self.calls), stats["retried"], stats["droppedChunks"]), (1, 0, 0))   # u/만이면 필터 뒤 글이 없어 의심 조각도 아님

    def test_drop_suspect(self):
        self.texts = {("c0000.wav", False): "네", ("c0001.wav", False): "으" * 100, ("c0002.wav", False): "끝"}
        segs, stats = self.run_it(retryTemperature=0, onSuspect="drop")
        self.assertEqual([s["text"] for s in segs], ["네", "끝"])
        self.assertEqual(stats["droppedChunks"], 1)

    def test_broken_utf8_from_whisper(self):
        def broken(base, names, work):
            for n in names:   # "안녕" 뒤에 "하"(ED 95 98)의 앞 두 바이트만
                raw = '{"transcription": [{"offsets": {"from": 0, "to": 500}, "text": "안녕'.encode() + b"\xed\x95" + b'"}]}'
                (work / (Path(n).stem + ".json")).write_bytes(raw)
        T.whisper = broken
        segs, _ = self.run_it(retryTemperature=0)
        self.assertEqual({s["text"] for s in segs}, {"안녕"})

    def test_hints_prompt_and_fixes(self):
        self.texts = {("c0000.wav", False): "세컨 브레인은 김철 수가", ("c0001.wav", False): "네", ("c0002.wav", False): "끝"}
        hints = {"prompt": "김철수, SecondBrain", "ids": [3, 8],
                 "fixes": [{"id": 12, "wrong": "세컨 브레인", "right": "SecondBrain"}, {"id": 13, "wrong": "없는말", "right": "x"}]}
        segs, stats = self.run_it(hints)
        self.assertEqual((segs[0]["text"], segs[0]["original"]), ("SecondBrain은 김철 수가", "세컨 브레인은 김철 수가"))
        self.assertNotIn("original", segs[1])
        self.assertEqual(self.used, [3, 8, 12])   # 프롬프트로 쓴 힌트 + 실제로 바꾼 고침 짝만
        self.assertEqual(stats["fixedSegments"], 1)
        base = self.calls[0][0]
        self.assertEqual(base[base.index("--prompt") + 1], "김철수, SecondBrain")
        self.assertEqual(base[base.index("-mc") + 1], "224")   # -mc 0이면 whisper.cpp가 프롬프트를 버린다

    def test_hints_off(self):
        self.texts = {("c0000.wav", False): "세컨 브레인", ("c0001.wav", False): "네", ("c0002.wav", False): "끝"}
        hints = {"prompt": "SecondBrain", "ids": [8], "fixes": [{"id": 12, "wrong": "세컨 브레인", "right": "SecondBrain"}]}
        segs, _ = self.run_it(hints, useHints=False)
        self.assertEqual((segs[0]["text"], self.used), ("세컨 브레인", []))
        base = self.calls[0][0]
        self.assertNotIn("--prompt", base); self.assertEqual(base[base.index("-mc") + 1], "0")
        segs, _ = self.run_it({"prompt": "", "ids": [8], "fixes": hints["fixes"]})   # 고침 짝만
        self.assertEqual((segs[0]["text"], self.used), ("SecondBrain", [12]))
        self.assertNotIn("--prompt", self.calls[-1][0])

    def test_manifest_and_seed(self):
        p = P.load(WC2)
        self.assertEqual((p.key, p.entry.name, p.auto_recommend, p.hints), ("whisper-cpp@2", "transcribe.py", True, True))
        self.assertEqual(p.schema["asrModel"]["type"], "whisper-model")
        root = Path(self.tmp.name) / "settings"
        self.assertEqual(P.seed(p, root), [1])
        self.assertEqual(P.read(p, 1, root), SETTINGS)
        self.assertEqual(SETTINGS["maxContext"], 0)


class VocabTests(unittest.TestCase):
    """고침 짝 치환: 낱말 경계(조사는 붙어도 됨), 구간에 걸친 짝, original"""
    def fix(self, texts, *pairs):
        segs = [seg(i * 100, i * 100 + 90, t) for i, t in enumerate(texts)]
        return V.apply_fixes(segs, [{"id": k, "wrong": w, "right": r} for k, (w, r) in enumerate(pairs, 1)])

    def test_boundaries(self):
        out, used = self.fix(["세컨브레인 세컨브레인에서 세컨브레인은요 시세컨브레인 세컨브레인스"], ("세컨브레인", "SecondBrain"))
        self.assertEqual(out[0]["text"], "SecondBrain SecondBrain에서 SecondBrain은요 시세컨브레인 세컨브레인스")   # 앞에 글자, 뒤에 조사 아닌 글자면 그대로
        self.assertEqual((out[0]["original"], used), ("세컨브레인 세컨브레인에서 세컨브레인은요 시세컨브레인 세컨브레인스", [1]))
        out, _ = self.fix(["김철도 왔어, 김철 김철수"], ("김철", "김철수"))
        self.assertEqual(out[0]["text"], "김철수도 왔어, 김철수 김철수")   # 김철수의 김철은 뒤가 조사가 아니라 그대로
        out, _ = self.fix(["(secondbrain)이랑 SECONDBRAIN"], ("secondbrain", "SecondBrain"))
        self.assertEqual(out[0]["text"], "(SecondBrain)이랑 SecondBrain")   # 영문은 대소문자 무시
        out, used = self.fix(["SecondBrain"], ("secondbrain", "SecondBrain"))
        self.assertEqual((out[0], used), (seg(0, 90, "SecondBrain"), []))   # 이미 맞는 글이면 안 바꾼 것
        out, used = self.fix(["가나다"], ("가", "x"), (" ", "y"))
        self.assertEqual((out[0]["text"], used), ("가나다", []))   # 두 글자 안 되는 틀린 글은 쓰지 않음

    def test_across_segments_and_order(self):
        out, _ = self.fix(["오늘", "세컨", "브레인으로", "정리", "세컨  브레인"], ("세컨 브레인", "SecondBrain"))
        self.assertEqual([(s["startMs"], s["endMs"], s["text"], s.get("original")) for s in out],
                         [(0, 90, "오늘", None), (100, 290, "SecondBrain으로", "세컨 브레인으로"), (300, 390, "정리", None),
                          (400, 490, "SecondBrain", "세컨  브레인")])
        out, used = self.fix(["세컨 브레인 앱", "브레인 좋아"], ("세컨 브레인", "SecondBrain"), ("브레인", "Brain"), ("SecondBrain 앱", "SB앱"))
        self.assertEqual([(s["text"], s["original"]) for s in out], [("SB앱", "세컨 브레인 앱"), ("Brain 좋아", "브레인 좋아")])   # 앞 짝부터 차례로, 원래 글은 처음 것
        self.assertEqual(used, [1, 2, 3])
        out, _ = V.apply_fixes([seg(0, 90, "세컨", suspect=True), seg(100, 190, "브레인")], [{"id": 1, "wrong": "세컨 브레인", "right": "SB"}])
        self.assertEqual(out, [{"startMs": 0, "endMs": 190, "text": "SB", "suspect": True, "original": "세컨 브레인"}])

    def test_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "h.json"
            f.write_text(json.dumps({"prompt": " 김철수,\n SecondBrain ", "ids": [3],
                                     "fixes": [{"id": 1, "wrong": "a b", "right": "c"}, {"wrong": " "}, "x"]}), encoding="utf-8")
            self.assertEqual(V.load(f), {"prompt": "김철수, SecondBrain", "ids": [3], "fixes": [{"id": 1, "wrong": "a b", "right": "c"}]})
            self.assertEqual(V.load(Path(tmp) / "none.json"), {"prompt": "", "fixes": [], "ids": []})

    def test_group_words_keeps_original(self):
        words = [dict(seg(0, 90, "오늘"), speaker=None), dict(seg(100, 190, "SecondBrain으로", original="세컨 브레인으로"), speaker=None),
                 dict(seg(200, 290, "정리"), speaker=None), dict(seg(5000, 5090, "끝"), speaker=None)]
        self.assertEqual([(s["text"], s.get("original")) for s in SP.group_words(words)],
                         [("오늘 SecondBrain으로 정리", "오늘 세컨 브레인으로 정리"), ("끝", None)])


class AnalyzeHintsTests(unittest.TestCase):
    """analyze.py: /vocab/pick 본문·힌트 파일, 실패하면 힌트 없이, /vocab/used (가짜 서버)"""
    def setUp(self):
        import analyze, bundles as B
        self.A, self.B = analyze, B
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = analyze.CHOSEN, analyze.VOCAB_OFF
        analyze.CHOSEN = analyze.Chosen(P.load(WC2), 1, dict(SETTINGS))
        analyze.VOCAB_OFF = False
        self.calls, self.reply = [], None

    def tearDown(self):
        self.A.CHOSEN, self.A.VOCAB_OFF = self.old
        self.tmp.cleanup()

    def bundle(self, source, name):
        return self.B.Bundle(source, [self.B.Item(source, name, 0, 1000, Path("C:/x") / name)])

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply

    def test_pick_and_file(self):
        self.reply = {"hints": [{"id": 3, "kind": "name", "text": "김철수"}, {"id": 8, "kind": "term", "text": "SecondBrain"}],
                      "prompt": "김철수, SecondBrain", "fixes": [{"id": 12, "wrong": "세컨브레인", "text": "SecondBrain"}], "skipped": 0}
        path = self.A.bundle_hints(self.call, self.bundle("call", "통화 김철수_261114_130500.m4a"), self.root)
        self.assertEqual(self.calls, [("POST", "/api/vocab/pick", {"caller": "김철수", "max": 20, "maxChars": 120})])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"prompt": "김철수, SecondBrain", "ids": [3, 8], "fixes": [{"id": 12, "wrong": "세컨브레인", "right": "SecondBrain"}]})
        self.assertEqual(V.load(path)["fixes"][0]["right"], "SecondBrain")   # 파이프라인이 읽는 모양
        self.reply = {"hints": [], "prompt": "", "fixes": [], "skipped": 0}
        self.assertIsNone(self.A.bundle_hints(self.call, self.bundle("phone", "20261114-130500.m4a"), self.root))   # 고를 것 없음
        self.assertEqual(self.calls[-1][2], {"max": 20, "maxChars": 120})   # 통화가 아니면 caller 없음

    def test_use_hints_off_and_server_fail(self):
        self.A.CHOSEN.settings["useHints"] = False
        self.assertIsNone(self.A.bundle_hints(self.call, self.bundle("phone", "a.m4a"), self.root))
        self.assertEqual(self.calls, [])
        self.A.CHOSEN.settings["useHints"] = True
        self.reply = OSError("HTTP Error 404")
        self.assertIsNone(self.A.bundle_hints(self.call, self.bundle("phone", "a.m4a"), self.root))
        self.assertIsNone(self.A.bundle_hints(self.call, self.bundle("phone", "b.m4a"), self.root))
        self.assertEqual(len(self.calls), 1)   # 한 번 실패하면 이번 실행은 다시 묻지 않음

    def test_mark_used(self):
        self.reply = {"updated": 2}
        self.A.mark_used(self.call, set())
        self.A.mark_used(self.call, {12, 3})
        self.assertEqual(self.calls, [("POST", "/api/vocab/used", {"ids": [3, 12]})])
        self.reply = OSError("down")
        self.A.mark_used(self.call, {1})   # 실패해도 예외 없음


class ServerVersionTests(unittest.TestCase):
    """--asr-model 등으로 새 설정 버전을 쓸 때 서버 목록의 번호도 본다"""
    def setUp(self):
        import analyze, asrmodels as M
        self.A, self.M = analyze, M
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "models").mkdir()
        for n in ("ggml-batisay-ko-turbo-q8_0.bin", "ggml-large-v3-turbo.bin"): (self.root / "models" / n).write_bytes(b"x")
        self.old = M.ANALYSIS, M.SETTINGS, M.LEGACY_SETTINGS
        M.ANALYSIS = M.SETTINGS = M.LEGACY_SETTINGS = self.root
        self.pipes = P.discover([HERE / "pipelines"])
        self.settings = self.root / "pipeline-settings"

    def tearDown(self):
        self.M.ANALYSIS, self.M.SETTINGS, self.M.LEGACY_SETTINGS = self.old
        self.tmp.cleanup()

    def test_server_versions(self):
        p = self.pipes["whisper-cpp@1"]
        rows = [{"name": "whisper-cpp", "version": "1", "settingsVersion": 4}, {"name": "whisper-cpp", "version": "2", "settingsVersion": 9},
                {"name": "whisper-cpp", "version": "1", "settingsVersion": 2}, {"bad": True}, "x"]
        self.assertEqual(P.server_versions(rows, p), [2, 4])
        self.assertEqual(P.server_versions(None, p), [])

    def test_asr_model_number_above_server(self):
        asked = []
        server = lambda p: asked.append(p.key) or [1, 4]
        c = self.A.choose_pipeline(None, None, "large-v3-turbo", self.pipes, self.settings, server=server)
        self.assertEqual(c.settings_version, 5)
        self.assertEqual(self.A.choose_pipeline(None, None, "large-v3-turbo", self.pipes, self.settings, server=server).settings_version, 5)   # 있으면 그대로
        self.assertEqual(self.A.choose_pipeline(None, None, "batisay-ko-turbo-q8_0", self.pipes, self.settings, server=server).settings_version, 1)
        self.assertEqual(asked, ["whisper-cpp@1"])   # 새로 쓸 때만 물음
        self.assertEqual(P.write(self.pipes["whisper-cpp@1"], {"asrModel": "x"}, self.settings, floor=2), 6)   # 이 PC가 더 크면 이 PC 기준

    def test_server_unreadable_falls_back(self):
        c = self.A.choose_pipeline(None, None, "large-v3-turbo", self.pipes, self.settings, server=lambda p: [])
        self.assertEqual(c.settings_version, 2)
        warns = []
        orig = self.A.api
        self.A.api = lambda: (_ for _ in ()).throw(OSError("404"))
        try:
            self.assertEqual(self.A.server_settings_versions(self.pipes["whisper-cpp@1"], warns.append), [])
        finally:
            self.A.api = orig
        self.assertIn("이 PC 기준", warns[0])


class CompareTests(unittest.TestCase):
    def test_parse_spec_and_measure(self):
        import compare as C
        self.assertEqual(C.parse_spec("whisper-cpp@2#3"), ("whisper-cpp@2", 3))
        self.assertEqual(C.parse_spec("whisper-cpp@1"), ("whisper-cpp@1", None))
        for bad in ("whisper-cpp#1", "whisper-cpp@1#0", "whisper-cpp@1#x"):
            with self.assertRaises(ValueError): C.parse_spec(bad)
        words = [dict(seg(i * 100, i * 100 + 90, w), speaker=None) for i, w in enumerate("오늘 시험 어땠어".split())]
        shown, m = C.measure(words, True)
        self.assertEqual([s["text"] for s in shown], ["오늘 시험 어땠어"])
        self.assertEqual((m["segments"], m["chars"], m["suspect"]), (1, 7, 0))


if __name__ == "__main__":
    unittest.main()
