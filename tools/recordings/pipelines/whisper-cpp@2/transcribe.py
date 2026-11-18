"""전사 파이프라인 whisper-cpp@2(2026-11-16): 반복 환각 막기(계획 "1단계"). 공개 뒤에는 고치지 않는다(바꾸면 whisper-cpp@3).

  python transcribe.py --wav W --out O.json --settings S.json [--words] [--hints H.json] [--run-options R.json]
  → O.json = {"segments": [{startMs, endMs, text, suspect?, original?}], "stats": {...}, "vocabUsed": [id]}

@1과 다른 점(2026-11-14 통화가 버스 소음 구간부터 "으"·"u/"·"데스라인 벤션 2차 강화입니다." 수백 번으로 망가진 것):
- 앞 글 힌트 끔(-mc, 설정 maxContext = 0).
- 조각별 전사: wav를 chunkSec(30초) 안팎으로 자르고(chunkMinSec~chunkSec 사이에서 가장 조용한 0.1초에서) 조각마다 따로 전사한다.
  whisper-cli 한 번에 조각 여러 개를 넘겨 모델은 한 번만 올리고, 조각마다 Silero VAD(-vsd·-vp, @1과 같은 값)로 말소리만 돈다.
  구간 시각 = 조각 시작 + 조각 안 시각(묶음 오프셋과 같은 방식). 한 조각이 망가져도 다음 조각으로 번지지 않는다.
  (Silero VAD 구간만 뽑는 whisper.cpp 예제는 빌드돼 있지 않아 쓰지 않았다. 빌드 폴더를 다시 구성해야 해서.)
- 품질 검사(quality.py): 조각마다 같은 구절 연속 반복·같은 글자 연속·압축률·글자/초. 걸리면 retryTemperature로 한 번 다시(걸린 조각만 모아 한 번에),
  둘 중 덜 걸린 쪽을 쓰고 그래도 걸리면 그 조각의 구간에 suspect: true(onSuspect = "drop"이면 버림).
- 반복 필터(quality.clean): 기호 부스러기 제거, 같은 구절 maxPhraseRepeat번 넘게 → 한 번. 무음 환각 문장 필터는 @1 그대로.
- 단어장 힌트(--hints, 설정 useHints, vocab.py): prompt를 --prompt로 모든 조각에 똑같이 넣는다(조각마다 따로 전사하므로 앞 조각 글은 이어지지 않는다).
  whisper.cpp는 -mc 0이면 --prompt도 버리므로(앞 글 자리에 프롬프트를 넣고 -mc만큼 자른다) 힌트가 있을 때만 -mc를 hintContext로 올린다(일단, 실제 전사로 확인 전).
  전사 뒤 고침 짝을 치환하고 바꾼 구간은 원문을 original에 둔다. vocabUsed = 쓴 힌트 id(프롬프트를 넣었으면) + 실제로 바꾼 고침 짝 id(analyze.py가 POST /api/vocab/used).
- --words면 단어마다 한 구간(-ml 1 -sow). 필터는 단어를 이어 놓고 접으므로 단어에 걸친 반복도 접는다.
- 도구·모델 폴더: 환경 변수 SECONDBRAIN_ANALYSIS_TOOLS(analyze.py가 넘김) → %LOCALAPPDATA%/SecondBrain/analysis
- 진행도(2026-11-17, 결과를 바꾸지 않아 같은 버전에 넣음): whisper-cli에 -pp를 더해 조각 안 "progress = N%" 줄과 끝난 조각 수(.json이 생긴 수)로
  표준 출력에 `@@progress {"percent": 조각 수 기준 0~100, "text": "조각 i/m"}`을 낸다. 품질 검사에 걸린 조각을 다시 돌리는 동안은 percent 100, text "다시 돌리는 중 i/b조각".
- 동시 실행(2026-11-17 KST, 결과를 바꾸지 않아 같은 버전): 실행 옵션 parallel(--run-options, 매니페스트 runOptions 기본 2. 설정 버전에 들어가지 않는다.
  파일이 없으면 1)만큼 whisper-cli를 동시에 띄운다. 조각을 작업자마다 번갈아 나누고
  (조각·인자가 같으니 조각마다 결과는 같다) 작업자마다 BATCH씩 넘긴다. 디코더(빔 5)·VAD·멜 변환을 CPU가 도는 동안 GPU가 쉬던 것을 메운다.
  하나라도 실패하면 나머지를 죽이고 "whisper 실패". 진행도 = 끝난 조각 수(.json 수) + 돌고 있는 작업자들의 조각 안 퍼센트 평균. parallel 1이면 전과 같다.
- 자동 동시 실행(2026-11-18 KST, 결과를 바꾸지 않아 같은 버전): parallel = "auto"(새 기본) 또는 고정 수, parallelMax·yield(autopar.py 머리말).
  번갈아 나누기 대신 공유 대기열(autopar.Pool): 작업자가 묶음씩 가져가고 조정기가 처리량·VRAM·GPU·CPU·메모리를 재 작업자 수를 바꾼다.
  고정 수면 묶음 = 조각 수 ÷ 작업자 수(최대 BATCH)라 whisper-cli 실행 횟수가 전보다 많지 않다(1이면 전과 같다). 조각·인자가 같으니 구간은 그대로(시험: parallel 1과 auto 같은 구간).
  stats.parallel = 실행 옵션 값("auto" 또는 수), stats.workers = 풀 통계(첫 전사, 다시 돌리기는 workers.retry). @@progress에 "workers"(지금 작업자 수)와 글 " · 작업자 N".
- 여러 입력(2026-11-18 KST, 결과를 바꾸지 않아 같은 버전): 매니페스트 multiInput, --inputs I.json(pipelines.py 머리말). 입력마다 조각 계획·품질 검사·다시 돌리기 판정·고침은 따로 하고,
  whisper-cli만 (단어 단위, 프롬프트)가 같은 입력끼리 한 작업자 풀에 넣는다(조각 이름 앞에 i000_). 짧은 녹음 여러 개에서 모델 올리기·python 띄우기가 파일마다가 아니라 한 번.
  다시 돌리기는 모든 입력의 걸린 조각을 무리마다 한 번에. 한 입력의 wav가 깨지면 그 입력만 {"error"}. 진행도 percent·"조각 i/m"은 모든 입력 기준, 글 끝에 "(파일 N개)".
  한 개 모드(--wav)는 같은 길(run_inputs에 입력 하나)로 돌아 전과 같다(시험: test_multi_input 입력마다 하나씩 돌린 것과 같은 구간).
"""
import argparse, json, os, re, subprocess, sys, threading, time, wave
from collections import deque
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import autopar as A
import quality as Q
import vocab as V

RATE = 16000
FRAME_MS = 100
BATCH = A.BATCH   # whisper-cli 한 번에 넘기는 조각 수(명령줄 길이 제한)
PROGRESS = re.compile(r"progress\s*=\s*(\d+(?:\.\d+)?)\s*%")   # whisper-cli -pp: "whisper_print_progress_callback: progress =  10%"(조각 파일마다 0부터)


def emit(percent=None, text=None, workers=None):
    """analyze.py가 읽는 진행 한 줄(pipelines.py 머리말). workers = 지금 whisper-cli 작업자 수(자동 동시 실행)"""
    ev = {k: v for k, v in (("percent", None if percent is None else round(min(100.0, max(0.0, percent)), 1)), ("text", text), ("workers", workers)) if v is not None}
    print("@@progress " + json.dumps(ev, ensure_ascii=False), flush=True)


def tools_dir() -> Path:
    if os.environ.get("SECONDBRAIN_ANALYSIS_TOOLS"): return Path(os.environ["SECONDBRAIN_ANALYSIS_TOOLS"])
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "SecondBrain" / "analysis"


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


def chunk_segments(data: dict, start_ms: int, end_ms: int) -> list:
    """조각 하나의 whisper-cli -oj 결과 → 녹음 시각 구간들(깨진 글자·빈 글·무음 환각 문장 뺌, 조각 끝을 넘지 않게)"""
    out = []
    for s in data.get("transcription", []):
        text = (s.get("text") or "").replace("�", "").strip()
        if not text or Q.HALLUCINATIONS.match(text): continue
        a = min(start_ms + int(s["offsets"]["from"]), end_ms)
        b = max(a, min(start_ms + int(s["offsets"]["to"]), end_ms))
        out.append({"startMs": a, "endMs": b, "text": text})
    return out


def split_names(names: list, n: int) -> list:
    """조각 이름들 → 작업자 n개 몫(번갈아 나눔). 2026-11-18부터 whisper()는 공유 대기열(autopar)을 쓰고 이것은 쓰지 않는다(시험·비교용으로 남김)"""
    n = max(1, int(n or 1))
    return [g for g in (names[i::n] for i in range(n)) if g]


def json_ok(path: Path) -> bool:
    """whisper-cli -oj 결과가 다 써졌는가(죽인 whisper-cli가 반쯤 쓴 파일은 아님)"""
    try:
        json.loads(path.read_bytes().decode("utf-8", errors="replace"))
        return True
    except (OSError, ValueError):
        return False


def whisper(base: list, names: list, work: Path, progress=None, run=1, weights: dict | None = None, key: str = "", on_workers=None, **pool) -> dict:
    """조각 wav 이름들 → 같은 이름의 .json(work 안, 상대 경로로 명령줄을 줄인다). whisper-cli를 실행 옵션 run대로 동시에(autopar 공유 대기열).
    run = 실행 옵션 딕셔너리 또는 고정 수(시험). progress(끝난 조각 수, 전체 조각 수, 조각 안 진행 0~100) = -pp 진행 줄마다.
    끝난 조각 = .json이 생긴 것(묶음을 띄우기 전에 지운다). 조각 안 진행 = 돌고 있는 작업자들의 마지막 퍼센트 평균. 여러 스레드가 불러도 progress는 한 번에 하나씩(잠금).
    고정 수에서 하나라도 실패하면 나머지 whisper-cli를 죽이고 sys.exit("whisper 실패: ..."). → 풀 통계(autopar.Pool.stats)"""
    run = run if isinstance(run, dict) or run is None else {"parallel": run}
    lock, pcts = threading.Lock(), {}

    def report(w, pct):
        with lock:
            pcts[w] = pct
            live = list(pcts.values())
            done = sum(1 for n in names if (work / f"{Path(n).stem}.json").exists())
            progress(done, len(names), sum(live) / len(live) if live else 0.0)

    def launch(w, batch, started):
        cmd = list(base) + ["-pp"]
        for n in batch: cmd += ["-f", n, "-of", Path(n).stem]
        try:
            return run_whisper(cmd, work, progress and (lambda pct: report(w, pct)), started)
        finally:
            with lock: pcts.pop(w, None)
    return A.run_pool(names, launch, lambda n: json_ok(work / f"{Path(n).stem}.json"), lambda n: (work / f"{Path(n).stem}.json").unlink(missing_ok=True),
                      weights, run, key=key, on_workers=on_workers, **pool)


def run_whisper(cmd: list, work: Path, on_percent=None, started=None) -> tuple:
    """whisper-cli를 돌려 (종료 코드, 출력 끝). 표준 오류를 섞어 줄 단위로 읽으며 -pp 진행 줄이면 on_percent(0~100). started(Popen) = 띄운 직후(죽일 수 있게)"""
    tail = deque(maxlen=20)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", cwd=str(work)) as p:
        if started: started(p)
        for line in p.stdout:
            m = PROGRESS.search(line)
            if m:
                if on_percent: on_percent(float(m.group(1)))
            else:
                tail.append(line)
        return p.wait(), "".join(tail)


def badness(fails: list, m: dict) -> tuple:
    return (len(fails), m["phraseRepeat"], m["charRun"], m["compression"])


class Input:
    """입력 wav 하나(한 개 모드는 이것 하나, 여러 입력은 여럿). prefix = 조각 이름 앞(여러 입력이 한 작업 폴더를 같이 쓴다)"""
    def __init__(self, wav: Path, words: bool, hints: dict | None, settings: dict, prefix: str = "", error=None):
        self.wav, self.words, self.prefix, self.error = wav, words, prefix, error
        self.hints = hints if hints and settings.get("useHints", True) else None
        self.prompt = self.hints["prompt"] if self.hints else ""
        self.chunks, self.names = [], []
        self.result, self.checks, self.bad = [], [], []
        self.w1 = {}


def prepare(inp: Input, work: Path, settings: dict):
    """wav → 조각 wav들(work/<prefix>cNNNN.wav)과 inp.chunks[(시작 ms, 끝 ms)]"""
    samples = read_wav(inp.wav)
    total = len(samples) * 1000 // RATE
    inp.chunks = plan_chunks(frame_energy(samples), total, int(settings["chunkMinSec"] * 1000), int(settings["chunkSec"] * 1000))
    inp.names = []
    for k, (a, b) in enumerate(inp.chunks):
        name = f"{inp.prefix}c{k:04d}.wav"
        write_wav(work / name, samples[a * RATE // 1000:b * RATE // 1000])
        inp.names.append(name)


def base_args(exe: Path, model: Path, vad: Path, settings: dict, words: bool, prompt: str) -> list:
    context = int(settings["maxContext"])
    if prompt: context = max(context, int(settings.get("hintContext") or 0))
    base = [str(exe), "-m", str(model), "-l", "ko", "-oj", "-np", "-mc", str(context),
            "--vad", "-vm", str(vad), "-vsd", str(int(settings["vadMinSilenceMs"])), "-vp", str(int(settings["vadPadMs"]))]
    if words: base += ["-ml", "1", "-sow"]
    if prompt: base += ["--prompt", prompt]
    return base


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
                 "failures": {f"{a // 1000}s": f for (a, _), f in zip(inp.chunks, inp.checks) if f}, "prompt": inp.prompt, "fixedSegments": n_fixed}, used


def run_inputs(inputs: list, work: Path, settings: dict, tools: Path, progress=None, run: dict | None = None, isolate: bool = False) -> list:
    """입력들 → 입력마다 (구간들, 통계, 쓴 단어장 id들) 또는 그 입력의 예외(isolate일 때만: 한 입력의 wav가 깨져도 나머지는 계속).
    조각은 입력마다 따로 계획·검사·고침하고, whisper-cli만 (단어 단위, 프롬프트)가 같은 입력끼리 한 작업자 풀에 같이 넣는다(조각·인자가 같으니 조각마다 결과는 같다).
    진행도 percent = 모든 입력의 조각 수 기준. 입력이 둘 이상이면 글 끝에 (파일 N개)"""
    exe = tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
    model = tools / "models" / f"ggml-{settings.get('asrModel') or 'batisay-ko-turbo-q8_0'}.bin"
    vad = tools / "models" / "ggml-silero-v5.1.2.bin"
    for p in (exe, model, vad):
        if not p.exists(): sys.exit(f"없음: {p}")
    live = []
    for inp in inputs:
        if inp.error is not None: continue
        try:
            prepare(inp, work, settings)
            live.append(inp)
        except Exception as e:
            if not isolate: raise
            inp.error = e
    # whisper-cli가 한글 한 글자를 토큰 경계에서 잘라 깨진 UTF-8을 낼 때가 있다(2026-11-16 단어 단위 실측). 깨진 바이트는 버린다
    load = lambda inp, k: chunk_segments(json.loads((work / f"{Path(inp.names[k]).stem}.json").read_bytes().decode("utf-8", errors="replace")), *inp.chunks[k])
    m = sum(len(inp.chunks) for inp in live)
    opts = A.parse(run)
    workers = [None if not opts["auto"] and opts["n"] == 1 else opts["n"]]   # 진행도에 낼 작업자 수(고정 1이면 내지 않아 전과 같다)
    files = f"(파일 {len(inputs)}개)" if len(inputs) > 1 else ""
    say = lambda pct, text: progress(pct, f"{text}{files} · 작업자 {workers[0]}", workers[0]) if workers[0] else progress(pct, text + files)
    if progress: say(0, f"조각 1/{m}")
    on_workers = (lambda n: workers.__setitem__(0, n)) if opts["auto"] or opts["n"] > 1 else None
    groups = {}   # (단어 단위, 프롬프트) → 입력들(처음 나온 차례)
    for inp in live: groups.setdefault((inp.words, inp.prompt), []).append(inp)
    keys = {g: A.state_key(Path(__file__).parent.name, settings, g[0]) for g in groups}
    bases = {g: base_args(exe, model, vad, settings, g[0], g[1]) for g in groups}
    start = 0
    for g, group in groups.items():
        names = [n for inp in group for n in inp.names]
        weights = {n: b - a for inp in group for n, (a, b) in zip(inp.names, inp.chunks)}
        w1 = whisper(bases[g], names, work, progress and (lambda done, total, pct, start=start: say((start + done + pct / 100) / m * 100, f"조각 {min(start + done + 1, m)}/{m}")),
                     run or {}, weights, keys[g], on_workers) or {}
        for inp in group:
            inp.w1 = w1
            inp.result = [load(inp, k) for k in range(len(inp.chunks))]
            inp.checks = [Q.failures(Q.metrics(segs), settings) for segs in inp.result]
            inp.bad = [k for k, f in enumerate(inp.checks) if f]
        start += len(names)
    temp = float(settings.get("retryTemperature") or 0)
    if temp > 0:
        r, rstart = sum(len(inp.bad) for inp in live), 0
        for g, group in groups.items():
            again = [inp for inp in group if inp.bad]
            if not again: continue
            for inp in again:
                for k in inp.bad: (work / f"{Path(inp.names[k]).stem}.json").unlink(missing_ok=True)
            names = [inp.names[k] for inp in again for k in inp.bad]
            weights = {inp.names[k]: inp.chunks[k][1] - inp.chunks[k][0] for inp in again for k in inp.bad}
            w2 = whisper(bases[g] + ["-tp", str(temp)], names, work,
                         progress and (lambda done, total, pct, rstart=rstart: say(100, f"다시 돌리는 중 {min(rstart + done + 1, r)}/{r}조각")), run or {}, weights, keys[g], on_workers)
            for inp in group: inp.w1 = {**inp.w1, "retry": w2}
            for inp in again:
                for k in inp.bad:
                    seg2 = load(inp, k)
                    f2 = Q.failures(Q.metrics(seg2), settings)
                    if badness(f2, Q.metrics(seg2)) < badness(inp.checks[k], Q.metrics(inp.result[k])):
                        inp.result[k], inp.checks[k] = seg2, f2
            rstart += len(names)
    out = []
    for inp in inputs:
        if inp.error is not None:
            out.append(inp.error); continue
        segs, stats, used = finish(inp, settings, temp)
        stats.update(parallel="auto" if opts["auto"] else opts["n"], workers=inp.w1)
        if len(inputs) > 1: stats["multi"] = {"inputs": len(inputs), "chunks": m}
        out.append((segs, stats, used))
    return out


def transcribe(wav: Path, work: Path, settings: dict, words: bool, tools: Path, hints: dict | None = None, progress=None, run: dict | None = None) -> tuple:
    """→ (구간들, 통계, 쓴 단어장 id들). hints = vocab.load() 결과(없으면 힌트 없이). progress(percent, text[, workers]) = 진행(emit).
    run = 실행 옵션(결과를 바꾸지 않음): parallel("auto" 또는 수, 없으면 1)·parallelMax·yield(autopar.parse)"""
    [res] = run_inputs([Input(wav, words, hints, settings)], work, settings, tools, progress, run)
    return res


def transcribe_many(spec: list, work: Path, settings: dict, tools: Path, progress=None, run: dict | None = None) -> list:
    """여러 입력(pipelines.py 머리말): spec = [{"wav", "words", "hints"(파일 경로 또는 None)}] → 입력마다 (구간들, 통계, 쓴 단어장 id들) 또는 예외"""
    inputs = []
    for k, r in enumerate(spec):
        try:
            inputs.append(Input(Path(r["wav"]).resolve(), bool(r.get("words")), V.load(r["hints"]) if r.get("hints") else None, settings, f"i{k:03d}_"))
        except Exception as e:
            inputs.append(Input(Path(str(r.get("wav"))), False, None, settings, f"i{k:03d}_", error=e))
    return run_inputs(inputs, work, settings, tools, progress, run, isolate=True)


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
    settings = json.load(open(a.settings, encoding="utf-8"))
    run = json.load(open(a.run_options, encoding="utf-8")) if a.run_options else {}
    t0 = time.time()
    if a.inputs:
        spec = json.load(open(a.inputs, encoding="utf-8-sig"))["inputs"]
        work = Path(a.inputs).with_name(Path(a.inputs).stem + ".chunks")
        work.mkdir(parents=True, exist_ok=True)
        write_results(spec, transcribe_many(spec, work, settings, tools_dir(), emit, run), round(time.time() - t0, 1))
    else:
        out = Path(a.out)
        work = out.with_name(out.stem + ".chunks")
        work.mkdir(parents=True, exist_ok=True)
        segs, stats, used = transcribe(Path(a.wav).resolve(), work, settings, a.words, tools_dir(), V.load(a.hints) if a.hints else None, emit, run)
        stats["seconds"] = round(time.time() - t0, 1)
        out.write_text(json.dumps({"segments": segs, "stats": stats, "vocabUsed": used}, ensure_ascii=False), encoding="utf-8")
    for f in work.iterdir(): f.unlink(missing_ok=True)
    work.rmdir()


if __name__ == "__main__":
    main()
