"""전사 파이프라인 whisper-cpp@2(2026-11-16): 반복 환각 막기(계획 "1단계"). 공개 뒤에는 고치지 않는다(바꾸면 whisper-cpp@3).

  python transcribe.py --wav W --out O.json --settings S.json [--words] [--hints H.json]
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
"""
import argparse, json, os, subprocess, sys, time, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import quality as Q
import vocab as V

RATE = 16000
FRAME_MS = 100
BATCH = 40   # whisper-cli 한 번에 넘기는 조각 수(명령줄 길이 제한)


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


def whisper(base: list, names: list, work: Path):
    """조각 wav 이름들 → 같은 이름의 .json(work 안, 상대 경로로 명령줄을 줄인다)"""
    for i in range(0, len(names), BATCH):
        cmd = list(base)
        for n in names[i:i + BATCH]: cmd += ["-f", n, "-of", Path(n).stem]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(work))
        if p.returncode != 0:
            sys.exit("whisper 실패: " + p.stderr[-400:])


def badness(fails: list, m: dict) -> tuple:
    return (len(fails), m["phraseRepeat"], m["charRun"], m["compression"])


def transcribe(wav: Path, work: Path, settings: dict, words: bool, tools: Path, hints: dict | None = None) -> tuple:
    """→ (구간들, 통계, 쓴 단어장 id들). hints = vocab.load() 결과(없으면 힌트 없이)"""
    hints = hints if hints and settings.get("useHints", True) else None
    prompt = hints["prompt"] if hints else ""
    exe = tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
    model = tools / "models" / f"ggml-{settings.get('asrModel') or 'batisay-ko-turbo-q8_0'}.bin"
    vad = tools / "models" / "ggml-silero-v5.1.2.bin"
    for p in (exe, model, vad):
        if not p.exists(): sys.exit(f"없음: {p}")
    samples = read_wav(wav)
    total = len(samples) * 1000 // RATE
    chunks = plan_chunks(frame_energy(samples), total, int(settings["chunkMinSec"] * 1000), int(settings["chunkSec"] * 1000))
    names = []
    for k, (a, b) in enumerate(chunks):
        name = f"c{k:04d}.wav"
        write_wav(work / name, samples[a * RATE // 1000:b * RATE // 1000])
        names.append(name)
    context = int(settings["maxContext"])
    if prompt: context = max(context, int(settings.get("hintContext") or 0))
    base = [str(exe), "-m", str(model), "-l", "ko", "-oj", "-np", "-mc", str(context),
            "--vad", "-vm", str(vad), "-vsd", str(int(settings["vadMinSilenceMs"])), "-vp", str(int(settings["vadPadMs"]))]
    if words: base += ["-ml", "1", "-sow"]
    if prompt: base += ["--prompt", prompt]
    # whisper-cli가 한글 한 글자를 토큰 경계에서 잘라 깨진 UTF-8을 낼 때가 있다(2026-11-16 단어 단위 실측). 깨진 바이트는 버린다
    load = lambda k: chunk_segments(json.loads((work / f"c{k:04d}.json").read_bytes().decode("utf-8", errors="replace")), *chunks[k])
    whisper(base, names, work)
    result = [load(k) for k in range(len(chunks))]
    checks = [Q.failures(Q.metrics(segs), settings) for segs in result]
    bad = [k for k, f in enumerate(checks) if f]
    temp = float(settings.get("retryTemperature") or 0)
    if bad and temp > 0:
        for k in bad: (work / f"c{k:04d}.json").unlink(missing_ok=True)
        whisper(base + ["-tp", str(temp)], [names[k] for k in bad], work)
        for k in bad:
            again = load(k)
            f2 = Q.failures(Q.metrics(again), settings)
            if badness(f2, Q.metrics(again)) < badness(checks[k], Q.metrics(result[k])):
                result[k], checks[k] = again, f2
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
    return out, {"chunks": len(chunks), "retried": len(bad) if temp > 0 else 0, "suspectChunks": n_suspect, "droppedChunks": n_dropped,
                 "failures": {f"{a // 1000}s": f for (a, _), f in zip(chunks, checks) if f},
                 "prompt": prompt, "fixedSegments": n_fixed}, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--words", action="store_true")
    ap.add_argument("--hints", help="단어장 힌트 파일(vocab.py)")
    a = ap.parse_args()
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
