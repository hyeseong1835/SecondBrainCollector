"""전사 파이프라인 whisper-lite@1(2026-11-17): whisper-cpp@2의 반복 억제는 그대로 두고 계산을 줄인 것. 공개 뒤에는 고치지 않는다(바꾸면 whisper-lite@2).
계획: 음성 전사 개선 계획.md "가벼운 파이프라인". 가벼움의 우선순위는 ① 전사 시간 ② GPU 점유 ③ 메모리.

  python transcribe.py --wav W --out O.json --settings S.json [--words] [--hints H.json]
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
"""
import argparse, collections, json, os, re, subprocess, sys, time, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import quality as Q
import vocab as V

RATE = 16000
FRAME_MS = 100
BATCH = 40   # whisper-cli 한 번에 넘기는 조각 수(명령줄 길이 제한)
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


def progress(percent: float, text: str):
    print("@@progress " + json.dumps({"percent": round(percent, 1), "text": text}, ensure_ascii=False), flush=True)


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


class Whisper:
    """whisper-cli 실행기. 조각마다 진행도를 내고 시간 통계를 모은다"""
    def __init__(self, base: list, work: Path):
        self.base, self.work = base, work
        self.timings, self.gpu, self.vulkan = {}, None, {}

    def device(self) -> str:
        if not self.gpu: return "CPU"
        m = re.fullmatch(r"Vulkan(\d+)", self.gpu)
        return self.vulkan.get(m.group(1), self.gpu) if m else self.gpu

    def run(self, names: list, extra: list, on_chunk):
        """조각 wav 이름들 → 같은 이름의 .json(work 안, 상대 경로로 명령줄을 줄인다). on_chunk(이번 실행에서 시작한 조각 수)"""
        done = 0
        for i in range(0, len(names), BATCH):
            cmd = self.base + extra
            for n in names[i:i + BATCH]: cmd += ["-f", n, "-of", Path(n).stem]
            tail, vad_ms, t0, first = collections.deque(maxlen=20), 0.0, time.time(), True
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=str(self.work))
            for line in p.stderr:
                tail.append(line.rstrip())
                if PROCESSING.search(line):
                    if first:
                        add_time(self.timings, "startup", (time.time() - t0) * 1000); first = False
                    done += 1; on_chunk(done)
                elif m := VAD_TIME.search(line):
                    vad_ms = float(m.group(1))
                elif "whisper_print_timings" in line:
                    add_timings(self.timings, line)
                elif self.gpu is None and (m := GPU.search(line)):
                    self.gpu = m.group(1)
                elif m := VULKAN.search(line):
                    self.vulkan[m.group(1)] = m.group(2).strip()
            if p.wait() != 0:
                sys.exit("whisper 실패: " + "\n".join(tail)[-400:])
            add_time(self.timings, "vad", vad_ms)


def badness(fails: list, m: dict) -> tuple:
    return (len(fails), m["phraseRepeat"], m["charRun"], m["compression"])


def transcribe(wav: Path, work: Path, settings: dict, words: bool, tools: Path, hints: dict | None = None) -> tuple:
    """→ (구간들, 통계, 쓴 단어장 id들). hints = vocab.load() 결과(없으면 힌트 없이)"""
    hints = hints if hints and settings.get("useHints", True) else None
    prompt = hints["prompt"] if hints else ""
    exe = tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
    model = tools / "models" / f"ggml-{settings.get('asrModel') or 'batisay-ko-turbo-q8_0'}.bin"
    vad = tools / "models" / "ggml-silero-v5.1.2.bin"
    engine, onnx, vad_stats = settings.get("vadEngine", "sherpa"), find_vad_onnx(tools), {}
    if engine == "sherpa":
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            onnx = None
        if onnx is None:
            print(f"sherpa-onnx나 models/{'·'.join(VAD_ONNX)}가 없어 whisper.cpp VAD로 돈다", file=sys.stderr); engine = "whisper"
    for p in (exe, model) + ((vad,) if engine == "whisper" else ()):
        if not p.exists(): sys.exit(f"없음: {p}")
    samples = read_wav(wav)
    total = len(samples) * 1000 // RATE
    if engine == "sherpa":
        t = time.time()
        spans = speech_spans(samples, onnx, settings)
        chunks = pack(spans, int(settings["chunkSec"] * 1000))
        vad_stats = {"sec": round(time.time() - t, 2), "spans": len(spans), "speechSec": round(sum(b - a for a, b in spans) / 1000, 1)}
    else:
        chunks = [[c] for c in plan_chunks(frame_energy(samples), total, int(settings["chunkMinSec"] * 1000), int(settings["chunkSec"] * 1000))]
    names = []
    for k, pieces in enumerate(chunks):
        name = f"c{k:04d}.wav"
        write_wav(work / name, chunk_audio(samples, pieces))
        names.append(name)
    w = Whisper(base_args(exe, model, vad if engine == "whisper" else None, settings, words, prompt), work)
    # whisper-cli가 한글 한 글자를 토큰 경계에서 잘라 깨진 UTF-8을 낼 때가 있다(2026-11-16 단어 단위 실측). 깨진 바이트는 버린다
    load = lambda k: chunk_segments(json.loads((work / f"c{k:04d}.json").read_bytes().decode("utf-8", errors="replace")), chunks[k])
    m = len(chunks)
    progress(0, f"조각 0/{m}")
    t0 = time.time()
    w.run(names, [], lambda i: progress(90 * (i - 1) / m, f"조각 {i}/{m}"))
    pass_sec = [round(time.time() - t0, 1)]
    result = [load(k) for k in range(m)]
    checks = [Q.failures(Q.metrics(segs), settings) for segs in result]
    bad = [k for k, f in enumerate(checks) if f]
    temp = float(settings.get("retryTemperature") or 0)
    if bad and temp > 0:
        for k in bad: (work / f"c{k:04d}.json").unlink(missing_ok=True)
        r = len(bad)
        t1 = time.time()
        w.run([names[k] for k in bad], ["-tp", str(temp)], lambda i: progress(90 + 10 * (i - 1) / r, f"다시 돌리는 중 {i}/{r}"))
        pass_sec.append(round(time.time() - t1, 1))
        for k in bad:
            again = load(k)
            f2 = Q.failures(Q.metrics(again), settings)
            if badness(f2, Q.metrics(again)) < badness(checks[k], Q.metrics(result[k])):
                result[k], checks[k] = again, f2
    progress(100, "끝")
    out, n_suspect, n_dropped = [], 0, 0
    for segs, fails in zip(result, checks):
        segs = Q.clean(segs, int(settings["maxPhraseRepeat"]))
        if fails and segs:
            n_suspect += 1
            if settings.get("onSuspect") == "drop":
                n_dropped += 1; continue
            for s in segs: s["suspect"] = True
        out += segs
    used = list(hints["ids"]) if prompt else []
    n_fixed = 0
    if hints and hints["fixes"]:
        out, fixed = V.apply_fixes(out, hints["fixes"])
        used += fixed; n_fixed = sum(1 for s in out if "original" in s)
    return out, {"chunks": m, "retried": len(bad) if temp > 0 else 0, "suspectChunks": n_suspect, "droppedChunks": n_dropped,
                 "failures": {f"{c[0][0] // 1000}s": f for c, f in zip(chunks, checks) if f},
                 "prompt": prompt, "fixedSegments": n_fixed, "passSec": pass_sec, "device": w.device(), "vad": {"engine": engine, **vad_stats},
                 "whisper": w.timings}, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--words", action="store_true")
    ap.add_argument("--hints", help="단어장 힌트 파일(vocab.py)")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    settings = json.load(open(a.settings, encoding="utf-8"))
    out = Path(a.out)
    work = out.with_name(out.stem + ".chunks")
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    segs, stats, used = transcribe(Path(a.wav).resolve(), work, settings, a.words, tools_dir(), V.load(a.hints) if a.hints else None)
    stats["seconds"] = round(time.time() - t0, 1)
    out.write_text(json.dumps({"segments": segs, "stats": stats, "vocabUsed": used}, ensure_ascii=False), encoding="utf-8")
    for f in work.iterdir(): f.unlink(missing_ok=True)
    work.rmdir()


if __name__ == "__main__":
    main()
