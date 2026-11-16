"""python -m unittest test_pipelines (임시 폴더에서만, whisper는 돌리지 않음)"""
import importlib.util, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pipelines as P
import asrmodels as M

HERE = Path(__file__).parent
# 실행 약속만 보는 가짜 파이프라인: 설정의 text를 구간 하나로, --words면 단어마다. --hints면 첫 구간을 prompt로 바꾸고 ids를 vocabUsed로
FAKE = '''import argparse, json, sys
ap = argparse.ArgumentParser()
for k in ("--wav", "--out", "--settings"): ap.add_argument(k, required=True)
ap.add_argument("--words", action="store_true")
ap.add_argument("--hints")
a = ap.parse_args()
s = json.load(open(a.settings, encoding="utf-8"))
for line in s.get("stdout") or []: print(line, flush=True)
if s.get("stderr"): sys.stderr.write(s["stderr"]); sys.stderr.flush()
if s.get("fail"): raise SystemExit("일부러 실패")
words = s["text"].split() if a.words else [s["text"]]
segs = [{"startMs": i * 100, "endMs": i * 100 + 90, "text": w} for i, w in enumerate(words)] + [{"startMs": 0, "endMs": 1, "text": "  "}]
if s.get("suspect"): segs[0]["suspect"] = True
used = []
if a.hints:
    h = json.load(open(a.hints, encoding="utf-8"))
    segs[0]["original"], segs[0]["text"], used = segs[0]["text"], h["prompt"], h["ids"]
open(a.out, "w", encoding="utf-8").write(json.dumps({"segments": segs, "wav": a.wav, "vocabUsed": used}, ensure_ascii=False))
'''


def make(root: Path, name="fake", version="1", entry="run.py", **extra) -> Path:
    d = root / f"{name}@{version}"
    d.mkdir(parents=True)
    (d / "run.py").write_text(FAKE, encoding="utf-8")
    m = {"name": name, "version": version, "description": "시험", "autoRecommend": False, "entry": entry,
         "settings": {"text": {"type": "string", "default": "안녕 하세요", "label": "글"}, "fail": {"type": "bool", "default": False, "label": "실패"}}}
    m.update(extra)
    (d / "pipeline.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.user, self.repo, self.settings = self.root / "user", self.root / "repo", self.root / "settings"

    def tearDown(self): self.tmp.cleanup()

    def test_parse_key(self):
        self.assertEqual(P.parse_key("whisper-cpp@1"), ("whisper-cpp", "1"))
        self.assertEqual(P.parse_key("sense-voice@0.2"), ("sense-voice", "0.2"))
        for bad in ("whisper-cpp", "a@b@c", "@1", "a@", "a/b@1"):
            with self.assertRaises(ValueError): P.parse_key(bad)

    def test_discover_user_first_and_skips_broken(self):
        make(self.repo, "fake", "1", description="저장소")
        make(self.user, "fake", "1", description="사용자")
        make(self.repo, "fake", "2")
        make(self.repo, "other", "1", entry="none.py")          # entry 없음
        d = make(self.repo, "renamed", "1"); d.rename(self.repo / "renamed@9")   # 폴더 이름과 매니페스트가 다름
        (self.repo / "loose").mkdir()                            # 이름@버전 꼴이 아닌 폴더
        warns = []
        pipes = P.discover([self.user, self.repo, self.root / "none"], warn=warns.append)
        self.assertEqual(sorted(pipes), ["fake@1", "fake@2"])
        self.assertEqual(pipes["fake@1"].description, "사용자")
        self.assertEqual(len(warns), 2)

    def test_settings_write_once_and_find(self):
        p = P.load(make(self.repo))
        self.assertEqual(P.versions(p, self.settings), [])
        self.assertEqual(P.write(p, {"text": "가"}, self.settings), 1)
        self.assertEqual(P.write(p, {}, self.settings), 2)
        self.assertEqual(P.read(p, 1, self.settings), {"text": "가", "fail": False})   # 기본값 채움
        self.assertEqual(P.find(p, {"text": "안녕 하세요"}, self.settings), 2)
        self.assertEqual(P.find(p, {"text": "가", "fail": False}, self.settings), 1)
        self.assertIsNone(P.find(p, {"text": "나"}, self.settings))
        self.assertEqual(P.find_or_write(p, {"text": "나"}, self.settings), 3)
        self.assertEqual(P.find_or_write(p, {"text": "나"}, self.settings), 3)
        (P.settings_dir(p, self.settings) / "7.json").write_text("{깨짐", encoding="utf-8")
        (P.settings_dir(p, self.settings) / "0.json").write_text("{}", encoding="utf-8")   # 0은 버전 아님
        (P.settings_dir(p, self.settings) / "x.json").write_text("{}", encoding="utf-8")
        self.assertEqual(P.versions(p, self.settings), [1, 2, 3, 7])
        self.assertIsNone(P.find(p, {"text": "다"}, self.settings))   # 깨진 7은 건너뜀
        self.assertEqual(P.write(p, {"text": "다"}, self.settings), 8)
        self.assertEqual(json.loads((P.settings_dir(p, self.settings) / "1.json").read_text(encoding="utf-8"))["text"], "가")

    def test_seed_copies_only_missing(self):
        d = make(self.repo)
        (d / "seed-settings").mkdir()
        (d / "seed-settings" / "1.json").write_text('{"text": "씨앗1"}', encoding="utf-8")
        (d / "seed-settings" / "2.json").write_text('{"text": "씨앗2"}', encoding="utf-8")
        (d / "seed-settings" / "note.json").write_text('{}', encoding="utf-8")
        p = P.load(d)
        sd = P.settings_dir(p, self.settings); sd.mkdir(parents=True)
        (sd / "2.json").write_text('{"text": "사용자2"}', encoding="utf-8")
        self.assertEqual(P.seed(p, self.settings), [1])
        self.assertEqual(P.seed(p, self.settings), [])
        self.assertEqual(P.read(p, 1, self.settings)["text"], "씨앗1")
        self.assertEqual(P.read(p, 2, self.settings)["text"], "사용자2")   # 있는 것은 덮지 않음

    def test_listing_shape(self):
        p = P.load(make(self.repo, autoRecommend=True))
        P.write(p, {}, self.settings); P.write(p, {"text": "x"}, self.settings)
        [row] = P.listing({p.key: p}, self.settings)
        self.assertEqual(set(row), {"name", "version", "description", "autoRecommend", "settingsSchema", "settingsVersions", "latestSettingsVersion", "folder", "hints"})
        self.assertFalse(row["hints"])
        self.assertEqual((row["name"], row["version"], row["autoRecommend"]), ("fake", "1", True))
        self.assertEqual((row["settingsVersions"], row["latestSettingsVersion"]), ([1, 2], 2))
        self.assertEqual(row["settingsSchema"]["text"]["default"], "안녕 하세요")
        q = P.load(make(self.repo, "empty"))
        self.assertIsNone(P.listing({q.key: q}, self.settings)[0]["latestSettingsVersion"])

    def test_run_contract(self):
        p = P.load(make(self.repo))
        wav = self.root / "p0.wav"; wav.write_bytes(b"")
        work = self.root / "work"; work.mkdir()
        self.assertEqual(P.run(p, P.effective(p, {}), wav, work),
                         [{"startMs": 0, "endMs": 90, "speaker": None, "text": "안녕 하세요"}])   # 빈 글은 뺌
        words = P.run(p, P.effective(p, {"suspect": True}), wav, work, words=True)
        self.assertEqual([w["text"] for w in words], ["안녕", "하세요"])
        self.assertTrue(words[0]["suspect"]); self.assertNotIn("suspect", words[1])
        with self.assertRaisesRegex(RuntimeError, "일부러 실패"):
            P.run(p, P.effective(p, {"fail": True}), wav, work)

    def run_fake(self, **settings):
        """가짜 파이프라인을 돌려 (구간들, 받은 진행들)"""
        p = P.load(make(self.repo)) if not (self.repo / "fake@1").exists() else P.load(self.repo / "fake@1")
        wav = self.root / "p0.wav"; wav.write_bytes(b"")
        work = self.root / "work"; work.mkdir(exist_ok=True)
        got = []
        segs = P.run(p, P.effective(p, settings), wav, work, on_progress=got.append)
        return segs, got

    def test_run_progress_lines(self):
        base, none = self.run_fake()
        self.assertEqual(none, [])   # 진행을 안 내는 파이프라인도 그대로
        lines = ['@@progress {"percent": 12.5, "text": "조각 1/3"}', "그냥 로그 한 줄", '@@progress {"text": " 글만 "}', '@@progress {"percent": 250}',
                 '@@progress {깨진 JSON', '@@progress [1, 2]', '@@progress {"percent": "abc", "text": "x"}', '@@progress {"percent": true}', '@@progress {}',
                 '@@progress {"percent": -5}', '@@progress {"percent": 100, "text": "끝"}']
        segs, got = self.run_fake(stdout=lines)
        self.assertEqual(segs, base)   # 같은 입력에 같은 출력
        self.assertEqual(got, [{"percent": 12.5, "text": "조각 1/3"}, {"text": "글만"}, {"percent": 100.0}, {"text": "x"}, {"percent": 0.0}, {"percent": 100.0, "text": "끝"}])

    def test_run_progress_without_listener_and_big_stderr(self):
        p = P.load(make(self.repo))
        wav = self.root / "p0.wav"; wav.write_bytes(b"")
        work = self.root / "work"; work.mkdir()
        s = P.effective(p, {"stdout": ['@@progress {"percent": 1}'], "stderr": "로그\n" * 100000})   # 표준 오류가 가득 차도 멈추지 않는다
        self.assertEqual(P.run(p, s, wav, work)[0]["text"], "안녕 하세요")
        with self.assertRaises(RuntimeError) as e:
            P.run(p, {**s, "fail": True, "stderr": "원인 설명\n"}, wav, work, on_progress=lambda ev: None)
        self.assertIn("원인 설명", str(e.exception)); self.assertIn("종료 코드 1", str(e.exception))

    def test_parse_progress(self):
        self.assertEqual(P.parse_progress('@@progress {"percent": 3, "text": "a"}\n'),{"percent": 3.0, "text": "a"})
        self.assertIsNone(P.parse_progress('{"percent": 3}'))   # 접두어 없음
        self.assertIsNone(P.parse_progress('@@progress {"percent": NaN}'))
        self.assertIsNone(P.parse_progress("@@progress"))
        self.assertEqual(P.parse_progress('@@progress {"percent": 5, "text": 7}'), {"percent": 5.0})   # 문자열이 아닌 text는 버리고 percent만

    def test_throttle(self):
        t = [0.0]
        g = P.Throttle(0.25, clock=lambda: t[0])
        got = []
        for now in (0.0, 0.1, 0.24, 0.25, 0.3, 0.6, 0.9):
            t[0] = now; got.append(g.ok())
        self.assertEqual(got, [True, False, False, True, False, True, True])   # 초당 4번

    def test_analyze_reporter(self):
        import analyze, contextlib, io
        old = analyze.PROGRESS
        try:
            analyze.PROGRESS = False
            self.assertIsNone(analyze.transcribe_reporter(1, 2))
            analyze.PROGRESS = True
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rep = analyze.transcribe_reporter(2, 3)
                rep({"percent": 40.0, "text": "조각 2/5"}); rep({"percent": 41.0}); analyze.transcribe_reporter(3, 3)({"text": "글"})
        finally:
            analyze.PROGRESS = old
        self.assertEqual([json.loads(l[len("@@progress "):]) for l in out.getvalue().splitlines()],
                         [{"event": "step", "stage": "transcribe", "file": 2, "files": 3, "percent": 40.0, "text": "조각 2/5"},   # 41.0은 너무 잦아 거름
                          {"event": "step", "stage": "transcribe", "file": 3, "files": 3, "text": "글"}])   # percent 없으면 생략

    def test_run_hints_only_for_hints_pipelines(self):
        wav = self.root / "p0.wav"; wav.write_bytes(b"")
        work = self.root / "work"; work.mkdir()
        hints = self.root / "hints.json"
        hints.write_text(json.dumps({"prompt": "김철수, SecondBrain", "fixes": [], "ids": [3, 8]}, ensure_ascii=False), encoding="utf-8")
        plain = P.load(make(self.repo))
        used = set()
        self.assertEqual(P.run(plain, P.effective(plain, {}), wav, work, hints=hints, used=used)[0]["text"], "안녕 하세요")   # hints 아니면 안 넘김
        self.assertEqual(used, set())
        p = P.load(make(self.repo, "hinted", hints=True))
        self.assertTrue(p.hints and P.wants_hints(p, {}) and P.wants_hints(p, {"useHints": True}))
        self.assertFalse(P.wants_hints(p, {"useHints": False}) or P.wants_hints(plain, {}))
        [seg] = P.run(p, P.effective(p, {}), wav, work, hints=hints, used=used)
        self.assertEqual((seg["text"], seg["original"]), ("김철수, SecondBrain", "안녕 하세요"))
        self.assertEqual(used, {3, 8})
        self.assertNotIn("original", P.run(p, P.effective(p, {}), wav, work)[0])   # 힌트 파일 없으면 그대로

    def test_whisper_cpp_1_manifest(self):
        pipes = P.discover([HERE / "pipelines"])
        p = pipes["whisper-cpp@1"]
        self.assertEqual(p.entry.name, "transcribe.py")
        self.assertEqual(P.defaults(p), {"asrModel": M.DEFAULT})
        self.assertEqual(p.schema["asrModel"]["type"], "whisper-model")
        P.seed(p, self.settings)
        self.assertEqual(P.read(p, 1, self.settings), {"asrModel": "batisay-ko-turbo-q8_0"})   # 기존 결과 대응표

    def test_whisper_cpp_1_segments(self):
        spec = importlib.util.spec_from_file_location("wc1", HERE / "pipelines" / "whisper-cpp@1" / "transcribe.py")
        wc1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(wc1)
        data = {"transcription": [
            {"offsets": {"from": 0, "to": 900}, "text": " 안녕하세요 "},
            {"offsets": {"from": 900, "to": 1500}, "text": "시청해 주셔서 감사합니다"},
            {"offsets": {"from": 1500, "to": 1600}, "text": " - "},
            {"offsets": {"from": 1600, "to": 1700}, "text": "한글자막 by 누구"},
            {"offsets": {"from": 1700, "to": 2500}, "text": "네."}]}
        self.assertEqual(wc1.segments(data), [{"startMs": 0, "endMs": 900, "text": "안녕하세요"}, {"startMs": 1700, "endMs": 2500, "text": "네."}])

    def load_wc1(self):
        spec = importlib.util.spec_from_file_location("wc1", HERE / "pipelines" / "whisper-cpp@1" / "transcribe.py")
        wc1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(wc1)
        return wc1

    def test_whisper_cpp_1_run_whisper(self):
        wc1 = self.load_wc1()
        child = "\n".join(["import sys", "e = lambda t: print(t, file=sys.stderr, flush=True)",
                           "e('whisper_print_progress_callback: progress =   5%')", "e('아무 로그')",
                           "e('whisper_print_progress_callback: progress = 100%')", "e('원인')", "sys.exit(3)"])
        got = []
        code, tail = wc1.run_whisper([sys.executable, "-c", child], got.append)
        self.assertEqual((code, got), (3, [5.0, 100.0]))
        self.assertEqual(tail, "아무 로그\n원인\n")   # 진행 줄은 오류 꼬리에서 뺌
        self.assertEqual(wc1.run_whisper([sys.executable, "-c", "pass"])[0], 0)   # 듣는 쪽이 없어도 됨

    def test_whisper_cpp_1_same_output_with_progress(self):
        import contextlib, io, os
        wc1 = self.load_wc1()
        tools = self.root / "tools"
        for p in ("src/whisper.cpp/build/bin/whisper-cli.exe", "models/ggml-batisay-ko-turbo-q8_0.bin", "models/ggml-silero-v5.1.2.bin"):
            (tools / p).parent.mkdir(parents=True, exist_ok=True); (tools / p).write_bytes(b"")
        (self.root / "s.json").write_text("{}", encoding="utf-8")
        data = {"transcription": [{"offsets": {"from": 0, "to": 900}, "text": " 안녕하세요 "}, {"offsets": {"from": 900, "to": 1500}, "text": "시청해 주셔서 감사합니다"},
                                  {"offsets": {"from": 1700, "to": 2500}, "text": "네."}]}
        cmds = []

        def fake(cmd, on_percent=None):
            cmds.append(cmd)
            Path(cmd[cmd.index("-of") + 1] + ".json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            for pct in (5, 50, 100):
                on_percent(float(pct))
            return 0, ""

        old, env, argv = wc1.run_whisper, dict(os.environ), sys.argv
        wc1.run_whisper = fake
        os.environ["SECONDBRAIN_ANALYSIS_TOOLS"] = str(tools)
        try:
            for words in (False, True):
                out = self.root / f"o{int(words)}.json"
                sys.argv = ["t", "--wav", "in.wav", "--out", str(out), "--settings", str(self.root / "s.json")] + (["--words"] if words else [])
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    wc1.main()
                self.assertEqual(json.loads(out.read_text(encoding="utf-8")),   # 결과는 진행도 없던 때와 같다
                                 {"segments": [{"startMs": 0, "endMs": 900, "text": "안녕하세요"}, {"startMs": 1700, "endMs": 2500, "text": "네."}]})
                self.assertEqual([P.parse_progress(l) for l in buf.getvalue().splitlines()], [{"percent": 0.0}, {"percent": 5.0}, {"percent": 50.0}, {"percent": 100.0}])
        finally:
            wc1.run_whisper, sys.argv = old, argv
            os.environ.clear(); os.environ.update(env)
        # 명령줄은 -pp만 더해졌다(나머지 인자·순서는 2026-11-16 그대로)
        exe, model, vad = (str(tools / x) for x in ("src/whisper.cpp/build/bin/whisper-cli.exe", "models/ggml-batisay-ko-turbo-q8_0.bin", "models/ggml-silero-v5.1.2.bin"))
        want = lambda stem: [exe, "-m", model, "-l", "ko", "-f", "in.wav", "-oj", "-of", str(self.root / stem), "-np", "-pp", "--vad", "-vm", vad, "-vsd", "500", "-vp", "200"]
        self.assertEqual(cmds, [want("o0.whisper"), want("o1.whisper") + ["-ml", "1", "-sow"]])


class ChooseTests(unittest.TestCase):
    """analyze.choose_pipeline: --pipeline·--settings-version·--asr-model(데스크톱 호환)"""
    def setUp(self):
        import analyze
        self.A = analyze
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"; self.models.mkdir()
        for n in ("ggml-batisay-ko-turbo-q8_0.bin", "ggml-large-v3-turbo.bin"):
            (self.models / n).write_bytes(b"x")
        self.old = M.ANALYSIS, M.SETTINGS, M.LEGACY_SETTINGS
        M.ANALYSIS = M.SETTINGS = M.LEGACY_SETTINGS = self.root
        self.settings = self.root / "pipeline-settings"
        make(self.root / "repo")
        self.pipes = P.discover([HERE / "pipelines", self.root / "repo"])

    def tearDown(self):
        M.ANALYSIS, M.SETTINGS, M.LEGACY_SETTINGS = self.old
        self.tmp.cleanup()

    def choose(self, pipeline=None, n=None, asr=None):
        return self.A.choose_pipeline(pipeline, n, asr, self.pipes, self.settings)

    def test_legacy_default_uses_seed(self):
        c = self.choose()
        self.assertEqual(c.ref(), {"name": "whisper-cpp", "version": "1", "settingsVersion": 1})
        self.assertEqual(c.settings, {"asrModel": "batisay-ko-turbo-q8_0"})

    def test_legacy_settings_json_and_asr_model(self):
        (self.root / "settings.json").write_text('{"asrModel": "large-v3-turbo"}', encoding="utf-8")
        self.assertEqual(self.choose().settings_version, 2)                     # 새 설정 버전을 씀
        self.assertEqual(self.choose(asr="batisay-ko-turbo-q8_0").settings_version, 1)
        self.assertEqual(self.choose("whisper-cpp@1", asr="ggml-large-v3-turbo.bin").settings_version, 2)
        self.assertEqual(self.choose("whisper-cpp@1").settings_version, 2)      # 최신
        self.assertEqual(self.choose("whisper-cpp@1", 1).settings["asrModel"], "batisay-ko-turbo-q8_0")

    def test_plan_does_not_write(self):
        with self.assertRaisesRegex(ValueError, "아직 없음"):
            self.A.choose_pipeline(None, None, "large-v3-turbo", self.pipes, self.settings, create=False)
        with self.assertRaisesRegex(ValueError, "아직 없음"):
            self.A.choose_pipeline("fake@1", None, None, self.pipes, self.settings, create=False)
        self.assertEqual(P.versions(self.pipes["whisper-cpp@1"], self.settings), [1])   # 씨앗만
        self.assertEqual(P.versions(self.pipes["fake@1"], self.settings), [])

    def test_errors(self):
        for args, msg in (((None, None, "large-v3"), "전사 모델 없음"), (("nope@1", None, None), "파이프라인 없음"),
                          (("fake@1", None, "large-v3-turbo"), "--asr-model은"), (("whisper-cpp@1", 1, "large-v3-turbo"), "함께"),
                          (("whisper-cpp@1", 5, None), "설정 버전 5"), (("whisper-cpp", None, None), "<이름>@<버전>")):
            with self.assertRaisesRegex(ValueError, msg): self.choose(*args)
        self.assertEqual(P.versions(self.pipes["whisper-cpp@1"], self.settings), [1])   # 없는 모델로 설정 버전을 쓰지 않음

    def test_other_pipeline_latest_or_defaults(self):
        c = self.choose("fake@1")
        self.assertEqual((c.settings_version, c.settings), (1, {"text": "안녕 하세요", "fail": False}))
        P.write(self.pipes["fake@1"], {"text": "새"}, self.settings)
        self.assertEqual(self.choose("fake@1").settings["text"], "새")


class RedoTests(unittest.TestCase):
    """analyze --redo-from(데스크톱 전체 재전사)과 미전사 판단"""
    def setUp(self):
        import analyze, argparse, types
        self.A, self.ns = analyze, types.SimpleNamespace
        self.argparse = argparse
        # 서버 GET /api/recordings/analyzed?device[&pipeline&version&settingsVersion]
        self.analyzed = {"": ["a1", "a2", "b1", "c1"], "whisper-cpp|1|1": ["a1", "a2"], "whisper-cpp|1|2": ["c1"]}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        from urllib.parse import urlparse, parse_qs
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        self.assertEqual(q["device"], "phone")
        return self.analyzed.get("|".join(q[k] for k in ("pipeline", "version", "settingsVersion")) if "pipeline" in q else "", [])

    def bundles(self):
        it = lambda rel: self.ns(rel=rel)
        return {k: self.ns(items=[it(x) for x in v]) for k, v in {"A": ["a1", "a2"], "B": ["b1", "b2"], "C": ["c1"], "D": ["d1"]}.items()}

    def pick(self, redo=False, redo_from=None):
        want = self.A.wanted(self.ns(redo=redo, redo_from=redo_from), self.get, "phone")
        return sorted(k for k, b in self.bundles().items() if want(b))

    def test_parse(self):
        self.assertEqual(self.A.redo_entry("whisper-cpp@1#2"), ("whisper-cpp", "1", 2))
        self.assertEqual(self.A.redo_entry("sense-voice@0.2#10"), ("sense-voice", "0.2", 10))
        for bad in ("whisper-cpp@1", "whisper-cpp#1", "whisper-cpp@1#0", "whisper-cpp@1#x", "a@b@c#1"):
            with self.assertRaises(self.argparse.ArgumentTypeError): self.A.redo_entry(bad)

    def test_selection(self):
        self.assertEqual(self.pick(), ["B", "D"])                       # 미전사 = 파일 하나라도 어느 결과에도 없음
        self.assertEqual(self.pick(redo=True), ["A", "B", "C", "D"])    # 예전 --redo
        self.assertEqual(self.pick(True, [("whisper-cpp", "1", 1)]), ["A"])
        self.assertEqual(self.pick(True, [("whisper-cpp", "1", 1), ("whisper-cpp", "1", 2)]), ["A", "C"])
        self.assertEqual(self.pick(True, [("nope", "1", 1)]), [])
        self.assertIn("&pipeline=nope&version=1&settingsVersion=1", self.calls[-1])


if __name__ == "__main__":
    unittest.main()
