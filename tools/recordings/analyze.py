"""녹음 2차 분석(2026-11-11): 노트북(eGPU RX 6600, Vulkan)에서 전사·요약하고 결과만 SecondBrain API에 올린다. 원본 오디오는 올리지 않는다.

  python analyze.py                 # 아직 분석하지 않은 녹음 전부
  python analyze.py --file X.m4a --dry-run   # 파일 하나 시험(올리지 않음)
  python analyze.py --redo          # 이미 분석한 것도 다시

녹음 위치: %USERPROFILE%/SecondBrainRecordings/<원천>/<날짜>/<이름>.m4a
  phone/           폰 녹음(pull-recordings.ps1이 가져옴). 기기 phone
  discord-output/  디스코드 출력 장치 녹음(데스크톱 수집기). 기기 laptop
  discord-bot/     디스코드 봇 녹음(데스크톱 수집기, 사람별 파일 + 옆 .json에 화자). 기기 laptop. 세그먼트 speaker = 디스코드 표시 이름
  call/            삼성 통화 녹음(pull-call-recordings.ps1이 가져옴). 기기 phone, file은 "call/<날짜>/통화 <상대>_<YYMMDD>_<HHMMSS>.m4a"
화자 구분: speakers.py(sherpa-onnx). phone·call만. '나' 기준은 python speakers.py enroll로 먼저 만든다(없으면 화자1·화자2…만)
도구·모델: %LOCALAPPDATA%/SecondBrain/analysis (whisper.cpp Vulkan 빌드, ffmpeg, llama.cpp Vulkan, 모델들)
  전사 batisay-ko-turbo q8_0 + Silero VAD(말소리 구간만), 요약 EXAONE 3.5 7.8B Q4_K_M(llama-server)
API: %LOCALAPPDATA%/SecondBrain/api.json의 url·key(훅 키)
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"])
A = LOCAL / "SecondBrain" / "analysis"
WHISPER = A / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
ASR_MODEL = A / "models" / "ggml-batisay-ko-turbo-q8_0.bin"
VAD_MODEL = A / "models" / "ggml-silero-v5.1.2.bin"
LLM_MODEL = A / "models" / "EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"
LLAMA = A / "llama" / "llama-server.exe"
FFMPEG = next((A / "ffmpeg").rglob("ffmpeg.exe"), None)
ROOT = Path(os.environ["USERPROFILE"]) / "SecondBrainRecordings"
SOURCES = {"phone": "phone", "discord-output": "laptop", "discord-bot": "laptop", "call": "phone"}   # 폴더 → 기기
REL_PREFIX = {"call": "call/"}   # 같은 기기(phone)의 폰 녹음과 file 이름이 겹치지 않게
LLM_PORT = 8089
# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적)
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|[.\s,!?♪]*)$")


def api():
    cfg = json.load(open(LOCAL / "SecondBrain" / "api.json", encoding="utf-8"))
    base, key = cfg["url"].rstrip("/"), cfg["key"]

    def call(method, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode("utf-8")
            return json.loads(t) if t else None
    return call


def started_from_name(path: Path) -> dt.datetime:
    m = re.search(r"_(\d{6})_(\d{6})$", path.stem)   # 통화 녹음: 통화 <상대>_YYMMDD_HHMMSS
    if m:
        return dt.datetime.strptime("20" + m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone()
    m = re.match(r"(\d{8})-(\d{6})", path.stem)
    if not m:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").astimezone()


# ---- 디스코드 봇 녹음(discord-bot) ----
# 수집기 DiscordBotSource가 사람별 파일 옆에 같은 이름의 .json을 둔다:
#   { user_id, speaker(표시 이름), username, guild_id, guild, channel_id, channel, start, end, duration_ms, ... }
# 파일 하나 = 한 사람이므로 모든 세그먼트의 speaker를 그 사람의 표시 이름으로 채운다.

def discord_bot_meta(f: Path):
    """봇 녹음 옆 .json. 없거나 깨졌으면 None."""
    side = f.with_suffix(".json")
    try:
        return json.load(open(side, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def apply_discord_bot(f: Path, segs: list, start: dt.datetime):
    """세그먼트 speaker에 디스코드 표시 이름을 넣고, 시작 시각을 .json의 것으로 바꾼다. (start, 요약 문맥)을 돌려준다."""
    meta = discord_bot_meta(f)
    if not meta:
        return start, None
    speaker = meta.get("speaker") or meta.get("username") or meta.get("user_id")
    for s in segs:
        s["speaker"] = speaker
    if meta.get("start"):
        try:
            start = dt.datetime.fromisoformat(meta["start"])
        except ValueError:
            pass
    where = " / ".join(x for x in (meta.get("guild"), meta.get("channel")) if x)
    return start, f"디스코드 음성 채널({where})에서 {speaker}이(가) 한 말"


def source_of(f: Path) -> str:
    """--file로 받은 파일의 원천: .../SecondBrainRecordings/<원천>/<날짜>/<이름>.m4a"""
    parent = f.parent.parent.name
    return parent if parent in SOURCES else "phone"


def to_wav(src: Path, dst: Path):
    subprocess.run([str(FFMPEG), "-y", "-loglevel", "error", "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)
    return (dst.stat().st_size - 44) // 2 * 1000 // 16000   # 길이(ms)


def transcribe(wav: Path, out_base: Path, words: bool = False):
    """words=True면 단어마다 한 구간(-ml 1 -sow). 화자 구분(assign_speakers)이 화자별로 다시 묶는다"""
    cmd = [str(WHISPER), "-m", str(ASR_MODEL), "-l", "ko", "-f", str(wav), "-oj", "-of", str(out_base), "-np",
           "--vad", "-vm", str(VAD_MODEL), "-vsd", "500", "-vp", "200"] + (["-ml", "1", "-sow"] if words else [])
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("whisper 실패: " + p.stderr[-400:])
    data = json.load(open(str(out_base) + ".json", encoding="utf-8"))
    segs = []
    for s in data.get("transcription", []):
        text = (s.get("text") or "").strip()
        if not text or HALLUCINATIONS.match(text):
            continue
        segs.append({"startMs": int(s["offsets"]["from"]), "endMs": int(s["offsets"]["to"]), "speaker": None, "text": text})
    return segs


class Llm:
    """llama-server를 한 번 띄워 두고 요약에 쓴다. 필요할 때만(긴 전사가 있을 때) 띄운다."""
    def __init__(self):
        self.proc = None

    def summarize(self, transcript: str, context: str | None = None) -> str:
        if self.proc is None:
            self.proc = subprocess.Popen([str(LLAMA), "-m", str(LLM_MODEL), "--port", str(LLM_PORT), "-ngl", "99", "-c", "8192", "--log-disable"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(120):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{LLM_PORT}/health", timeout=2); break
                except Exception:
                    time.sleep(1)
        body = {"messages": [
            {"role": "system", "content": "너는 개인 기록 정리 도우미다. 사용자가 하루 동안 폰으로 녹음한 소리의 전사를 받는다. 전사는 음성 인식 결과라 틀린 글자가 있을 수 있다. "
                                          "무슨 상황이었는지(대화·수업·혼잣말·영상 소리 등)와 핵심 내용을 한국어로 3줄 이내 개조식으로 요약한다. 없는 내용을 지어내지 않는다."},
            {"role": "user", "content": (f"[{context}]\n" if context else "") + transcript[:12000]}], "temperature": 0.2, "max_tokens": 300}
        req = urllib.request.Request(f"http://127.0.0.1:{LLM_PORT}/v1/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)["choices"][0]["message"]["content"].strip()

    def close(self):
        if self.proc:
            self.proc.terminate()


SPEAKER_SOURCES = ("phone", "call")   # 화자 구분(speakers.py)을 하는 원천. 전사를 단어 단위로 받는다


def assign_speakers(source: str, f: Path, wav: Path, words: list, state: dict):
    """단어 구간(transcribe(words=True))에 화자를 붙이고 화자·틈 단위로 다시 묶는다. 통화는 나·상대 이름, 폰 녹음은 나·화자1·화자2….
    sherpa-onnx나 모델이 없으면 화자 없이 묶기만 한다. → (구간들, 통화 상대 이름 또는 None)"""
    import speakers as sp   # numpy만 쓴다. sherpa-onnx는 Diarizer가 부를 때
    partner = sp.call_partner(f) if source == "call" else None
    if words and "d" not in state:
        try:
            state["d"], state["me"] = sp.Diarizer(), sp.Me.load()
            if state["me"] is None: print("  ('나' 기준 없음: python speakers.py enroll)")
        except Exception as e:
            print("  화자 구분 건너뜀:", e); state["d"] = None
    if words and state.get("d"):
        turns, embs = state["d"].diarize(sp.load_wav(wav))   # 화자 수는 정하지 않는다(잘게 나눈 뒤 name_clusters가 합친다)
        sp.label_segments(words, turns, sp.name_clusters(embs, state["me"], turns, partner))
    segs = [s for s in sp.group_words(words) if not HALLUCINATIONS.match(s["text"])]
    return segs, partner


def labeled(segs: list) -> str:
    """요약에 줄 전사. 화자가 있으면 바뀔 때마다 '이름: '을 붙인다"""
    out, last = [], object()
    for s in segs:
        if s["speaker"] and s["speaker"] != last:
            out.append(f"\n{s['speaker']}: {s['text']}"); last = s["speaker"]
        else:
            out.append(s["text"])
    return " ".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file"); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--redo", action="store_true")
    ap.add_argument("--min-summary-chars", type=int, default=60)
    a = ap.parse_args()
    for p in (WHISPER, ASR_MODEL, VAD_MODEL, FFMPEG):
        if not p or not Path(p).exists(): sys.exit(f"없음: {p}")
    call = None if a.dry_run else api()

    jobs = []   # (원천, 기기, 상대 경로, 절대 경로)
    if a.file:
        f = Path(a.file).resolve()
        src = source_of(f)
        jobs.append((src, SOURCES[src], REL_PREFIX.get(src, "") + f.relative_to(ROOT / src).as_posix() if f.is_relative_to(ROOT / src) else f.name, f))
    else:
        for folder, device in SOURCES.items():
            base = ROOT / folder
            if not base.exists(): continue
            done = set() if a.redo else set(call("GET", "/api/recordings/analyzed?device=" + urllib.parse.quote(device)) or [])
            for f in sorted(base.rglob("*.m4a")):
                rel = REL_PREFIX.get(folder, "") + f.relative_to(base).as_posix()
                if rel not in done: jobs.append((folder, device, rel, f))
    print(f"분석할 녹음 {len(jobs)}개")

    llm = Llm()
    spk = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for i, (source, device, rel, f) in enumerate(jobs, 1):
                t0 = time.time()
                wav = Path(tmp) / "a.wav"
                dur = to_wav(f, wav)
                segs = transcribe(wav, Path(tmp) / "a", words=source in SPEAKER_SOURCES)
                partner = None
                if source in SPEAKER_SOURCES:
                    segs, partner = assign_speakers(source, f, wav, segs, spk)
                transcript = " ".join(s["text"] for s in segs)
                speech = sum(s["endMs"] - s["startMs"] for s in segs)
                start, context = started_from_name(f), None
                if source == "discord-bot":
                    start, context = apply_discord_bot(f, segs, start)
                elif source == "discord-output":
                    context = "디스코드 음성 통화에서 내 PC로 들린 소리(내 목소리는 없음)"
                summary = None
                if partner:
                    context = f"전화 통화 녹음. 상대: {partner}. '나'는 녹음한 사용자 본인"
                if len(transcript) >= a.min_summary_chars and LLM_MODEL.exists():
                    summary = llm.summarize(labeled(segs) if any(s["speaker"] for s in segs) else transcript, context)
                if partner and summary:
                    summary = f"통화 상대: {partner}\n{summary}"
                body = {"device": device, "file": rel, "source": source, "startedAt": start.isoformat(), "endedAt": (start + dt.timedelta(milliseconds=dur)).isoformat(),
                        "asrModel": ASR_MODEL.stem.removeprefix("ggml-"), "llmModel": LLM_MODEL.stem if summary else None, "language": "ko",
                        "speechMs": speech, "transcript": transcript, "summary": summary, "segments": segs}
                if call:
                    call("PUT", "/api/recordings/analysis", body)
                print(f"[{i}/{len(jobs)}] {rel}  {dur/1000:.0f}초 → 말 {speech/1000:.0f}초, {len(segs)}구간 ({time.time()-t0:.1f}초)")
                if a.dry_run:
                    for s in segs: print(f"    {s['startMs']/1000:7.1f}–{s['endMs']/1000:7.1f}  {(s['speaker'] + ': ') if s['speaker'] else ''}{s['text']}")
                    if summary: print("  요약:", summary)
    finally:
        llm.close()


if __name__ == "__main__":
    main()
