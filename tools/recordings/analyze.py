"""녹음 2차 분석(2026-11-11): 노트북(eGPU RX 6600, Vulkan)에서 전사·요약하고 결과만 SecondBrain API에 올린다. 원본 오디오는 올리지 않는다.

  python analyze.py                 # 아직 전사하지 않은 묶음 전부를 전사(+화자 구분)만(진행 중인 묶음은 건너뜀)
  python analyze.py --stage all     # 전사하고 요약까지(요약은 일단 쓰지 않음, 2026-11-11 사용자 결정)
  python analyze.py --plan          # 묶음과 상태(분석됨·분석할 것·진행 중)만 보기
  python analyze.py --file X.m4a --dry-run   # 그 파일이 든 묶음 하나 시험(올리지 않음)
  python analyze.py --source phone --limit 2 --dry-run
  python analyze.py --redo          # 이미 분석한 것도 다시
  python analyze.py --source phone --pick 20261111-140200.m4a   # 그 파일이 든 묶음 하나(이미 분석했어도 다시)
  python analyze.py --progress-json # 사람이 읽는 줄 사이에 "@@progress {json}" 줄(데스크톱 녹음 화면이 읽음)

동시 실행 막기(runlock.py): %LOCALAPPDATA%/SecondBrain/analysis/analyze.lock + 명령줄에 analyze.py가 든 다른 python 프로세스. 이미 돌고 있으면 종료 코드 3.
진행 중인 묶음(마지막 파일 뒤 간격이 안 지남)은 --file·--pick으로 골라도 분석하지 않는다(종료 코드 4).

묶음(2026-11-11, bundles.py): 같은 출처에서 앞 파일 끝~다음 파일 시작이 간격 기준(API GET /api/recordings/bundle-rule, 기본 60초) 이하면 한 묶음,
최대 30분(넘으면 가장 긴 간격에서 나눔), 통화는 파일 하나가 한 묶음. 전사는 파일마다 해서 묶음 시각으로 옮기고, 화자 구분(간격을 무음으로 채워 이어 붙인 오디오)과 요약은 묶음 전체로 한 번에 하며,
결과는 묶음 한 줄(file = 첫 파일, files = 든 파일·오프셋, 구간 시각은 묶음 시작 기준)로 올린다.

녹음 위치: %USERPROFILE%/SecondBrainRecordings/<원천>/<날짜>/<이름>.m4a
  phone/           폰 녹음(pull-recordings.ps1이 가져옴). 기기 phone
  discord-output/  디스코드 출력 장치 녹음(데스크톱 수집기). 기기 laptop
  discord-bot/     디스코드 봇 녹음(데스크톱 수집기, 사람별 파일 + 옆 .json에 화자). 기기 laptop. 세그먼트 speaker = 디스코드 표시 이름
  call/            삼성 통화 녹음(pull-call-recordings.ps1이 가져옴). 기기 phone, file은 "call/<날짜>/통화 <상대>_<YYMMDD>_<HHMMSS>.m4a"
  laptop-mic/      노트북 마이크 상시 녹음(데스크톱 수집기). 기기 laptop, file은 "laptop-mic/<날짜>/<이름>.m4a". 기숙사에선 룸메이트 목소리도 잡혀 화자 구분 대상
화자 구분: speakers.py(sherpa-onnx). phone·call·laptop-mic. '나' 기준은 python speakers.py enroll로 먼저 만든다(없으면 화자1·화자2…만)
도구·모델: %LOCALAPPDATA%/SecondBrain/analysis (whisper.cpp Vulkan 빌드, ffmpeg, llama.cpp Vulkan, 모델들)
  전사 batisay-ko-turbo q8_0 + Silero VAD(말소리 구간만), 요약 EXAONE 3.5 7.8B Q4_K_M(llama-server)
API: %LOCALAPPDATA%/SecondBrain/api.json의 url·key(훅 키). 환경 변수 SECONDBRAIN_API·SECONDBRAIN_API_KEY가 있으면 그것(시험 인스턴스)
단계(--stage, 2026-11-11): transcribe(기본) = 전사·화자 구분만 올린다(요약 비움). all = 예전처럼 전사하고 바로 요약
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bundles as B
import runlock
import trash as T

LOCAL = Path(os.environ["LOCALAPPDATA"])
A = LOCAL / "SecondBrain" / "analysis"
WHISPER = A / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli.exe"
ASR_MODEL = A / "models" / "ggml-batisay-ko-turbo-q8_0.bin"
VAD_MODEL = A / "models" / "ggml-silero-v5.1.2.bin"
LLM_MODEL = A / "models" / "EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"
LLAMA = A / "llama" / "llama-server.exe"
FFMPEG = next((A / "ffmpeg").rglob("ffmpeg.exe"), None)
ROOT = Path(os.environ["USERPROFILE"]) / "SecondBrainRecordings"
SOURCES = {"phone": "phone", "discord-output": "laptop", "discord-bot": "laptop", "call": "phone", "laptop-mic": "laptop"}   # 폴더 → 기기
REL_PREFIX = {"call": "call/", "laptop-mic": "laptop-mic/"}   # 같은 기기의 다른 출처와 file 이름이 겹치지 않게
LLM_PORT = 8089
# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적)
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|[.\s,!?♪]*)$")


def api():
    # 시험 인스턴스: 환경 변수 SECONDBRAIN_API·SECONDBRAIN_API_KEY가 api.json보다 앞선다(데스크톱 Settings와 같은 이름. 데스크톱이 띄우면 물려받음)
    env_url, env_key = os.environ.get("SECONDBRAIN_API"), os.environ.get("SECONDBRAIN_API_KEY")
    cfg = {} if env_url and env_key else json.load(open(LOCAL / "SecondBrain" / "api.json", encoding="utf-8"))
    base, key = (env_url or cfg["url"]).rstrip("/"), env_key or cfg["key"]

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
# 파일 하나 = 한 사람. 묶음 안에서 사람(user_id)마다 트랙을 따로 이어 붙여 전사하고, 그 트랙 구간의 speaker를 그 사람의 표시 이름으로 채운다.

def discord_bot_meta(f: Path):
    """봇 녹음 옆 .json. 없거나 깨졌으면 None."""
    side = f.with_suffix(".json")
    try:
        return json.load(open(side, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def discord_bot_who(meta: dict | None):
    """(트랙 키, 화자 이름). .json이 없으면 ('', None)"""
    if not meta:
        return "", None
    speaker = meta.get("speaker") or meta.get("username") or meta.get("user_id")
    return str(meta.get("user_id") or speaker or ""), speaker


def discord_bot_context(metas: list) -> str:
    where = next((" / ".join(x for x in (m.get("guild"), m.get("channel")) if x) for m in metas if m), "")
    return f"디스코드 음성 채널({where})에서 나눈 말. 줄 앞 이름이 말한 사람"

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


SPEAKER_SOURCES = ("phone", "call", "laptop-mic")   # 화자 구분(speakers.py)을 하는 원천. 전사를 단어 단위로 받는다


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


# ---- 묶음(bundles.py) ----
# 같은 출처에서 간격이 짧은 파일들을 한 묶음으로 이어 붙여(간격은 무음으로 채워 시각을 맞춤) 한 번에 전사·화자 구분·요약한다.
# 결과 한 줄 = 묶음 하나(file = 첫 파일, files = 든 파일과 묶음 시작부터의 오프셋). 구간 시각은 묶음 시작 기준.

def m4a_duration_ms(path: Path) -> int:
    """MP4/M4A 길이(ms): 맨 위 상자에서 moov → mvhd의 timescale·duration. 못 읽으면 0. (데스크톱 RecordingsPage.M4aDurationMs와 같은 방식)"""
    import struct
    try:
        with open(path, "rb") as f:
            size_all = f.seek(0, 2)
            pos = 0
            while pos + 8 <= size_all:
                f.seek(pos)
                size, typ = struct.unpack(">I4s", f.read(8))
                head = 8
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]; head = 16
                elif size == 0:
                    size = size_all - pos
                if size < 8: return 0
                if typ == b"moov":
                    inner, end = pos + head, pos + size
                    while inner + 8 <= end:
                        f.seek(inner)
                        isize, ityp = struct.unpack(">I4s", f.read(8))
                        if ityp == b"mvhd":
                            ver = f.read(4)[0]
                            if ver == 1:
                                f.read(16); ts, du = struct.unpack(">IQ", f.read(12))
                            else:
                                f.read(8); ts, du = struct.unpack(">II", f.read(8))
                            return du * 1000 // ts if ts else 0
                        if isize < 8: return 0
                        inner += isize
                    return 0
                pos += size
    except (OSError, struct.error, IndexError):
        pass
    return 0


def epoch_ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).astimezone().isoformat()


def hms(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


def scan(folder: str) -> list:
    """원천 폴더의 녹음 → bundles.Item 목록. 시작은 파일 이름(디스코드 봇은 옆 .json), 끝은 시작 + m4a 길이."""
    base = ROOT / folder
    out = []
    for f in sorted(base.rglob("*.m4a")):
        rel = REL_PREFIX.get(folder, "") + f.relative_to(base).as_posix()
        start = epoch_ms(started_from_name(f))
        dur = m4a_duration_ms(f)
        track, speaker = "", None
        if folder == "discord-bot":
            meta = discord_bot_meta(f)
            track, speaker = discord_bot_who(meta)
            if meta and meta.get("start"):
                try: start = epoch_ms(dt.datetime.fromisoformat(meta["start"]))
                except ValueError: pass
            if meta and not dur and meta.get("duration_ms"): dur = int(meta["duration_ms"])
        out.append(B.Item(folder, rel, start, start + max(dur, 1), f, track, speaker))
    return out


def bundle_rule(get) -> dict:
    try:
        r = get("/api/recordings/bundle-rule")
        return {"gapSec": int(r["gapSec"]), "maxSec": int(r["maxSec"]), "singleSources": list(r.get("singleSources") or [])}
    except Exception as e:
        print("  묶음 규칙을 API에서 못 받아 기본값을 쓴다:", e)
        return dict(B.DEFAULT_RULE)


def read_wav_i16(path: Path):
    import numpy as np, wave
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav_i16(path: Path, samples):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(samples.tobytes())


def join_wavs(b: "B.Bundle", wavs: list, dst: Path) -> Path:
    """묶음 길이의 무음 위에 파일들을 제 시각(묶음 시작 기준 오프셋)에 얹은 16kHz wav. 겹치면 더한다."""
    import numpy as np
    total = (b.end - b.start) * 16
    buf = np.zeros(total, dtype=np.int32)
    for it, wav in zip(b.items, wavs):
        x = read_wav_i16(wav)
        at = (it.start - b.start) * 16
        n = max(0, min(len(x), total - at))
        buf[at:at + n] += x[:n]
    write_wav_i16(dst, np.clip(buf, -32768, 32767).astype(np.int16))
    return dst


def analyze_bundle(b: "B.Bundle", tmp: Path, llm: "Llm | None", spk: dict, min_summary_chars: int):
    """묶음 하나를 전사·화자 구분(llm이 있으면 요약까지). → API 본문(segments 포함)
    전사는 파일마다 하고 시각에 오프셋을 더한다: 무음을 채워 이어 붙인 긴 wav를 whisper.cpp VAD로 돌리면 구간 시각이 수백 초씩 어긋났다(2026-11-11 실측).
    녹음은 5초 무음 뒤에만 파일을 끊으므로 경계에서 말이 잘리는 일은 드물다. 화자 구분(sherpa)과 요약은 묶음 전체로 한 번에."""
    source, first = b.source, b.items[0]
    words_mode = source in SPEAKER_SOURCES
    wavs, segs, partner = [], [], None
    for k, it in enumerate(b.items):
        progress("step", stage="transcribe", file=k + 1, files=len(b.items))
        wav = tmp / f"p{k}.wav"
        to_wav(it.path, wav)
        wavs.append(wav)
        part = transcribe(wav, tmp / "a", words=words_mode)
        off = it.start - b.start
        for s in part:
            s["startMs"] += off; s["endMs"] += off
            if source == "discord-bot": s["speaker"] = it.speaker
        segs += part
    segs.sort(key=lambda s: (s["startMs"], s["endMs"]))
    if words_mode:
        progress("step", stage="speakers")
        whole = wavs[0] if len(wavs) == 1 else join_wavs(b, wavs, tmp / "bundle.wav")
        segs, partner = assign_speakers(source, first.path, whole, segs, spk)
    transcript = " ".join(s["text"] for s in segs)
    speech = sum(s["endMs"] - s["startMs"] for s in segs)
    context = None
    if source == "discord-bot":
        context = discord_bot_context([discord_bot_meta(it.path) for it in b.items])
    elif source == "discord-output":
        context = "디스코드 음성 통화에서 내 PC로 들린 소리(내 목소리는 없음)"
    elif source == "laptop-mic":
        context = "노트북 마이크로 녹음한 소리(나와 방 안 다른 사람 목소리가 섞일 수 있음)"
    if partner:
        context = f"전화 통화 녹음. 상대: {partner}. '나'는 녹음한 사용자 본인"
    if len(b.items) > 1:
        context = (context + ". " if context else "") + f"녹음 파일 {len(b.items)}개를 시간순으로 이어 붙인 것"
    summary = None
    if llm is not None and len(transcript) >= min_summary_chars and LLM_MODEL.exists():
        progress("step", stage="summary")
        summary = llm.summarize(labeled(segs) if any(s["speaker"] for s in segs) else transcript, context)
    if partner and summary:
        summary = f"통화 상대: {partner}\n{summary}"
    files = [{"file": it.rel, "startedAt": iso(it.start), "endedAt": iso(it.end), "offsetMs": it.start - b.start} for it in b.items]
    return {"device": SOURCES[source], "file": first.rel, "source": source, "startedAt": iso(b.start), "endedAt": iso(b.end),
            "asrModel": ASR_MODEL.stem.removeprefix("ggml-"), "llmModel": LLM_MODEL.stem if summary else None, "language": "ko",
            "speechMs": speech, "transcript": transcript, "summary": summary, "segments": segs, "files": files}


PROGRESS = False


def progress(event: str, **kw):
    """--progress-json이면 기계가 읽는 진행 한 줄. 사람이 읽는 줄과 섞여 나오므로 접두어로 가른다."""
    if PROGRESS:
        print("@@progress " + json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def bundle_info(b: "B.Bundle") -> dict:
    return {"source": b.source, "first": Path(b.items[0].rel).name, "start": iso(b.start), "end": iso(b.end), "files": len(b.items)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="이 파일이 든 묶음 하나만")
    ap.add_argument("--dry-run", action="store_true", help="분석은 하되 올리지 않는다")
    ap.add_argument("--plan", action="store_true", help="묶음과 상태만 보이고 분석하지 않는다")
    ap.add_argument("--redo", action="store_true", help="이미 분석한 묶음도 다시")
    ap.add_argument("--limit", type=int, help="묶음 최대 N개만")
    ap.add_argument("--stage", choices=("transcribe", "all"), default="transcribe", help="transcribe = 전사·화자 구분만(기본), all = 요약까지")
    ap.add_argument("--min-summary-chars", type=int, default=60)
    ap.add_argument("--source", choices=list(SOURCES), help="이 원천 폴더만(예: phone)")
    ap.add_argument("--pick", help="--source 안에서 이 파일 이름이 든 묶음 하나만(이미 분석했어도 다시)")
    ap.add_argument("--progress-json", action="store_true", help='진행을 "@@progress {json}" 줄로도 낸다')
    ap.add_argument("--no-auto-remove", action="store_true", help="말소리 없는 묶음을 휴지통으로 옮기지 않는다")
    ap.add_argument("--sweep-silent", action="store_true", help="전사하지 않고, 이미 전사된 묶음 중 말소리 없는 것을 휴지통으로(--dry-run이면 목록만)")
    a = ap.parse_args()
    global PROGRESS
    PROGRESS = a.progress_json
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)   # 앱이 파이프로 읽어도 한 줄씩 바로·한글 그대로
    except (AttributeError, ValueError):
        pass
    if a.pick and not a.source: ap.error("--pick에는 --source가 필요합니다")
    if not a.plan:
        busy = runlock.acquire(sys.argv[1:])
        if busy:
            progress("busy", pid=busy.get("pid"), started=busy.get("started"), cmd=busy.get("cmd"), via=busy.get("via"))
            print(f"이미 analyze.py가 돌고 있습니다(PID {busy.get('pid')}" + (f", {busy['started']} 시작" if busy.get("started") else "") + "). 끝난 뒤 다시 실행하세요.")
            sys.exit(3)
    try:
        run(a)
    except BaseException as e:
        if not isinstance(e, SystemExit) or e.code not in (0, None, 4):
            progress("error", message=str(e) or type(e).__name__)
        raise
    finally:
        runlock.release()


def sweep_silent(a, call):
    """이미 전사된 묶음 중 말소리 없는 것(서버 분석 글 0자)을 휴지통으로. 묶음 파일이 이 PC에 모두 있을 때만."""
    rows = call("GET", "/api/recordings?from=2000-01-01&to=2100-01-01") or []
    now = epoch_ms(dt.datetime.now().astimezone())
    gone = T.listed(ROOT)
    rule, local = bundle_rule(lambda path: call("GET", path)), {}
    n, n_files = 0, 0
    for r in sorted(rows, key=lambda r: r["startedAt"]):
        src = r.get("source")
        if src not in T.SOURCES or (a.source and src != a.source): continue
        if not T.is_silent(r.get("segments") or [], r.get("transcript")): continue
        files = r.get("files") or [{"file": r["file"]}]
        if isinstance(files, str): files = json.loads(files)
        pre = REL_PREFIX.get(src, "")
        paths = [ROOT / src / f["file"].removeprefix(pre) for f in files]
        names = [p.name for p in paths]
        when = r["startedAt"][:16].replace("T", " ")
        if all((src, x) in gone for x in names): continue
        missing = [x for x, p in zip(names, paths) if not p.exists()]
        if missing:
            print(f"  건너뜀 {src} {when}: 이 PC에 없는 파일 {len(missing)}개({missing[0]}…)"); continue
        if src not in local:   # 지금 이 PC의 묶음. 전사 뒤 같은 묶음에 파일이 더 붙었으면(말이 있을 수 있음) 건너뛴다
            local[src] = {it.path.name: bb for bb in B.make_bundles(scan(src), rule) for it in bb.items}
        cur = local[src].get(names[0])
        if cur is None or {it.path.name for it in cur.items} != set(names):
            print(f"  건너뜀 {src} {when}: 전사 뒤 묶음이 바뀜(다시 전사 필요)"); continue
        if B.in_progress(cur, now, rule): continue
        n += 1; n_files += len(paths)
        if a.dry_run:
            print(f"  후보 {src} {when}  파일 {len(paths)}개")
        else:
            T.move_bundle(ROOT, src, paths, r["startedAt"], spans=[(f.get("startedAt", ""), f.get("endedAt", "")) for f in files])
            print(f"  휴지통으로 {src} {when}  파일 {len(paths)}개")
            progress("trashed", source=src, start=r["startedAt"], files=len(paths))
    print(f"말소리 없는 묶음 {n}개(파일 {n_files}개) " + ("— dry-run이라 옮기지 않음" if a.dry_run else f"휴지통으로 옮김({T.TRASH_DAYS}일 뒤 녹음 화면이 영구 삭제)"))


def run(a):
    if a.sweep_silent:
        return sweep_silent(a, api())
    if not a.plan:
        for p in (WHISPER, ASR_MODEL, VAD_MODEL, FFMPEG):
            if not p or not Path(p).exists(): sys.exit(f"없음: {p}")
    call = api()
    get = lambda path: call("GET", path)
    rule = bundle_rule(get)
    now = epoch_ms(dt.datetime.now().astimezone())
    print(f"묶음 규칙: 간격 {rule['gapSec']}초 이하 · 최대 {rule['maxSec'] // 60}분 · 묶지 않음 {', '.join(rule['singleSources']) or '-'}")

    target = Path(a.file).resolve() if a.file else None
    folders = [source_of(target)] if target else [x for x in SOURCES if not a.source or x == a.source]
    jobs, n_done, n_progress = [], 0, 0
    for folder in folders:
        if not (ROOT / folder).exists(): continue
        bundles = B.make_bundles(scan(folder), rule)
        if target or a.pick:
            hit = lambda it: Path(it.path).resolve() == target if target else Path(it.rel).name == a.pick
            for b in bundles:
                if not any(hit(it) for it in b.items): continue
                if B.in_progress(b, now, rule):
                    progress("refused", reason="in-progress", **bundle_info(b))
                    print(f"진행 중인 묶음이라 분석하지 않습니다({hms(b.start)} ~ {hms(b.end)[6:]}, 마지막 파일 뒤 {rule['gapSec']}초가 지나지 않음).")
                    sys.exit(4)
                jobs.append(b)
            continue
        done = set() if a.redo else set(get("/api/recordings/analyzed?device=" + urllib.parse.quote(SOURCES[folder])) or [])
        for b in bundles:
            state = "진행 중" if B.in_progress(b, now, rule) else "분석됨" if all(it.rel in done for it in b.items) else "분석할 것"
            if a.plan:
                print(f"  {folder:14} {hms(b.start)} ~ {hms(b.end)[6:]}  파일 {len(b.items):3}개  {state}")
            if state == "진행 중": n_progress += 1
            elif state == "분석됨": n_done += 1
            else: jobs.append(b)
    if a.limit: jobs = jobs[:a.limit]
    if (target or a.pick) and not jobs:
        progress("missing", file=a.file or a.pick, source=a.source)
        print("그 파일이 든 묶음을 찾지 못했습니다(이 PC의 녹음 폴더에 파일이 없음):", a.file or f"{a.source}/{a.pick}")
    print(("전사만 합니다(요약 안 함). " if a.stage == "transcribe" else "") + f"분석할 묶음 {len(jobs)}개(파일 {sum(len(b.items) for b in jobs)}개) · 이미 분석 {n_done} · 진행 중이라 건너뜀 {n_progress}")
    if not a.plan:
        progress("plan", total=len(jobs), files=sum(len(b.items) for b in jobs), done=n_done, inProgress=n_progress, dryRun=a.dry_run, stage=a.stage)
    if a.plan or not jobs:
        if not a.plan: progress("end", analyzed=0, total=0)
        return

    llm = Llm() if a.stage == "all" else None
    spk = {}
    try:
        for i, b in enumerate(jobs, 1):
            t0 = time.time()
            progress("start", i=i, total=len(jobs), **bundle_info(b))
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # 백신·색인기가 임시 파일을 잡고 있어도 멈추지 않게
                body = analyze_bundle(b, Path(tmp), llm, spk, a.min_summary_chars)
            if not a.dry_run:
                call("PUT", "/api/recordings/analysis", body)
            segs = body["segments"]
            print(f"[{i}/{len(jobs)}] {b.source} {hms(b.start)}~{hms(b.end)[6:]}  파일 {len(b.items)}개 {(b.end - b.start) / 1000:.0f}초"
                  f" → 말 {body['speechMs'] / 1000:.0f}초, {len(segs)}구간 ({time.time() - t0:.1f}초)")
            progress("done", i=i, total=len(jobs), speechMs=body["speechMs"], segments=len(segs), seconds=round(time.time() - t0, 1),
                     uploaded=not a.dry_run, **bundle_info(b))
            if not a.no_auto_remove and b.source in T.SOURCES and T.is_silent(segs, body["transcript"]):
                if a.dry_run:
                    print(f"    말소리 없음 → 휴지통 후보(dry-run이라 옮기지 않음)")
                else:
                    moved = T.move_bundle(ROOT, b.source, [it.path for it in b.items], iso(b.start), spans=[(iso(it.start), iso(it.end)) for it in b.items])
                    print(f"    말소리 없음 → 휴지통으로 파일 {len(moved)}개({T.TRASH_DAYS}일 뒤 영구 삭제, 녹음 화면에서 되살리기)")
                    progress("trashed", source=b.source, start=iso(b.start), files=len(moved))
            if a.dry_run:
                for s in segs: print(f"    {s['startMs'] / 1000:7.1f}–{s['endMs'] / 1000:7.1f}  {(s['speaker'] + ': ') if s['speaker'] else ''}{s['text']}")
                if body["summary"]: print("  요약:", body["summary"])
        progress("end", analyzed=len(jobs), total=len(jobs))
    finally:
        if llm: llm.close()

if __name__ == "__main__":
    main()
