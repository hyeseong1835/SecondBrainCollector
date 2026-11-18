"""전사 파이프라인 whisper-lite@1(2026-11-17): whisper-cpp@2의 반복 억제는 그대로 두고 계산을 줄인 것. 공개 뒤에는 고치지 않는다(바꾸면 whisper-lite@2).
계획: 음성 전사 개선 계획.md "가벼운 파이프라인". 가벼움의 우선순위는 ① 전사 시간 ② GPU 점유 ③ 메모리.

  python transcribe.py --wav W --out O.json --settings S.json [--words] [--hints H.json] [--run-options R.json]
  → O.json = {"segments": [{startMs, endMs, text, suspect?, original?}], "stats": {...}, "vocabUsed": [id]}

@2와 같은 것: 앞 글 힌트 끔(-mc 0), 30초 안팎 조각마다 따로 전사(조각마다 whisper.cpp Silero VAD), 조각 품질 검사(quality.py, @2를 베낌),
  걸린 조각만 온도를 올려 한 번 다시, 그래도 걸리면 suspect, 반복 필터, 무음 환각 문장 필터, 단어장 힌트(vocab.py, @2를 베낌), --words(-ml 1 -sow).
@2와 다른 것(설정으로 바꿀 수 있음):
- greedy 디코딩(beamSize 1, bestOf 1). @2는 whisper 기본(빔 5, 다시 돌릴 때 후보 5).
- whisper 자체 온도 올려 다시(fallback) 끔(-nf). @2는 켬(디코딩이 흔들리면 온도 0.2씩 올려 최대 5번 다시 → 소음 구간에서 시간이 크게 늘어남).
  반복은 품질 검사 + 한 번 다시(retryTemperature) + 반복 필터가 맡는다.
- useGpu = false면 -ng(CPU로만, GPU를 비운다). threads = -t. VAD 문턱·최소 말 길이(-vt·-vspd)도 설정으로.
- vadEngine = "sherpa"(기본 꺼짐 = "whisper"): sherpa-onnx Silero VAD(models/silero_vad.onnx → models/sensevoice/silero_vad.onnx)를 녹음 전체에 한 번 돌려
  말소리 구간을 찾고(앞뒤 vadPadMs), 말소리만 0.1초 무음을 끼워 chunkSec 이하로 이어 붙여 조각을 만든다. whisper-cli는 --vad 없이 돈다.
  구간 시각은 조각 안 시각 → 이어 붙인 표로 녹음 시각(끼운 무음은 앞 구간 끝으로). 다시 돌리기도 같은 조각 wav라 VAD를 다시 하지 않는다.
  sherpa-onnx나 모델이 없으면 경고하고 "whisper"로 돈다.
  2026-11-17 실측(5분 조각): whisper.cpp 내장 VAD는 CPU 4스레드 고정이라 느리다(CPU 전용 실행에서 76초). sherpa는 같은 5분에 약 2초.
  그러나 chunkSec 30으로 말소리를 꽉 채우면 whisper(greedy, -nf)가 말을 크게 건너뛰어(글자 611 → 208) 기본으로 쓰지 않는다. 더 짧은 chunkSec은 아직 안 잼.
- 진행도: 표준 출력에 "@@progress {"percent", "text"}"(계획 "파이프라인 진행도" 약속). whisper-cli의 "processing '<조각>'" 줄(표준 오류)로 센다.
  첫 전사가 0~90%, 다시 돌리기가 90~100%.
- stats.whisper: whisper-cli가 찍는 시간(인코더·디코더·VAD ms·횟수, 실행마다 더함), startup = 실행부터 첫 조각까지(모델 올리기·GPU 준비).
  stats.passSec: 첫 전사·다시 돌리기 걸린 초. stats.vad: {engine, sec, spans, speechSec}(sherpa일 때 VAD 시간·구간 수·말소리 길이).
- 도구·모델 폴더: 환경 변수 SECONDBRAIN_ANALYSIS_TOOLS(analyze.py가 넘김) → %LOCALAPPDATA%/SecondBrain/analysis
- 동시 실행(2026-11-17 KST, 결과를 바꾸지 않아 같은 버전): 실행 옵션 parallel(--run-options, 매니페스트 runOptions 기본 2, 설정 버전에 들어가지 않는다.
  파일이 없으면 1)만큼 whisper-cli를 동시에 띄운다. 조각을 작업자마다 번갈아 나누고(조각·인자가 같으니 조각마다 결과는 같다) 작업자마다 BATCH씩.
  useGpu = false(CPU로만, seed 2)면 늘 1: 프로세스마다 -t 스레드가 같은 CPU를 다투고 메울 GPU 빈틈도 없어서.
  하나라도 실패하면 나머지를 죽이고 "whisper 실패". 진행도 = 시작한 조각 수 - 지금 조각을 돌고 있는 작업자 수(parallel 1이면 전과 같다).
  stats.parallel = 실제로 쓴 수. stats.whisper의 ms는 프로세스마다 더한 값이라 parallel > 1이면 걸린 시간(passSec)보다 클 수 있다.
- 자동 동시 실행(2026-11-18 KST, 결과를 바꾸지 않아 같은 버전): parallel = "auto"(새 기본) 또는 고정 수, parallelMax·yield(autopar.py 머리말, whisper-cpp@2와 같은 파일).
  번갈아 나누기 대신 공유 대기열(autopar.Pool)에서 작업자가 묶음씩 가져가고 조정기가 처리량·VRAM·GPU·CPU·메모리로 작업자 수를 바꾼다.
  CPU로만(useGpu = false): 고정 수는 예전처럼 늘 1. 자동이면 프로세스 수만 고른다(상한 = 논리 코어 ÷ threads, GPU 양보 없음).
  threads(-t)는 설정이라 바꾸지 않는다(스레드 수가 바뀌면 부동소수 합 순서가 달라져 결과가 바뀔 수 있다).
  stats.parallel = 실행 옵션 값("auto" 또는 수), stats.workers = 풀 통계(다시 돌리기는 workers.retry). @@progress에 "workers"와 글 " · 작업자 N".
  진행도의 '시작한 조각 수'는 급히 줄이며 되돌린 조각을 다시 세지 않도록 조각 수로 자른다.
- 여러 입력(2026-11-18 KST, 결과를 바꾸지 않아 같은 버전): 매니페스트 multiInput, --inputs I.json(pipelines.py 머리말). 입력마다 조각 계획·품질 검사·다시 돌리기 판정·고침은 따로 하고,
  whisper-cli만 (단어 단위, 프롬프트)가 같은 입력끼리 한 작업자 풀에 넣는다(조각 이름 앞에 i000_). 짧은 녹음 여러 개에서 모델 올리기·python 띄우기가 파일마다가 아니라 한 번.
  다시 돌리기는 모든 입력의 걸린 조각을 무리마다 한 번에. 한 입력의 wav가 깨지면 그 입력만 {"error"}. 진행도 percent·"조각 i/m"은 모든 입력 기준, 글 끝에 "(파일 N개)".
  한 개 모드(--wav)는 같은 길(run_inputs에 입력 하나)로 돌아 전과 같다(시험: test_multi_input 입력마다 하나씩 돌린 것과 같은 구간).
"""
import argparse, collections, json, os, re, subprocess, sys, threading, time, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import autopar as A
import quality as Q
import vocab as V

RATE = 16000
FRAME_MS = 100
BATCH = A.BATCH   # whisper-cli 한 번에 넘기는 조각 수(명령줄 길이 제한)
GAP_MS = 100   # sherpa VAD 조각에서 말소리 구간 사이에 끼우는 무음
VAD_ONNX = ("silero_vad.onnx", "sensevoice/silero_vad.onnx")   # models/ 아래 sherpa-onnx용 Silero VAD(앞이 우선)
PROCESSING = re.compile(r"processing '([^']+)'")
TIMING = re.compile(r"whisper_print_timings:\s+(\w+) time =\s*([\d.]+) ms(?: /\s*(\d+) runs)?")
VAD_TIME = re.compile(r"vad time =\s*([\d.]+) ms")   # 실행 하나 안에서 쌓인 값(마지막 것이 그 실행의 합)
GPU = re.compile(r"whisper_backend_init_gpu: using (\S+) backend")
VULKAN = re.compile(r"ggml_vulkan: (\d+) = ([^(]+)")   # Vulkan 장치 번호 = 이름(Vulkan0이 RX 6600인지 Iris Xe인지)


def tools_dir() -> Path:
    if os.environ.get("SECONDBRAIN_ANALYSIS_TOOLS"): return Path(os.environ["SECONDBRAIN_ANALYSIS_TOOLS"])
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "SecondBrain" / "analysis"


def progress(percent: float, text: str, workers: int | None = None):
    ev = {"percent": round(percent, 1), "text": text + (f" · 작업자 {workers}" if workers else "")}
    if workers: ev["workers"] = workers
    print("@@progress " + json.dumps(ev, ensure_ascii=False), flush=True)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"16kHz 모노 16비트 wav가 아님: {path}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(path: Path, samples: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(samples.astype(np.int16).tobytes())


def frame_energy(samples: np.ndarray) -> np.ndarray:
    """0.1초마다 평균 제곱(마지막 덜 찬 칸 포함)"""
    n = RATE * FRAME_MS // 1000
    pad = (-len(samples)) % n
    x = np.concatenate([samples.astype(np.float32), np.zeros(pad, np.float32)]) if pad else samples.astype(np.float32)
    return (x.reshape(-1, n) ** 2).mean(axis=1) if len(x) else np.zeros(0)


def plan_chunks(energy, total_ms: int, min_ms: int, max_ms: int) -> list:
    """[(시작 ms, 끝 ms)]. 남은 길이가 max_ms를 넘으면 시작+min_ms ~ 시작+max_ms에서 가장 조용한 칸의 가운데에서 자른다."""
    out, start = [], 0
    while total_ms - start > max_ms:
        lo, hi = (start + min_ms) // FRAME_MS, (start + max_ms) // FRAME_MS
        window = energy[lo:hi]
        cut = (lo + int(np.argmin(window))) * FRAME_MS + FRAME_MS // 2 if len(window) else start + max_ms
        out.append((start, cut)); start = cut
    if total_ms > start: out.append((start, total_ms))
    return out


def find_vad_onnx(tools: Path) -> Path | None:
    return next((tools / "models" / n for n in VAD_ONNX if (tools / "models" / n).exists()), None)


def speech_spans(samples: np.ndarray, model: Path, settings: dict) -> list:
    """sherpa-onnx Silero VAD를 녹음 전체에 한 번 → 말소리 [(시작 ms, 끝 ms)]. 앞뒤로 vadPadMs를 늘리고 겹치면 합친다(whisper.cpp -vp와 같은 뜻)"""
    import sherpa_onnx
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = str(model)
    c.silero_vad.threshold = float(settings["vadThreshold"])
    c.silero_vad.min_silence_duration = settings["vadMinSilenceMs"] / 1000
    c.silero_vad.min_speech_duration = settings["vadMinSpeechMs"] / 1000
    c.silero_vad.max_speech_duration = float(settings["chunkSec"])
    c.silero_vad.window_size = 512
    c.sample_rate, c.num_threads = RATE, 1
    vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=max(60, len(samples) // RATE + 1))
    x, raw = samples.astype(np.float32) / 32768, []

    def drain():
        while not vad.empty():
            raw.append((vad.front.start, vad.front.start + len(vad.front.samples))); vad.pop()
    for i in range(0, len(x), 512):
        vad.accept_waveform(x[i:i + 512]); drain()
    vad.flush(); drain()
    pad, total, out = int(settings["vadPadMs"]), len(samples) * 1000 // RATE, []
    for a, b in raw:
        a, b = max(0, a * 1000 // RATE - pad), min(total, b * 1000 // RATE + pad)
        if out and a <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], b))
        else: out.append((a, b))
    return out


def pack(spans: list, max_ms: int) -> list:
    """말소리 구간들 → 조각들 [[(시작 ms, 끝 ms)…]]. 조각 하나 = 구간을 GAP_MS 무음을 끼워 이은 것, 길이 max_ms 이하(긴 구간은 잘라 넣음)"""
    pieces = [(a + i, min(b, a + i + max_ms)) for a, b in spans for i in range(0, b - a, max_ms)]
    out, size = [], 0
    for a, b in pieces:
        n = b - a
        if out and size + GAP_MS + n <= max_ms:
            out[-1].append((a, b)); size += GAP_MS + n
        else:
            out.append([(a, b)]); size = n
    return out


def chunk_audio(samples: np.ndarray, pieces: list) -> np.ndarray:
    gap = np.zeros(RATE * GAP_MS // 1000, np.int16)
    parts = []
    for i, (a, b) in enumerate(pieces):
        if i: parts.append(gap)
        parts.append(samples[a * RATE // 1000:b * RATE // 1000])
    return np.concatenate(parts) if parts else np.zeros(0, np.int16)


def to_orig(pieces: list, t: int) -> int:
    """조각 wav 안의 시각(ms) → 녹음 시각. 끼운 무음·조각 끝을 넘는 시각은 그 앞 구간의 끝으로"""
    c = 0
    for i, (a, b) in enumerate(pieces):
        n = b - a
        if t < c + n + GAP_MS or i == len(pieces) - 1: return a + max(0, min(t - c, n))
        c += n + GAP_MS
    return 0


def chunk_segments(data: dict, pieces: list) -> list:
    """조각 하나의 whisper-cli -oj 결과 → 녹음 시각 구간들(깨진 글자·빈 글·무음 환각 문장 뺌, 조각 끝을 넘지 않게).
    pieces = 조각에 이어 붙인 녹음 구간들(whisper VAD면 조각 전체 하나)"""
    out = []
    for s in data.get("transcription", []):
        text = (s.get("text") or "").replace("�", "").strip()
        if not text or Q.HALLUCINATIONS.match(text): continue
        a = to_orig(pieces, int(s["offsets"]["from"]))
        b = max(a, to_orig(pieces, int(s["offsets"]["to"])))
        out.append({"startMs": a, "endMs": b, "text": text})
    return out


def base_args(exe: Path, model: Path, vad: Path | None, settings: dict, words: bool, prompt: str) -> list:
    """whisper-cli 공통 인자(조각 파일·온도는 따로). vad = whisper.cpp VAD 모델(None이면 VAD 없이: 이미 말소리만 이은 조각).
    -np는 쓰지 않는다: 조각마다 찍는 'processing' 줄로 진행도를 센다"""
    context = int(settings["maxContext"])
    if prompt: context = max(context, int(settings.get("hintContext") or 0))
    cmd = [str(exe), "-m", str(model), "-l", "ko", "-oj", "-mc", str(context),
           "-t", str(int(settings["threads"])), "-bs", str(int(settings["beamSize"])), "-bo", str(int(settings["bestOf"]))]
    if vad:
        cmd += ["--vad", "-vm", str(vad), "-vt", str(settings["vadThreshold"]), "-vspd", str(int(settings["vadMinSpeechMs"])),
                "-vsd", str(int(settings["vadMinSilenceMs"])), "-vp", str(int(settings["vadPadMs"]))]
    if not settings.get("fallback"): cmd.append("-nf")
    if not settings.get("useGpu", True): cmd.append("-ng")
    if words: cmd += ["-ml", "1", "-sow"]
    if prompt: cmd += ["--prompt", prompt]
    return cmd


def add_time(total: dict, name: str, ms: float, runs: int = 0):
    t = total.setdefault(name, {"ms": 0.0, "runs": 0})
    t["ms"] = round(t["ms"] + ms, 1)
    t["runs"] += runs


def add_timings(total: dict, line: str):
    """whisper_print_timings 줄 하나 → total[이름] = {ms, runs}에 더한다"""
    m = TIMING.search(line)
    if m: add_time(total, m.group(1), float(m.group(2)), int(m.group(3) or 0))


def split_names(names: list, n: int) -> list:
    """조각 이름들 → 작업자 n개 몫(번갈아 나눔). 2026-11-18부터 Whisper.run은 공유 대기열(autopar)을 쓰고 이것은 쓰지 않는다(시험·비교용으로 남김)"""
    n = max(1, int(n or 1))
    return [g for g in (names[i::n] for i in range(n)) if g]


def json_ok(path: Path) -> bool:
    """whisper-cli -oj 결과가 다 써졌는가(죽인 whisper-cli가 반쯤 쓴 파일은 아님)"""
    try:
        json.loads(path.read_bytes().decode("utf-8", errors="replace"))
        return True
    except (OSError, ValueError):
        return False


class Whisper:
    """whisper-cli 실행기. 조각마다 진행도를 내고 시간 통계를 모은다. 실행 옵션 run대로 동시에(autopar 공유 대기열, 통계·진행 콜백은 잠금 안에서).
    run = 실행 옵션 딕셔너리 또는 고정 수(시험). key = 처리량 표 열쇠(autopar.state_key), weights = {조각 이름: 오디오 ms}, on_workers(수) = 작업자 수가 바뀔 때"""
    def __init__(self, base: list, work: Path, run=1, key: str = "", cpu_only: bool = False, threads: int | None = None,
                 weights: dict | None = None, on_workers=None, **pool):
        self.base, self.work = base, work
        self.run_opts = run if isinstance(run, dict) or run is None else {"parallel": run}
        self.key, self.cpu_only, self.threads, self.weights, self.on_workers, self.pool = key, cpu_only, threads, weights, on_workers, pool
        self.timings, self.gpu, self.vulkan = {}, None, {}
        self.lock = threading.Lock()

    def device(self) -> str:
        if not self.gpu: return "CPU"
        m = re.fullmatch(r"Vulkan(\d+)", self.gpu)
        return self.vulkan.get(m.group(1), self.gpu) if m else self.gpu

    def run(self, names: list, extra: list, on_chunk) -> dict:
        """조각 wav 이름들 → 같은 이름의 .json(work 안, 상대 경로로 명령줄을 줄인다). 공유 대기열에서 작업자가 묶음씩(autopar).
        on_chunk(이번 실행에서 시작한 조각 수(조각 수로 자름), 지금 조각을 돌고 있는 작업자 수) = 'processing' 줄마다(잠금 안에서 한 번에 하나씩).
        고정 수에서 하나라도 실패하면 나머지 whisper-cli를 죽이고 sys.exit("whisper 실패: ..."). → 풀 통계"""
        lock, started, busy = self.lock, [0], {}

        def launch(w: int, batch: list, on_start):
            cmd = self.base + extra
            for n in batch: cmd += ["-f", n, "-of", Path(n).stem]
            tail, vad_ms, t0, first = collections.deque(maxlen=20), 0.0, time.time(), True
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=str(self.work))
            on_start(p)
            try:
                for line in p.stderr:
                    tail.append(line.rstrip())
                    if PROCESSING.search(line):
                        with lock:
                            if first:
                                add_time(self.timings, "startup", (time.time() - t0) * 1000); first = False
                            started[0] += 1; busy[w] = True
                            on_chunk(min(started[0], len(names)), sum(busy.values()))
                    elif m := VAD_TIME.search(line):
                        vad_ms = float(m.group(1))
                    elif "whisper_print_timings" in line:
                        with lock: add_timings(self.timings, line)
                    elif self.gpu is None and (m := GPU.search(line)):
                        with lock: self.gpu = self.gpu or m.group(1)
                    elif m := VULKAN.search(line):
                        with lock: self.vulkan[m.group(1)] = m.group(2).strip()
            finally:
                with lock: busy.pop(w, None)
                p.stderr.close()
            code = p.wait()
            if code == 0:
                with lock: add_time(self.timings, "vad", vad_ms)
            return code, "\n".join(tail)
        return A.run_pool(names, launch, lambda n: json_ok(self.work / f"{Path(n).stem}.json"), lambda n: (self.work / f"{Path(n).stem}.json").unlink(missing_ok=True),
                          self.weights, self.run_opts, key=self.key, cpu_only=self.cpu_only, threads=self.threads, on_workers=self.on_workers, **self.pool)


def badness(fails: list, m: dict) -> tuple:
    return (len(fails), m["phraseRepeat"], m["charRun"], m["compression"])


class Input:
    """입력 wav 하나(한 개 모드는 이것 하나, 여러 입력은 여럿). prefix = 조각 이름 앞(여러 입력이 한 작업 폴더를 같이 쓴다)"""
    def __init__(self, wav: Path, words: bool, hints: dict | None, settings: dict, prefix: str = "", error=None):
        self.wav, self.words, self.prefix, self.error = wav, words, prefix, error
        self.hints = hints if hints and settings.get("useHints", True) else None
        self.prompt = self.hints["prompt"] if self.hints else ""
        self.chunks, self.names, self.vad_stats = [], [], {}
        self.result, self.checks, self.bad = [], [], []
        self.w, self.pass_sec, self.w1 = None, [], {}


def setup(settings: dict, tools: Path) -> tuple:
    """→ (whisper-cli, 모델, whisper VAD 모델, VAD 엔진, sherpa VAD 모델). 없는 도구는 sys.exit"""
    exe = tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
    model = tools / "models" / f"ggml-{settings.get('asrModel') or 'batisay-ko-turbo-q8_0'}.bin"
    vad = tools / "models" / "ggml-silero-v5.1.2.bin"
    engine, onnx = settings.get("vadEngine", "sherpa"), find_vad_onnx(tools)
    if engine == "sherpa":
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            onnx = None
        if onnx is None:
            print(f"sherpa-onnx나 models/{'·'.join(VAD_ONNX)}가 없어 whisper.cpp VAD로 돈다", file=sys.stderr); engine = "whisper"
    for p in (exe, model) + ((vad,) if engine == "whisper" else ()):
        if not p.exists(): sys.exit(f"없음: {p}")
    return exe, model, vad, engine, onnx


def prepare(inp: Input, work: Path, settings: dict, engine: str, onnx):
    """wav → 조각 wav들(work/<prefix>cNNNN.wav)과 inp.chunks(조각마다 녹음 구간들)"""
    samples = read_wav(inp.wav)
    total = len(samples) * 1000 // RATE
    if engine == "sherpa":
        t = time.time()
        spans = speech_spans(samples, onnx, settings)
        inp.chunks = pack(spans, int(settings["chunkSec"] * 1000))
        inp.vad_stats = {"sec": round(time.time() - t, 2), "spans": len(spans), "speechSec": round(sum(b - a for a, b in spans) / 1000, 1)}
    else:
        inp.chunks = [[c] for c in plan_chunks(frame_energy(samples), total, int(settings["chunkMinSec"] * 1000), int(settings["chunkSec"] * 1000))]
    inp.names = []
    for k, pieces in enumerate(inp.chunks):
        name = f"{inp.prefix}c{k:04d}.wav"
        write_wav(work / name, chunk_audio(samples, pieces))
        inp.names.append(name)


def finish(inp: Input, settings: dict, temp: float) -> tuple:
    """조각 결과 → (구간들, 이 입력의 통계, 쓴 단어장 id들)"""
    out, n_suspect, n_dropped = [], 0, 0
    for segs, fails in zip(inp.result, inp.checks):
        segs = Q.clean(segs, int(settings["maxPhraseRepeat"]))
        if fails and segs:
            n_suspect += 1
            if settings.get("onSuspect") == "drop":
                n_dropped += 1; continue
            for s in segs: s["suspect"] = True
        out += segs
    used = list(inp.hints["ids"]) if inp.prompt else []
    n_fixed = 0
    if inp.hints and inp.hints["fixes"]:
        out, fixed = V.apply_fixes(out, inp.hints["fixes"])
        used += fixed; n_fixed = sum(1 for s in out if "original" in s)
    return out, {"chunks": len(inp.chunks), "retried": len(inp.bad) if temp > 0 else 0, "suspectChunks": n_suspect, "droppedChunks": n_dropped,
                 "failures": {f"{c[0][0] // 1000}s": f for c, f in zip(inp.chunks, inp.checks) if f}, "prompt": inp.prompt, "fixedSegments": n_fixed}, used


def run_inputs(inputs: list, work: Path, settings: dict, tools: Path, run: dict | None = None, isolate: bool = False) -> list:
    """입력들 → 입력마다 (구간들, 통계, 쓴 단어장 id들) 또는 그 입력의 예외(isolate일 때만: 한 입력의 wav가 깨져도 나머지는 계속).
    조각은 입력마다 따로 계획·검사·고침하고, whisper-cli만 (단어 단위, 프롬프트)가 같은 입력끼리 한 작업자 풀에 같이 넣는다(조각·인자가 같으니 조각마다 결과는 같다).
    진행도: 첫 전사 0~90%(모든 입력의 조각 수 기준), 다시 돌리기 90~100%. 입력이 둘 이상이면 글 끝에 (파일 N개)"""
    exe, model, vad, engine, onnx = setup(settings, tools)
    live = []
    for inp in inputs:
        if inp.error is not None: continue
        try:
            prepare(inp, work, settings, engine, onnx)
            live.append(inp)
        except Exception as e:
            if not isolate: raise
            inp.error = e
    cpu_only = not settings.get("useGpu", True)
    opts = A.parse(run, cpu_only)
    workers = [None if not opts["auto"] and opts["n"] == 1 else opts["n"]]   # 진행도에 낼 작업자 수(고정 1이면 내지 않아 전과 같다)
    files = f"(파일 {len(inputs)}개)" if len(inputs) > 1 else ""
    say = lambda pct, text: progress(pct, text + files, workers[0])
    groups = {}   # (단어 단위, 프롬프트) → 입력들(처음 나온 차례)
    for inp in live: groups.setdefault((inp.words, inp.prompt), []).append(inp)
    for (words, prompt), group in groups.items():
        w = Whisper(base_args(exe, model, vad if engine == "whisper" else None, settings, words, prompt), work, run or {},
                    key=A.state_key(Path(__file__).parent.name, settings, words), cpu_only=cpu_only, threads=int(settings["threads"]),
                    weights={n: sum(b - a for a, b in pieces) for inp in group for n, pieces in zip(inp.names, inp.chunks)},
                    on_workers=(lambda n: workers.__setitem__(0, n)) if opts["auto"] or opts["n"] > 1 else None)
        for inp in group: inp.w = w
    # whisper-cli가 한글 한 글자를 토큰 경계에서 잘라 깨진 UTF-8을 낼 때가 있다(2026-11-16 단어 단위 실측). 깨진 바이트는 버린다
    load = lambda inp, k: chunk_segments(json.loads((work / f"{Path(inp.names[k]).stem}.json").read_bytes().decode("utf-8", errors="replace")), inp.chunks[k])
    m = sum(len(inp.chunks) for inp in live)
    say(0, f"조각 0/{m}")
    base = 0
    for group in groups.values():
        names = [n for inp in group for n in inp.names]
        t0 = time.time()
        w1 = group[0].w.run(names, [], lambda i, busy, base=base: say(90 * max(0, base + i - busy) / m, f"조각 {base + i}/{m}")) or {}
        sec = round(time.time() - t0, 1)
        for inp in group:
            inp.w1, inp.pass_sec = w1, [sec]
            inp.result = [load(inp, k) for k in range(len(inp.chunks))]
            inp.checks = [Q.failures(Q.metrics(segs), settings) for segs in inp.result]
            inp.bad = [k for k, f in enumerate(inp.checks) if f]
        base += len(names)
    temp = float(settings.get("retryTemperature") or 0)
    if temp > 0:
        r, rbase = sum(len(inp.bad) for inp in live), 0
        for group in groups.values():
            again = [inp for inp in group if inp.bad]
            if not again: continue
            for inp in again:
                for k in inp.bad: (work / f"{Path(inp.names[k]).stem}.json").unlink(missing_ok=True)
            names = [inp.names[k] for inp in again for k in inp.bad]
            t1 = time.time()
            w2 = group[0].w.run(names, ["-tp", str(temp)], lambda i, busy, rbase=rbase: say(90 + 10 * max(0, rbase + i - busy) / r, f"다시 돌리는 중 {rbase + i}/{r}"))
            sec = round(time.time() - t1, 1)
            for inp in group:
                inp.w1 = {**inp.w1, "retry": w2}
                if inp.bad: inp.pass_sec = inp.pass_sec + [sec]
            for inp in again:
                for k in inp.bad:
                    seg2 = load(inp, k)
                    f2 = Q.failures(Q.metrics(seg2), settings)
                    if badness(f2, Q.metrics(seg2)) < badness(inp.checks[k], Q.metrics(inp.result[k])):
                        inp.result[k], inp.checks[k] = seg2, f2
            rbase += len(names)
    say(100, "끝")
    out = []
    for inp in inputs:
        if inp.error is not None:
            out.append(inp.error); continue
        segs, stats, used = finish(inp, settings, temp)
        stats.update(passSec=inp.pass_sec, device=inp.w.device(), vad={"engine": engine, **inp.vad_stats}, whisper=inp.w.timings,
                     parallel="auto" if opts["auto"] else opts["n"], workers=inp.w1)
        if len(inputs) > 1: stats["multi"] = {"inputs": len(inputs), "chunks": m}
        out.append((segs, stats, used))
    return out


def transcribe(wav: Path, work: Path, settings: dict, words: bool, tools: Path, hints: dict | None = None, run: dict | None = None) -> tuple:
    """→ (구간들, 통계, 쓴 단어장 id들). hints = vocab.load() 결과(없으면 힌트 없이).
    run = 실행 옵션(결과를 바꾸지 않음): parallel("auto" 또는 수, 없으면 1. useGpu = false면 고정 수는 늘 1)·parallelMax·yield(autopar.parse)"""
    [res] = run_inputs([Input(wav, words, hints, settings)], work, settings, tools, run)
    return res


def transcribe_many(spec: list, work: Path, settings: dict, tools: Path, run: dict | None = None) -> list:
    """여러 입력(pipelines.py 머리말): spec = [{"wav", "words", "hints"(파일 경로 또는 None)}] → 입력마다 (구간들, 통계, 쓴 단어장 id들) 또는 예외"""
    inputs = []
    for k, r in enumerate(spec):
        try:
            inputs.append(Input(Path(r["wav"]).resolve(), bool(r.get("words")), V.load(r["hints"]) if r.get("hints") else None, settings, f"i{k:03d}_"))
        except Exception as e:
            inputs.append(Input(Path(str(r.get("wav"))), False, None, settings, f"i{k:03d}_", error=e))
    return run_inputs(inputs, work, settings, tools, run, isolate=True)


def write_results(spec: list, results: list, sec: float):
    """여러 입력: 입력마다 O.json(그 입력만 실패했으면 {"error"})"""
    for r, res in zip(spec, results):
        if isinstance(res, BaseException):
            body = {"error": f"{type(res).__name__}: {res}"}
        else:
            segs, stats, used = res
            body = {"segments": segs, "stats": {**stats, "seconds": sec}, "vocabUsed": used}
        Path(r["out"]).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    ap.add_argument("--out")
    ap.add_argument("--inputs", help="여러 입력 파일 {inputs: [{wav, out, words, hints}]}(pipelines.py 머리말)")
    ap.add_argument("--settings", required=True)
    ap.add_argument("--words", action="store_true")
    ap.add_argument("--hints", help="단어장 힌트 파일(vocab.py)")
    ap.add_argument("--run-options", help="실행 옵션 파일(parallel, 결과를 바꾸지 않음)")
    a = ap.parse_args()
    if not a.inputs and not (a.wav and a.out): ap.error("--wav·--out 또는 --inputs가 필요합니다")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    settings = json.load(open(a.settings, encoding="utf-8"))
    run = json.load(open(a.run_options, encoding="utf-8")) if a.run_options else {}
    t0 = time.time()
    if a.inputs:
        spec = json.load(open(a.inputs, encoding="utf-8-sig"))["inputs"]
        work = Path(a.inputs).with_name(Path(a.inputs).stem + ".chunks")
        work.mkdir(parents=True, exist_ok=True)
        write_results(spec, transcribe_many(spec, work, settings, tools_dir(), run), round(time.time() - t0, 1))
    else:
        out = Path(a.out)
        work = out.with_name(out.stem + ".chunks")
        work.mkdir(parents=True, exist_ok=True)
        segs, stats, used = transcribe(Path(a.wav).resolve(), work, settings, a.words, tools_dir(), V.load(a.hints) if a.hints else None, run)
        stats["seconds"] = round(time.time() - t0, 1)
        out.write_text(json.dumps({"segments": segs, "stats": stats, "vocabUsed": used}, ensure_ascii=False), encoding="utf-8")
    for f in work.iterdir(): f.unlink(missing_ok=True)
    work.rmdir()


if __name__ == "__main__":
    main()
