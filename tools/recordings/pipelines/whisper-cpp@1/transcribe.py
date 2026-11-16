"""전사 파이프라인 whisper-cpp@1(2026-11-16): 2026-11-16까지 analyze.py transcribe()가 하던 그대로. 고치지 않는다(바꾸면 whisper-cpp@2).

  python transcribe.py --wav W --out O.json --settings S.json [--words]
  → O.json = {"segments": [{startMs, endMs, text}]}

- whisper-cli -l ko + Silero VAD(-vsd 500 -vp 200). --words면 단어마다 한 구간(-ml 1 -sow, analyze.py 화자 구분이 다시 묶는다).
- 설정: asrModel = models/ggml-<이름>.bin
- 도구·모델 폴더: 환경 변수 SECONDBRAIN_ANALYSIS_TOOLS(analyze.py가 넘김) → %LOCALAPPDATA%/SecondBrain/analysis
- 진행도(2026-11-17, 결과를 바꾸지 않아 같은 버전에 넣음): whisper-cli에 -pp를 더해 "progress = N%" 줄을 읽고 표준 출력에 `@@progress {"percent": N}`을 낸다. 전사 결과는 그대로.
"""
import argparse, json, os, re, subprocess, sys
from collections import deque
from pathlib import Path

# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적). large-v3-turbo는 화자가 바뀔 때 "-"만 든 단어, 무음에 "한글자막 by …"를 낸다(2026-11-13)
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|한글\s*자막.*|[.\s,!?♪\-–—]*)$")


PROGRESS = re.compile(r"progress\s*=\s*(\d+(?:\.\d+)?)\s*%")   # whisper-cli -pp: "whisper_print_progress_callback: progress =  10%"


def emit(**kw):
    print("@@progress " + json.dumps(kw, ensure_ascii=False), flush=True)


def tools_dir() -> Path:
    if os.environ.get("SECONDBRAIN_ANALYSIS_TOOLS"): return Path(os.environ["SECONDBRAIN_ANALYSIS_TOOLS"])
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "SecondBrain" / "analysis"


def segments(data: dict) -> list:
    """whisper-cli -oj 결과 → 구간들(빈 글·환각 문장 뺌)"""
    out = []
    for s in data.get("transcription", []):
        text = (s.get("text") or "").strip()
        if not text or HALLUCINATIONS.match(text): continue
        out.append({"startMs": int(s["offsets"]["from"]), "endMs": int(s["offsets"]["to"]), "text": text})
    return out


def run_whisper(cmd: list, on_percent=None) -> tuple:
    """whisper-cli를 돌려 (종료 코드, 출력 끝). 표준 오류를 섞어 줄 단위로 읽으며 -pp 진행 줄이면 on_percent(0~100)"""
    tail = deque(maxlen=20)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as p:
        for line in p.stdout:
            m = PROGRESS.search(line)
            if m:
                if on_percent: on_percent(float(m.group(1)))
            else:
                tail.append(line)
        return p.wait(), "".join(tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--words", action="store_true")
    a = ap.parse_args()
    settings = json.load(open(a.settings, encoding="utf-8"))
    tools = tools_dir()
    whisper = tools / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
    model = tools / "models" / f"ggml-{settings.get('asrModel') or 'batisay-ko-turbo-q8_0'}.bin"
    vad = tools / "models" / "ggml-silero-v5.1.2.bin"
    for p in (whisper, model, vad):
        if not p.exists(): sys.exit(f"없음: {p}")
    out = Path(a.out)
    base = out.with_name(out.stem + ".whisper")
    cmd = [str(whisper), "-m", str(model), "-l", "ko", "-f", a.wav, "-oj", "-of", str(base), "-np", "-pp",
           "--vad", "-vm", str(vad), "-vsd", "500", "-vp", "200"] + (["-ml", "1", "-sow"] if a.words else [])
    emit(percent=0)
    code, tail = run_whisper(cmd, lambda pct: emit(percent=min(100.0, pct)))
    if code != 0:
        sys.exit("whisper 실패: " + tail[-400:])
    data = json.load(open(str(base) + ".json", encoding="utf-8"))
    out.write_text(json.dumps({"segments": segments(data)}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
