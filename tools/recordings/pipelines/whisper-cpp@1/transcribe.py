"""전사 파이프라인 whisper-cpp@1(2026-11-16): 2026-11-16까지 analyze.py transcribe()가 하던 그대로. 고치지 않는다(바꾸면 whisper-cpp@2).

  python transcribe.py --wav W --out O.json --settings S.json [--words]
  → O.json = {"segments": [{startMs, endMs, text}]}

- whisper-cli -l ko + Silero VAD(-vsd 500 -vp 200). --words면 단어마다 한 구간(-ml 1 -sow, analyze.py 화자 구분이 다시 묶는다).
- 설정: asrModel = models/ggml-<이름>.bin
- 도구·모델 폴더: 환경 변수 SECONDBRAIN_ANALYSIS_TOOLS(analyze.py가 넘김) → %LOCALAPPDATA%/SecondBrain/analysis
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적). large-v3-turbo는 화자가 바뀔 때 "-"만 든 단어, 무음에 "한글자막 by …"를 낸다(2026-11-13)
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|한글\s*자막.*|[.\s,!?♪\-–—]*)$")


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
    cmd = [str(whisper), "-m", str(model), "-l", "ko", "-f", a.wav, "-oj", "-of", str(base), "-np",
           "--vad", "-vm", str(vad), "-vsd", "500", "-vp", "200"] + (["-ml", "1", "-sow"] if a.words else [])
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        sys.exit("whisper 실패: " + p.stderr[-400:])
    data = json.load(open(str(base) + ".json", encoding="utf-8"))
    out.write_text(json.dumps({"segments": segments(data)}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
