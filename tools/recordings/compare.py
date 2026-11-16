"""전사 파이프라인 비교(2026-11-16). 계획: 음성 전사 개선 계획.md "확인 방법"·"개발 중 전사 최소화".
녹음 조각 × 파이프라인(설정 버전) → 지표 표. 서버에 올리지도 읽지도 않는다. 잘라 둔 조각만 돌린다(GPU가 약해 긴 녹음은 돌리지 않음).

  python compare.py 조각.wav --pipeline whisper-cpp@1#1 --pipeline whisper-cpp@2 --words
  python compare.py "통화.m4a" --start 1360 --duration 300 --pipeline whisper-cpp@1#1 --pipeline whisper-cpp@2#1 --words --show 15 --json 결과.json

- 입력: wav·m4a 등(ffmpeg로 16kHz 모노 wav). --start·--duration(초)을 주면 그 부분만 잘라 쓴다(모든 입력에).
- --pipeline <이름>@<버전>[#설정 버전]: 여러 번. 설정 버전을 안 주면 이 PC의 최신. 설정 파일을 새로 쓰지 않는다.
- --words: 단어 단위 전사(화자 구분 대상 phone·call·laptop-mic과 같음) 뒤 analyze.py처럼 문장 구간으로 묶어(speakers.group_words, 화자 없이) 잰다.
- 지표 잣대: pipelines/whisper-cpp@2/quality.py의 metrics(같은 구절 연속 반복, 같은 글자 연속, 압축률, 말 1초당 글자 수).
- analyze.py와 같은 잠금(runlock)을 잡는다: 전사와 GPU를 같이 쓰지 않게.
- --json은 실행 하나가 끝날 때마다 다시 쓴다(중간에 실패해도 돈 결과는 남게).
"""
import argparse, importlib.util, json, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import analyze as AN
import pipelines as P
import runlock
import speakers as SP

QUALITY = P.REPO / "whisper-cpp@2" / "quality.py"


def load_quality():
    spec = importlib.util.spec_from_file_location("wc2_quality", QUALITY)
    q = importlib.util.module_from_spec(spec); spec.loader.exec_module(q)
    return q


Q = load_quality()


def parse_spec(text: str) -> tuple:
    """'whisper-cpp@2#1' → ('whisper-cpp@2', 1), 'whisper-cpp@2' → ('whisper-cpp@2', None)"""
    key, _, n = text.strip().partition("#")
    P.parse_key(key)
    if n and (not n.isdigit() or int(n) < 1): raise ValueError(f"설정 버전은 1 이상 정수: {text}")
    return key, int(n) if n else None


def to_clip(src: Path, dst: Path, start: float | None, duration: float | None) -> int:
    """src → 16kHz 모노 wav(잘라서). → 길이(ms)"""
    if not AN.FFMPEG: sys.exit(f"없음: {AN.A / 'ffmpeg'}/**/ffmpeg.exe")
    cut = (["-ss", str(start)] if start else []) + (["-t", str(duration)] if duration else [])
    subprocess.run([str(AN.FFMPEG), "-y", "-loglevel", "error", *cut, "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    return (dst.stat().st_size - 44) // 2 * 1000 // 16000


def measure(segs: list, words: bool) -> tuple:
    """→ (잰 구간들, 지표). words면 analyze.py처럼 문장 구간으로 묶는다"""
    shown = SP.group_words([dict(s) for s in segs]) if words else segs
    m = Q.metrics(shown)
    m["segments"] = len(shown)
    m["suspect"] = sum(1 for s in shown if s.get("suspect"))
    return shown, m


def run_one(c: "AN.Chosen", wav: Path, words: bool) -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        t0 = time.time()
        segs = P.run(c.pipe, c.settings, wav, Path(tmp), words=words, tools=AN.A)
        sec = round(time.time() - t0, 1)
        try:
            stats = json.loads((Path(tmp) / f"{wav.stem}.segments.json").read_text(encoding="utf-8")).get("stats")
        except (OSError, ValueError):
            stats = None
    shown, m = measure(segs, words)
    return {"pipeline": c.pipe.key, "settingsVersion": c.settings_version, "settings": c.settings, "seconds": sec, "metrics": m,
            "stats": stats, "segments": shown}


def table(rows: list) -> str:
    cols = ["조각", "파이프라인", "시간(초)", "구간", "글자", "말(초)", "글자/초", "최대 구절 반복", "최대 같은 글자", "압축률", "suspect 구간"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        m = r["metrics"]
        out.append("| " + " | ".join(str(x) for x in (r["clip"], f"{r['pipeline']}#{r['settingsVersion']}", r["seconds"], m["segments"], m["chars"],
                                                         m["speechSec"], m["charsPerSec"], m["phraseRepeat"], m["charRun"], m["compression"], m["suspect"])) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="녹음 조각(wav·m4a 등)")
    ap.add_argument("--pipeline", action="append", required=True, help="<이름>@<버전>[#설정 버전] (여러 번)")
    ap.add_argument("--start", type=float, help="입력의 이 초부터")
    ap.add_argument("--duration", type=float, help="이 초만큼")
    ap.add_argument("--words", action="store_true", help="단어 단위 전사(phone·call·laptop-mic과 같음)")
    ap.add_argument("--show", type=int, default=0, help="파이프라인마다 앞 N 구간을 보인다")
    ap.add_argument("--json", help="전체 결과(구간 포함)를 이 파일로")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    pipes = P.discover(warn=lambda m: print("  " + m, file=sys.stderr))
    try:
        chosen = []
        for spec in a.pipeline:
            key, n = parse_spec(spec)
            chosen.append(AN.choose_pipeline(key, n, None, pipes, create=False))
    except ValueError as e:
        sys.exit(str(e))
    busy = runlock.acquire(["compare.py"] + sys.argv[1:])
    if busy: sys.exit(f"analyze.py가 돌고 있습니다(PID {busy.get('pid')}). 끝난 뒤 다시 실행하세요.")
    rows = []
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for i, src in enumerate(a.inputs):
                wav = Path(tmp) / f"clip{i}.wav"
                ms = to_clip(Path(src), wav, a.start, a.duration)
                label = Path(src).name + (f" @{a.start:g}s" if a.start else "") + f" ({ms / 1000:.0f}초)"
                for c in chosen:
                    print(f"{label} × {c.label()} …", flush=True)
                    try:
                        r = run_one(c, wav, a.words)
                    except RuntimeError as e:   # 한 파이프라인이 실패해도 나머지는 돌고, 이미 돈 결과는 남긴다
                        print("   실패:", str(e)[-300:], flush=True)
                        continue
                    r["clip"] = label
                    rows.append(r)
                    print(f"   {r['seconds']}초" + (" " + json.dumps(r["stats"], ensure_ascii=False) if r["stats"] else ""), flush=True)
                    if a.json: Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        runlock.release()
    print()
    print(table(rows))
    if a.show:
        for r in rows:
            print(f"\n## {r['clip']} × {r['pipeline']}#{r['settingsVersion']}")
            for s in r["segments"][:a.show]:
                print(f"  {s['startMs'] / 1000:7.1f}–{s['endMs'] / 1000:7.1f}{' [의심]' if s.get('suspect') else ''}  {s['text']}")


if __name__ == "__main__":
    main()
