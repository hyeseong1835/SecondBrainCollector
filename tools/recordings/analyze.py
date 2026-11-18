"""녹음 2차 분석(2026-11-11): 노트북(eGPU RX 6600, Vulkan)에서 전사·요약하고 결과만 SecondBrain API에 올린다. 원본 오디오는 올리지 않는다.

  python analyze.py                 # 아직 전사하지 않은 묶음 전부를 전사(+화자 구분)만(진행 중인 묶음은 건너뜀)
  python analyze.py --stage all     # 전사하고 요약까지(요약은 일단 쓰지 않음, 2026-11-11 사용자 결정)
  python analyze.py --plan          # 묶음과 상태(분석됨·분석할 것·진행 중)만 보기
  python analyze.py --file X.m4a --dry-run   # 그 파일이 든 묶음 하나 시험(올리지 않음)
  python analyze.py --source phone --limit 2 --dry-run
  python analyze.py --redo          # 이미 분석한 것도 다시
  python analyze.py --redo --redo-from whisper-cpp@1#1 --pipeline whisper-cpp@2   # 그 파이프라인 항목(여러 번 줄 수 있음)의 결과가 있는 묶음만 다시(데스크톱 전체 재전사)
  python analyze.py --source phone --pick 20261111-140200.m4a   # 그 파일이 든 묶음 하나(이미 분석했어도 다시). --pick을 여러 번 주면 그 묶음들
  python analyze.py --call 20261111-224007-ab12cd34.m4a   # 그 파일이 든 디스코드 통화(디스코드 일반 녹음+노트북 마이크)의 미전사 묶음 전부(--redo면 전부)
  python analyze.py --progress-json # 사람이 읽는 줄 사이에 "@@progress {json}" 줄(데스크톱 녹음 화면이 읽음)
    전사 중에는 파이프라인이 낸 진행(pipelines.py 머리말)을 이어 받아 {"event":"step","stage":"transcribe","file":k,"files":n,"percent":0~100,"text":"…"}(percent·text는 있을 때만, 초당 4번까지)
  python analyze.py --source phone --pick X.m4a --only-new   # 이미 전사된 묶음이면 건너뜀(데스크톱 전사 대기열이 씀)
  예상 시간(2026-11-17, eta.py): plan·start·step·done·error 줄에 "eta": {remainingSec, fileRemainingSec, endsAt, filesDone, filesTotal, audioDoneSec, audioTotalSec, …}
    (남은 시간 = 남은 파일마다 a × 오디오 초 + b, a·b는 이 PC 기록(파이프라인·설정 버전·실행 옵션별)과 이번 실행의 끝난 파일로 맞춤. 표본이 모자라면 null = 계산 중)

겹치기 실행(2026-11-18, run_flow): 묶음마다 준비(wav·단어장) → 전사(GPU) → 마무리(이름 붙이기·목소리 대조·요약·올리기)를 갈래로 겹친다.
  전사는 묶음 여러 개를 한 무리(조각 추정 GROUP_CHUNKS개·파일 GROUP_FILES개까지, 묶음은 나누지 않음)로: 여러 입력 파이프라인(multiInput)이면 무리의 파일 모두를
  파이프라인 한 번(모델 올리기·작업자 풀 한 번)에, 아니면 파일마다 차례로. 화자 분리(sherpa)는 준비가 끝나면 바로 다른 프로세스(DIAR_WORKERS개)에서 돌린다.
  마무리는 묶음 차례대로 하나씩(결과·목소리 저장소·올리기 차례가 전과 같다). 한 묶음이 실패해도 나머지는 계속, 무리 호출이 통째로 실패하면 묶음마다 다시.
  단어장 고르기는 실행 안에서 같은 본문이면 한 번만 묻는다(PICKS). --stage all(요약)이면 llama-server와 GPU를 나누지 않게 무리 사이를 겹치지 않는다.
  진행 줄: start {i, last, bundles, files = 무리의 파일 수, …}(무리 하나에 한 번), step transcribe에 groupFiles(무리 파일 수. 여러 입력이면 file = files = 1이고 percent가 무리 전체),
  done은 묶음 차례대로. 앞 무리 마무리가 다음 무리 전사와 겹치므로 done이 start보다 늦게 올 수 있다. eta는 eta.py Flow(무리마다 처리량 표본, fileRemainingSec = 지금 무리).
  중단(Ctrl+C·오류)이면 갈래를 멈추고 돌고 있는 파이프라인·whisper-cli·화자 분리 프로세스를 끝내고 임시 폴더를 지운다(데스크톱 취소는 작업 개체째 끝냄).
동시 실행 막기(runlock.py): %USERPROFILE%/.secondbrain/analysis/analyze.lock + 명령줄에 analyze.py가 든 다른 python 프로세스. 이미 돌고 있으면 종료 코드 3.
전사 대기열(2026-11-12): 데스크톱 녹음 화면이 %USERPROFILE%/.secondbrain/analysis/queue.json(2026-11-14 옮김)에 쌓아 두고 차례로 이 스크립트를 띄운다.
  묶음을 정하면 잠금 파일에 "plan"(이번 실행이 처리할 묶음 키 "<출처>/<첫 파일 이름>" 목록)을 적어, 대기열이 같은 묶음을 두 번 넣지 않게 한다.
진행 중인 묶음(마지막 파일 뒤 간격이 안 지남)은 --file·--pick으로 골라도 분석하지 않는다(종료 코드 4).

묶음(2026-11-11, bundles.py): 같은 출처에서 앞 파일 끝~다음 파일 시작이 간격 기준(API GET /api/recordings/bundle-rule, 기본 60초) 이하면 한 묶음,
최대 30분(넘으면 가장 긴 간격에서 나눔), 통화는 파일 하나가 한 묶음. 전사는 파일마다 해서 묶음 시각으로 옮기고, 화자 구분(간격을 무음으로 채워 이어 붙인 오디오)과 요약은 묶음 전체로 한 번에 하며,
결과는 묶음 한 줄(file = 첫 파일, files = 든 파일·오프셋, 구간 시각은 묶음 시작 기준)로 올린다.
디스코드 통화(2026-11-12, bundles.make_calls): 규칙의 callSources 묶음들이 간격 기준 안에 이어지면 한 통화. --call은 통화에 든 묶음을 출처별 기존 전사로 차례로 처리한다
(결과는 출처별 묶음 그대로 올리고, 합쳐 보이는 것은 데스크톱 녹음 화면).

녹음 위치: %USERPROFILE%/SecondBrainRecordings/<원천>/<날짜>/<이름>.m4a
  phone/           폰 녹음(pull-recordings.ps1이 가져옴). 기기 phone
  discord-output/  디스코드 출력 장치 녹음(데스크톱 수집기). 기기 laptop
  discord-bot/     디스코드 봇 녹음(데스크톱 수집기, 사람별 파일 + 옆 .json에 화자). 기기 laptop. 세그먼트 speaker = 디스코드 표시 이름
  call/            삼성 통화 녹음(pull-call-recordings.ps1이 가져옴). 기기 phone, file은 "call/<날짜>/통화 <상대>_<YYMMDD>_<HHMMSS>.m4a"
  laptop-mic/      노트북 마이크 상시 녹음(데스크톱 수집기). 기기 laptop, file은 "laptop-mic/<날짜>/<이름>.m4a". 기숙사에선 룸메이트 목소리도 잡혀 화자 구분 대상
화자 구분: speakers.py(sherpa-onnx). phone·call·laptop-mic·discord-output(2026-11-18, 내 목소리가 없어 '나'를 찾지 않고 화자N + 등록 목소리 이름만). '나' 기준은 python speakers.py enroll로 먼저 만든다(없으면 화자1·화자2…만)
  사람 목소리(2026-11-16, voices.py, 이 PC의 models/diar/voices/에만): 통화 전사 때 상대 목소리를 자동 등록(--dry-run이면 안 함),
  폰·노트북 마이크 녹음의 화자N은 등록된 목소리와 견줘 이름(덜 닮으면 구간에 speakerGuess: true, 서버는 아직 이 필드를 저장하지 않음). 목록·지우기: python speakers.py voices / forget
  디스코드 봇 녹음(2026-11-18): 전사 뒤 사람별 파일로 그 사람(디스코드 표시 이름, 별칭이 있으면 그 이름) 목소리를 등록(--dry-run이면 안 함)
도구·모델: %LOCALAPPDATA%/SecondBrain/analysis (whisper.cpp Vulkan 빌드, ffmpeg, llama.cpp Vulkan, 모델들)
  전사 모델은 고를 수 있다(asrmodels.py, 기본 batisay-ko-turbo q8_0) + Silero VAD(말소리 구간만), 요약 EXAONE 3.5 7.8B Q4_K_M(llama-server)
API: %USERPROFILE%/.secondbrain/api.json(없으면 옛 %LOCALAPPDATA%/SecondBrain/api.json, sbpaths.py)의 url·key(훅 키). 환경 변수 SECONDBRAIN_API·SECONDBRAIN_API_KEY가 있으면 그것(시험 인스턴스)
전사 파이프라인(2026-11-16, pipelines.py): 전사(wav → 구간)는 파이프라인 폴더(<이름>@<버전>/)의 스크립트가 하고, 여기는 wav 만들기·묶음·화자 구분·요약·업로드만.
  결과마다 pipeline {name, version, settingsVersion}을 붙이고, 올리기 전에 서버 목록에 등록한다(POST /api/transcription/pipelines).
  python analyze.py --list-pipelines   # 이 PC의 파이프라인과 설정 버전(JSON)
  python analyze.py --pipeline whisper-cpp@1 --settings-version 2 --source phone --pick X.m4a --dry-run   # 설정 버전을 안 주면 최신
  --pipeline을 안 주면 whisper-cpp@1에서 전사 모델(--asr-model → settings.json의 asrModel → batisay-ko-turbo-q8_0)이 같은 설정 버전(없으면 새로 씀).
  새로 쓰는 설정 버전 번호 = (이 PC, 서버 목록 GET /api/transcription/pipelines의 그 파이프라인) 중 최대 + 1. 서버 목록을 못 읽으면(옛 서버) 경고하고 이 PC 기준.
  미전사 = 어느 파이프라인의 결과도 없는 묶음(GET /api/recordings/analyzed). 다른 파이프라인으로 다시 돌리려면 --redo·--pick.
  실행 옵션(2026-11-17, pipelines.py 머리말): 결과를 바꾸지 않는 이 기기 값(예: parallel). analysis/settings.json의 runOptions → --run-option 키=값(여러 번).
  설정 버전·서버 기록에는 들어가지 않는다. python analyze.py --pipeline whisper-cpp@2 --run-option parallel=3 --source phone --pick X.m4a --dry-run
  자동 동시 실행(2026-11-18): parallel=auto(whisper-cpp@2·whisper-lite@1의 새 기본)면 파이프라인이 작업자 수를 스스로 조정한다. 진행 줄의 workers는
  step 이벤트에 그대로 싣고 eta.workers(지금·최소·최대·평균)에 모은다. 파일이 끝나면 stats.workers를 한 줄로 적는다(표준 오류).
단어장 힌트(2026-11-16, 계획 "3-① 단어장"): 매니페스트 hints인 파이프라인이고 설정 useHints가 꺼져 있지 않으면 묶음마다 POST /api/vocab/pick
  (통화면 caller = 통화 상대, max·maxChars = 설정 hintMax·hintChars)으로 힌트 파일을 만들어 --hints로 넘긴다. 서버를 못 읽으면 경고하고 이번 실행은 힌트 없이.
  올리기가 성공하면 파이프라인이 낸 vocabUsed(쓴 힌트·실제로 바꾼 고침 짝 id)로 POST /api/vocab/used(묶음 하나에 한 번). --plan은 고르지 않고, --dry-run은 고르기만.
전사 모델(--asr-model, 2026-11-13): models/ggml-*.bin 중 하나. 2026-11-16부터 whisper-cpp@1의 설정 asrModel로 바꿔 쓴다(데스크톱 호환, --pipeline whisper-cpp@1과만 같이 쓸 수 있음)
  python analyze.py --list-models   # 쓸 수 있는 모델과 기본값(JSON)
  python analyze.py --source phone --pick X.m4a --asr-model large-v3-turbo --dry-run
단계(--stage, 2026-11-11): transcribe(기본) = 전사·화자 구분만 올린다(요약 비움). all = 예전처럼 전사하고 바로 요약
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from dataclasses import dataclass, field
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import bundles as B
import runlock
import trash as T
import asrmodels as M
import pipelines as P
import eta as E
import sbpaths

LOCAL = Path(os.environ["LOCALAPPDATA"])
A = sbpaths.TOOLS   # 도구·모델(옮기지 않음)
LLM_MODEL = A / "models" / "EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"
LLAMA = A / "llama" / "llama-server.exe"
FFMPEG = next((A / "ffmpeg").rglob("ffmpeg.exe"), None)
ROOT = Path(os.environ["USERPROFILE"]) / "SecondBrainRecordings"
SOURCES = {"phone": "phone", "discord-output": "laptop", "discord-bot": "laptop", "call": "phone", "laptop-mic": "laptop"}   # 폴더 → 기기
REL_PREFIX = {"call": "call/", "laptop-mic": "laptop-mic/"}   # 같은 기기의 다른 출처와 file 이름이 겹치지 않게
LLM_PORT = 8089
LEGACY_PIPELINE = "whisper-cpp@1"   # --pipeline을 안 주면(--asr-model·settings.json asrModel을 이 파이프라인의 설정으로)
# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적). 전사는 파이프라인이 거르고, 여기선 화자 구분으로 다시 묶은 구간에 쓴다
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|한글\s*자막.*|[.\s,!?♪\-–—]*)$")   # large-v3-turbo는 화자가 바뀔 때 "-"만 든 단어, 무음에 "한글자막 by …"를 낸다(2026-11-13)


def api():
    # 시험 인스턴스: 환경 변수 SECONDBRAIN_API·SECONDBRAIN_API_KEY가 api.json보다 앞선다(데스크톱 Settings와 같은 이름. 데스크톱이 띄우면 물려받음)
    env_url, env_key = os.environ.get("SECONDBRAIN_API"), os.environ.get("SECONDBRAIN_API_KEY")
    cfg = {} if env_url and env_key else json.load(open(sbpaths.api_json(), encoding="utf-8"))
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


@dataclass
class Chosen:
    """이번 실행의 전사 파이프라인 항목과 그 설정(기본값 채움)"""
    pipe: P.Pipeline
    settings_version: int
    settings: dict
    run_options: dict = field(default_factory=dict)   # 실행 옵션(결과를 바꾸지 않음, ref()에 넣지 않는다)

    def ref(self) -> dict:
        return {"name": self.pipe.name, "version": self.pipe.version, "settingsVersion": self.settings_version}

    def label(self) -> str:
        run = (", 실행 옵션 " + ", ".join(f"{k}={v}" for k, v in self.run_options.items())) if self.run_options else ""
        return f"{self.pipe.key} 설정 {self.settings_version} (" + ", ".join(f"{k}={v}" for k, v in self.settings.items()) + ")" + run


CHOSEN: "Chosen | None" = None   # main()이 정한다


def missing_models(p: P.Pipeline, settings: dict) -> list:
    """설정 중 type whisper-model인데 models/에 없는 모델 이름들"""
    return [settings.get(k) for k, v in p.schema.items() if v.get("type") == "whisper-model" and M.resolve(str(settings.get(k) or ""))[1] is None]


def model_error(names: list) -> ValueError:
    have = ", ".join(n for n, _, _ in M.available()) or "없음"
    return ValueError(f"전사 모델 없음: {', '.join(map(str, names))} (설치된 모델: {have}, 폴더: {M.models_dir()})")


def choose_pipeline(pipeline: str | None, settings_version: int | None, asr_model: str | None, pipes: dict, root: Path | None = None, create: bool = True,
                    server=None) -> Chosen:
    """--pipeline·--settings-version·--asr-model → 항목. 고를 수 없으면 ValueError.
    --pipeline이 없으면 whisper-cpp@1에서 전사 모델(--asr-model → settings.json → 기본)이 같은 설정 버전(없으면 새로 씀, 데스크톱 호환).
    --pipeline만 주면 최신 설정 버전(하나도 없으면 기본값으로 1을 씀). create=False(--plan)면 새 설정 버전을 쓰지 않고 ValueError.
    새 설정 버전 번호 = (이 PC, server(p) = 서버 목록의 그 파이프라인 설정 버전들) 중 최대 + 1. server는 새로 쓸 때만 부른다."""
    floor = lambda: max(server(p) if server else [], default=0)
    key = pipeline or LEGACY_PIPELINE
    P.parse_key(key)
    p = pipes.get(key)
    if p is None:
        raise ValueError(f"파이프라인 없음: {key} (있는 것: {', '.join(pipes) or '없음'})")
    P.seed(p, root)
    if asr_model or (pipeline is None and settings_version is None):
        if key != LEGACY_PIPELINE: raise ValueError(f"--asr-model은 {LEGACY_PIPELINE}에만 쓸 수 있습니다")
        if settings_version: raise ValueError("--asr-model과 --settings-version은 함께 쓸 수 없습니다")
        name, path = M.resolve(asr_model)
        if path is None: raise model_error([name])
        n = P.find(p, {"asrModel": name}, root)
        if n is None:
            if not create: raise ValueError(f"{key}에 asrModel={name}인 설정 버전이 아직 없음(전사할 때 새로 씀)")
            n = P.write(p, {"asrModel": name}, root, floor())
    else:
        have = P.versions(p, root)
        if settings_version:
            if settings_version not in have:
                raise ValueError(f"{key}에 설정 버전 {settings_version}이 없습니다(있는 것: {', '.join(map(str, have)) or '없음'}, {P.settings_dir(p, root)})")
            n = settings_version
        else:
            if not have and not create: raise ValueError(f"{key}에 설정 버전이 아직 없음(전사할 때 기본값으로 1을 씀)")
            n = have[-1] if have else P.write(p, P.defaults(p), root, floor())
    c = Chosen(p, n, P.read(p, n, root))
    if missing := missing_models(p, c.settings): raise model_error(missing)
    return c


def run_option_arg(text: str) -> tuple:
    try:
        return P.parse_run_option(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def run_options_for(p: P.Pipeline, pairs: list, warn=None, local: dict | None = None) -> dict:
    """이번 실행의 실행 옵션: 매니페스트 기본값 ← analysis/settings.json runOptions ← --run-option. 매니페스트에 없는 --run-option 키는 경고하고 버린다"""
    over, schema = dict(pairs or []), p.run_schema or {}
    if warn and (unknown := [k for k in over if k not in schema]):
        warn(f"{p.key}에 없는 실행 옵션은 버립니다: {', '.join(unknown)}")
    if warn and (wrong := [k for k, v in over.items() if k in schema and not P.fits(schema[k].get("type", "string"), v)]):
        warn(f"형식이 틀린 실행 옵션은 버립니다(기본값): {', '.join(wrong)}")
    return P.run_values(p, P.local_run_options(M.settings_path()) if local is None else local, over)


def server_settings_versions(p: P.Pipeline, warn) -> list:
    """서버 목록(GET /api/transcription/pipelines, 읽기만)의 그 파이프라인 설정 버전들. 못 읽으면(옛 서버·연결 안 됨) 경고하고 []"""
    try:
        return P.server_versions(api()("GET", "/api/transcription/pipelines"), p)
    except Exception as e:
        warn(f"서버 전사 파이프라인 목록을 읽지 못해 이 PC 기준으로 새 설정 번호를 정합니다({p.key}): {str(e)[-200:]}")
        return []


def transcribe(wav: Path, tmp: Path, words: bool = False, hints: Path | None = None, used: set | None = None, on_progress=None):
    """이번 실행의 파이프라인으로 wav → 구간. words=True면 단어마다 한 구간(화자 구분 assign_speakers가 화자별로 다시 묶는다).
    hints = 단어장 힌트 파일, used = 쓴 단어장 id를 모을 집합, on_progress = 파이프라인이 낸 진행(percent·text·workers)을 받는 함수"""
    stats = {}
    segs = P.run(CHOSEN.pipe, CHOSEN.settings, wav, tmp, words=words, tools=A, hints=hints, used=used, on_progress=on_progress,
                 run_options=CHOSEN.run_options, stats=stats)
    if (line := workers_line(stats.get("workers"))): print(f"  {wav.name}: {line}", file=sys.stderr, flush=True)
    return segs


def workers_line(w) -> str | None:
    """파이프라인 stats.workers(autopar 풀 통계) → 사람이 읽는 한 줄. 자동이 아니고 1개뿐이면 None"""
    if not isinstance(w, dict) or (w.get("mode") != "auto" and (w.get("max") or 1) <= 1): return None
    try:
        text = f"whisper-cli {'자동' if w.get('mode') == 'auto' else '고정'} {w['start']}→{w['final']}개(최소 {w['min']}·최대 {w['max']}·평균 {w['avg']})"
        moves = [c for c in w.get("changes") or [] if c[2] != "시작"]
        if moves: text += " · " + "; ".join(f"{c[0]:g}초 {c[1]}개 {c[2]}" for c in moves[-3:])
        if w.get("recovered"): text += f" · 실패 되돌림 {w['recovered']}번"
        return text
    except (KeyError, TypeError, IndexError):
        return None


def transcribe_reporter(file: int, files: int, **extra):
    """파이프라인 진행을 --progress-json step 이벤트로 넘기는 함수(파일마다 새로, 초당 4번까지). --progress-json이 아니면 None.
    extra = step 줄에 더할 칸(겹치기 실행의 groupFiles = 지금 전사하는 무리의 파일 수)"""
    if not PROGRESS: return None
    gate = P.Throttle()

    def report(ev):
        if ETA and ev.get("workers"): ETA.workers(ev["workers"])   # 거르기 전에(작업자 수 변화를 놓치지 않게)
        if gate.ok(): progress("step", stage="transcribe", file=file, files=files, **extra, **ev)
    return report


VOCAB_OFF = False   # 단어장 고르기가 한 번 실패하면 이번 실행은 힌트 없이(묶음마다 경고하지 않게)
PICKS = {}          # 이번 실행의 단어장 고르기(본문 JSON → 응답, 2026-11-18). 폰 녹음은 묶음마다 본문이 같아 한 번만 묻는다(겹치기 실행에서 준비가 전사 앞을 막지 않게)


def pick_body(b: "B.Bundle", settings: dict) -> dict:
    """POST /api/vocab/pick 본문: 통화면 caller = 통화 상대, max·maxChars = 설정 hintMax·hintChars(없으면 서버 기본)"""
    body = {}
    if b.source == "call":
        import speakers as sp
        body["caller"] = sp.call_partner(b.items[0].path)
    for key, field in (("max", "hintMax"), ("maxChars", "hintChars")):
        if settings.get(field) is not None: body[key] = int(settings[field])
    return body


def hints_file(pick: dict) -> dict:
    """/vocab/pick 응답 → 파이프라인 힌트 파일 {prompt, fixes: [{id, wrong, right}], ids}"""
    return {"prompt": pick.get("prompt") or "", "ids": [h["id"] for h in pick.get("hints") or [] if "id" in h],
            "fixes": [{"id": f.get("id"), "wrong": f["wrong"], "right": f["text"]} for f in pick.get("fixes") or [] if f.get("wrong") and f.get("text")]}


def bundle_hints(call, b: "B.Bundle", tmp: Path) -> Path | None:
    """이 묶음의 단어장 힌트 파일(tmp/hints.json). 파이프라인이 힌트를 안 받거나, 고를 것이 없거나, 서버를 못 읽으면 None"""
    global VOCAB_OFF
    if VOCAB_OFF or not P.wants_hints(CHOSEN.pipe, CHOSEN.settings): return None
    try:
        body = pick_body(b, CHOSEN.settings)
        k = json.dumps(body, sort_keys=True, ensure_ascii=False)
        if k not in PICKS: PICKS[k] = call("POST", "/api/vocab/pick", body) or {}
        h = hints_file(PICKS[k])
    except Exception as e:
        VOCAB_OFF = True
        print(f"  단어장을 읽지 못해 이번 실행은 힌트 없이 전사합니다: {str(e)[-200:]}", file=sys.stderr)
        return None
    if not h["prompt"] and not h["fixes"]: return None
    print(f"    단어장: 힌트 {len(h['ids'])}개({h['prompt'][:60]}{'…' if len(h['prompt']) > 60 else ''}), 고침 짝 {len(h['fixes'])}개")
    path = tmp / "hints.json"
    path.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
    return path


def mark_used(call, ids: set):
    """올리기가 성공한 묶음에서 실제로 쓴 단어장 항목의 쓰인 횟수를 올린다(POST /api/vocab/used). 실패해도 전사는 그대로"""
    if not ids: return
    try:
        call("POST", "/api/vocab/used", {"ids": sorted(ids)[:500]})
    except Exception as e:
        print(f"    단어장 쓰인 횟수를 올리지 못함: {str(e)[-200:]}", file=sys.stderr)


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


SPEAKER_SOURCES = ("phone", "call", "laptop-mic", "discord-output")   # 화자 구분(speakers.py)을 하는 원천. 전사를 단어 단위로 받는다


def assign_speakers(source: str, f: Path, wav: Path, words: list, state: dict, rel: str | None = None, at: str | None = None, diar: tuple | None = None):
    """단어 구간(transcribe(words=True))에 화자를 붙이고 화자·틈 단위로 다시 묶는다. 통화는 나·상대 이름, 폰 녹음은 나·화자1·화자2….
    사람 목소리(speakers.apply_voices, 2026-11-16): 통화면 상대 목소리를 이 PC에 등록, 대면 녹음이면 화자N에 등록된 이름(덜 닮으면 구간 speakerGuess).
    state["save"]가 False(dry-run)면 목소리 저장소를 바꾸지 않는다. sherpa-onnx나 모델이 없으면 화자 없이 묶기만 한다. → (구간들, 통화 상대 이름 또는 None)
    diar = 미리 해 둔 화자 분리 (turns, embs)(겹치기 실행의 화자 분리 갈래, 같은 wav의 Diarizer.diarize 결과). 없으면 여기서 한다"""
    import speakers as sp   # numpy만 쓴다. sherpa-onnx는 Diarizer가 부를 때
    partner = sp.call_partner(f) if source == "call" else None
    if words and speaker_state(state):
        turns, embs = diar if diar is not None else state["d"].diarize(sp.load_wav(wav))   # 화자 수는 정하지 않는다(잘게 나눈 뒤 name_clusters가 합친다)
        no_me = source in sp.NO_ME_SOURCES   # 디스코드 출력: 내 목소리가 없다 → '나' 후보 없이 화자N만
        names, guessed = sp.name_clusters(embs, None if no_me else state["me"], turns, partner), set()
        if state["me"] is not None or no_me:   # '나'를 못 가르면 상대 목소리도 못 고른다
            try:
                names, guessed, msg = sp.apply_voices(state["voices"], source, rel or f.name, at, turns, embs, names, state["me"], partner, save=state.get("save", True))
                print("  " + msg)
            except Exception as e:
                print("  목소리 대조 건너뜀:", e)
        sp.label_segments(words, turns, names, guessed)
    segs = [s for s in sp.group_words(words) if not HALLUCINATIONS.match(s["text"])]
    return segs, partner


def speaker_state(state: dict) -> bool:
    """화자 분리기·'나'·목소리 저장소를 처음 쓸 때 읽는다. sherpa-onnx나 모델이 없으면 False"""
    if "d" not in state:
        import speakers as sp
        try:
            state["d"], state["me"], state["voices"] = sp.Diarizer(), sp.Me.load(), sp.V.Store(sp.VOICES)
            if state["me"] is None: print("  ('나' 기준 없음: python speakers.py enroll)")
        except Exception as e:
            print("  화자 구분 건너뜀:", e); state["d"] = None
    return bool(state.get("d"))


def enroll_bot_voices(b: "B.Bundle", wavs: list, state: dict):
    """디스코드 봇 묶음: 사람별 파일(wavs = b.items 순서의 16kHz wav)을 이어 그 사람 목소리를 등록(speakers.enroll_bot_tracks).
    state["save"]가 False(dry-run)면 저장하지 않는다. 실패해도 전사 결과는 그대로 올린다."""
    import speakers as sp, numpy as np
    if not speaker_state(state): return
    try:
        tracks = []
        for ix in B.by_track(b.items).values():
            first, parts, n = b.items[ix[0]], [], 0
            for i in ix:
                if n >= sp.SR * sp.BOT_MAX_SEC: break
                x = sp.load_wav(wavs[i]); parts.append(x); n += len(x)
            tracks.append((first.speaker, first.rel, iso(first.start), np.concatenate(parts)))
        n, msgs = sp.enroll_bot_tracks(state["voices"], state["d"], state["me"], tracks, save=state.get("save", True))
        print(f"  목소리(봇) {n}명 등록: " + "; ".join(msgs))
    except Exception as e:
        print("  목소리(봇) 등록 건너뜀:", e)


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
        return {"gapSec": int(r["gapSec"]), "maxSec": int(r["maxSec"]), "singleSources": list(r.get("singleSources") or []),
                "callSources": list(r.get("callSources") or B.DEFAULT_RULE["callSources"])}
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


# ---- 겹치기 실행(2026-11-18, run_flow) ----
# 묶음마다 준비(wav 만들기·단어장 고르기, CPU) → 전사(GPU) → 마무리(화자 구분·요약·올리기, CPU)를 세 갈래로 겹친다:
# 무리 g를 전사하는 동안 무리 g+1을 준비하고 무리 g-1을 마무리한다. 전사는 묶음 여러 개(무리)를 한 번에: 파이프라인이 여러 입력(multiInput)을 받으면
# 무리의 모든 파일을 한 호출(모델 올리기·작업자 풀 한 번)로, 아니면 파일마다 차례로. 마무리는 묶음 차례대로 하나씩(목소리 저장소·올리기 차례가 전과 같게).
# 결과: 파일마다 구간은 하나씩 돌린 것과 같다(파이프라인 약속). 다른 점은 단어장 고르기가 앞 묶음의 "쓰인 횟수 올리기"보다 먼저 될 수 있는 것뿐(같은 점수의 순서만 바뀔 수 있음).
GROUP_CHUNKS = 48   # 무리 하나의 30초 조각 수 목표(파일마다 올림한 추정). 작업자 풀이 묶음을 나눠 가질 만큼 크고, 겹치기가 잘게 돌 만큼 작게
GROUP_FILES = 64    # 무리 하나의 파일 수 상한
PREP_AHEAD = 1      # 전사 중인 무리 뒤에 미리 준비해 두는 무리 수(wav가 임시 폴더에 쌓이는 양)
WAV_THREADS = 4     # 묶음 안 파일들의 wav를 동시에 만드는 ffmpeg 수
# 화자 분리(sherpa, CPU)는 오디오만 보므로 준비가 끝나면 바로 화자 분리 갈래에서 돌린다(GPU 전사와 겹침). 마무리는 그 결과로 이름 붙이기·목소리 대조만.
# 갈래 수 = 논리 코어 ÷ 6(1~3, Diarizer 하나가 스레드 4개, whisper-cli도 CPU를 쓴다). 갈래는 프로세스(speakers.diarize_path: sherpa-onnx가 GIL을 놓지 않아
# 스레드면 다른 갈래가 멈췄다, 2026-11-18 실측)이고 프로세스마다 Diarizer 하나(같은 모델·설정이라 결과는 같다, 실측으로 확인).
DIAR_WORKERS = max(1, min(3, (os.cpu_count() or 4) // 6))


def est_chunks(sec: float) -> int:
    return max(1, -(-int(sec) // 30))


def make_groups(jobs: list, audio: list) -> list:
    """묶음들 → 무리들 [[묶음 번호(0부터)…]]. 차례대로 조각 수 추정 합이 GROUP_CHUNKS나 파일 수가 GROUP_FILES를 넘기 전까지 담는다(묶음은 나누지 않음)"""
    groups, cur, chunks, files = [], [], 0, 0
    for i, b in enumerate(jobs):
        c, f = sum(est_chunks(x) for x in audio[i]), len(b.items)
        if cur and (chunks + c > GROUP_CHUNKS or files + f > GROUP_FILES):
            groups.append(cur); cur, chunks, files = [], 0, 0
        cur.append(i); chunks += c; files += f
    if cur: groups.append(cur)
    return groups


@dataclass
class Job:
    """겹치기 실행에서 묶음 하나(i = 0부터, g = 무리 번호)"""
    i: int
    b: "B.Bundle"
    g: int = 0
    tmp: object = None            # tempfile.TemporaryDirectory
    hints: Path | None = None
    wavs: list = field(default_factory=list)
    parts: list | None = None     # 파일마다 구간들(전사 결과, 묶음 오프셋 전)
    used: set = field(default_factory=set)
    error: BaseException | None = None
    t0: float = 0.0
    diar: tuple | None = None     # 화자 분리 갈래: (묶음 wav, Future → (turns, embs))

    @property
    def dir(self) -> Path:
        return Path(self.tmp.name)

    def words(self) -> bool:
        return self.b.source in SPEAKER_SOURCES

    def close(self):
        if self.tmp is not None: self.tmp.cleanup(); self.tmp = None


def prep_job(j: Job, call):
    """준비: 임시 폴더, 단어장 힌트, 파일마다 16kHz wav(동시에 WAV_THREADS개). 실패하면 j.error(그 묶음만)"""
    from concurrent.futures import ThreadPoolExecutor
    j.t0 = time.time()
    j.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # 백신·색인기가 임시 파일을 잡고 있어도 멈추지 않게
    try:
        j.hints = bundle_hints(call, j.b, j.dir)
        j.wavs = [j.dir / f"p{k}.wav" for k in range(len(j.b.items))]
        with ThreadPoolExecutor(min(WAV_THREADS, len(j.wavs)) or 1) as ex:
            ms = list(ex.map(lambda k: to_wav(j.b.items[k].path, j.wavs[k]), range(len(j.wavs))))
        if ETA:
            for k, x in enumerate(ms): ETA.audio(j.i, k, x / 1000)
    except Exception as e:
        j.error = e


def transcribe_group(js: list, tmp: Path, tag: str = "g"):
    """무리 전사: 여러 입력 파이프라인이면 무리의 모든 파일을 한 호출로(P.run_many), 아니면 파일마다 transcribe(). 결과는 j.parts, 실패는 j.error(그 묶음만).
    한 호출이 통째로 실패하면(whisper-cli 실패 등) 묶음마다 따로 다시 돌려 실패를 그 묶음에 가둔다."""
    live = [j for j in js if j.error is None]
    if not live: return
    files = sum(len(j.wavs) for j in live)
    if not CHOSEN.pipe.multi:
        n = 0
        for j in live:
            try:
                parts = []
                for wav in j.wavs:
                    n += 1
                    progress("step", stage="transcribe", file=n, files=files, groupFiles=files)
                    parts.append(transcribe(wav, j.dir, words=j.words(), hints=j.hints, used=j.used, on_progress=transcribe_reporter(n, files, groupFiles=files)))
                j.parts = parts
            except Exception as e:
                j.error = e
        return
    items = [(j, k) for j in live for k in range(len(j.wavs))]
    progress("step", stage="transcribe", file=1, files=1, groupFiles=files)
    try:
        res = P.run_many(CHOSEN.pipe, CHOSEN.settings, [{"wav": j.wavs[k], "words": j.words(), "hints": j.hints} for j, k in items], tmp, tools=A,
                         on_progress=transcribe_reporter(1, 1, groupFiles=files), run_options=CHOSEN.run_options, tag=tag)
    except RuntimeError as e:
        if len(live) == 1:
            live[0].error = e; return
        print(f"  전사 무리(묶음 {len(live)}개) 실패 → 묶음마다 다시: {str(e)[-200:]}", file=sys.stderr, flush=True)
        for k, j in enumerate(live): transcribe_group([j], tmp, f"{tag}-{k}")
        return
    parts, line = {}, None
    for (j, k), r in zip(items, res):
        if isinstance(r, Exception):
            j.error = j.error or r; continue
        segs, used, stats = r
        j.used |= used
        parts.setdefault(j.i, [None] * len(j.wavs))[k] = segs
        line = line or workers_line(stats.get("workers"))
    for j in live:
        if j.error is None: j.parts = parts[j.i]
    if line: print(f"  무리 파일 {files}개: {line}", file=sys.stderr, flush=True)


def finish_bundle(b: "B.Bundle", wavs: list, parts: list, llm: "Llm | None", spk: dict, min_summary_chars: int, announce: bool = True, diar=None):
    """마무리: 파일마다 구간(parts)을 묶음 시각으로 옮기고 화자 구분(llm이 있으면 요약까지). → API 본문(segments 포함).
    전사는 파일마다 하고 시각에 오프셋을 더한다: 무음을 채워 이어 붙인 긴 wav를 whisper.cpp VAD로 돌리면 구간 시각이 수백 초씩 어긋났다(2026-11-11 실측).
    녹음은 5초 무음 뒤에만 파일을 끊으므로 경계에서 말이 잘리는 일은 드물다. 화자 구분(sherpa)과 요약은 묶음 전체로 한 번에.
    announce = 화자 구분·요약 step 줄을 낼까(겹치기 실행에서 다른 무리를 전사하는 동안은 내지 않는다: 데스크톱 단계가 전사로 남게)
    diar = 화자 분리 갈래의 (묶음 wav, Future → (turns, embs)). 실패했거나 없으면 여기서 화자 분리를 한다"""
    source, first = b.source, b.items[0]
    words_mode = source in SPEAKER_SOURCES
    segs, partner = [], None
    for it, part in zip(b.items, parts):
        off = it.start - b.start
        for s in part:
            s["startMs"] += off; s["endMs"] += off
            if source == "discord-bot": s["speaker"] = it.speaker
        segs += part
    segs.sort(key=lambda s: (s["startMs"], s["endMs"]))
    if words_mode:
        if announce: progress("step", stage="speakers")
        pre, whole = None, None
        if diar is not None:
            whole, fut = diar
            try:
                pre = fut.result()
            except Exception as e:
                print("  화자 분리 갈래 실패, 여기서 다시:", e)
        if whole is None: whole = bundle_wav(b, wavs)
        segs, partner = assign_speakers(source, first.path, whole, segs, spk, rel=first.rel, at=iso(b.start), diar=pre)
    elif source == "discord-bot":
        if announce: progress("step", stage="speakers")
        enroll_bot_voices(b, wavs, spk)
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
        if announce: progress("step", stage="summary")
        summary = llm.summarize(labeled(segs) if any(s["speaker"] for s in segs) else transcript, context)
    if partner and summary:
        summary = f"통화 상대: {partner}\n{summary}"
    files = [{"file": it.rel, "startedAt": iso(it.start), "endedAt": iso(it.end), "offsetMs": it.start - b.start} for it in b.items]
    return {"device": SOURCES[source], "file": first.rel, "source": source, "startedAt": iso(b.start), "endedAt": iso(b.end),
            "pipeline": CHOSEN.ref(), "llmModel": LLM_MODEL.stem if summary else None, "language": "ko",
            "speechMs": speech, "transcript": transcript, "summary": summary, "segments": segs, "files": files}


def bundle_wav(b: "B.Bundle", wavs: list) -> Path:
    """화자 분리에 넣는 묶음 wav: 파일 하나면 그대로, 여럿이면 간격을 무음으로 채워 이어 붙인 것(같은 임시 폴더의 bundle.wav)"""
    return wavs[0] if len(wavs) == 1 else join_wavs(b, wavs, wavs[0].parent / "bundle.wav")


def diar_fn():
    """화자 분리 갈래에서 부를 함수(다른 프로세스에서 불러 쓸 수 있게 모듈 함수). 시험이 바꿔 끼운다"""
    import speakers as sp
    return sp.diarize_path


def make_diar_pool():
    from concurrent.futures import ProcessPoolExecutor
    return ProcessPoolExecutor(DIAR_WORKERS)


def stop_pool(pool, cancel: bool):
    """화자 분리 갈래 끝내기. cancel(중단·오류)이면 도는 프로세스를 바로 죽이고, 아니면 끝나기를 기다린다"""
    if pool is None: return
    if not cancel: pool.shutdown(wait=True); return
    pool.shutdown(wait=False, cancel_futures=True)
    kill = getattr(pool, "kill_workers", None)   # 파이썬 3.14+
    if kill:
        try: kill()
        except Exception: pass
    else:
        for proc in list((getattr(pool, "_processes", None) or {}).values()):
            try: proc.kill()
            except Exception: pass


def run_flow(jobs: list, call, overlap: bool = True):
    """준비·전사·마무리 세 갈래를 돌리며 묶음을 차례대로 내준다(제너레이터: 마무리할 Job을 묶음 차례로 yield).
    받는 쪽(run_jobs)이 j.parts로 마무리하고 다음을 받는다. overlap = False면 앞 무리 마무리가 끝나야 다음 무리를 전사한다(요약 llama-server와 GPU를 같이 쓰지 않게).
    받는 쪽이 멈추면(예외·취소) 갈래를 멈추고 돌고 있는 파이프라인을 트리째 끝낸다. 갈래 안의 예상 못 한 오류는 받는 쪽에서 다시 던진다."""
    import queue
    groups = make_groups(jobs, plan_audio(jobs))
    stop = threading.Event()
    ready, done_q = queue.Queue(maxsize=PREP_AHEAD), queue.Queue()
    gate = None if overlap else threading.Semaphore(1)
    made = []   # 준비한 Job(끝에 임시 폴더를 지운다)
    diar = {"pool": None, "on": speakers_ready()}   # 화자 분리 갈래(처음 쓸 때 띄움)
    state = {"transcribing": True}

    def put(q, x) -> bool:
        while not stop.is_set():
            try:
                q.put(x, timeout=0.5); return True
            except queue.Full:
                pass
        return False

    def prep():
        try:
            for g, ix in enumerate(groups):
                js = []
                for i in ix:
                    if stop.is_set(): return
                    j = Job(i, jobs[i], g); made.append(j); prep_job(j, call); js.append(j)
                    if diar["on"] and j.error is None and j.words() and not stop.is_set():
                        try:
                            diar["pool"] = diar["pool"] or make_diar_pool()
                            whole = bundle_wav(j.b, j.wavs)
                            j.diar = (whole, diar["pool"].submit(diar_fn(), str(whole)))
                        except Exception as e:   # 갈래를 못 쓰면 마무리에서 예전처럼
                            print("  화자 분리 갈래 안 씀:", e, file=sys.stderr, flush=True); diar["on"] = False
                if not put(ready, js): return
            put(ready, None)
        except BaseException as e:
            done_q.put(e)

    def trans():
        try:
            while not stop.is_set():
                try:
                    js = ready.get(timeout=0.5)
                except queue.Empty:
                    continue
                if js is None: break
                if gate:
                    while not gate.acquire(timeout=0.5):
                        if stop.is_set(): return
                ix = [j.i for j in js]
                if ETA: ETA.start_group(ix)
                b0, b1 = js[0].b, js[-1].b
                progress("start", i=ix[0] + 1, last=ix[-1] + 1, total=len(jobs), bundles=len(js), source=b0.source, first=Path(b0.items[0].rel).name,
                         start=iso(b0.start), end=iso(b1.end), files=sum(len(j.b.items) for j in js))
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as gt:
                    transcribe_group(js, Path(gt), f"g{js[0].g}")
                if ETA: ETA.end_transcribe()
                if js[0].g == len(groups) - 1: state["transcribing"] = False
                for j in js: done_q.put(j)
        except BaseException as e:
            done_q.put(e)

    threads = [threading.Thread(target=prep, daemon=True, name="prep"), threading.Thread(target=trans, daemon=True, name="transcribe")]
    for t in threads: t.start()
    finished = False
    try:
        for n in range(len(jobs)):
            x = done_q.get()
            if isinstance(x, BaseException): raise x
            yield x, not state["transcribing"]
            x.close()
            if gate and (n + 1 == len(jobs) or x.g != jobs_group(groups, n + 1)): gate.release()
        finished = True
    finally:
        stop.set()
        P.kill_all()
        for t in threads: t.join(timeout=10)
        stop_pool(diar["pool"], cancel=not finished)   # 화자 분리 프로세스가 끝나야 임시 wav를 지울 수 있다
        for j in made: j.close()


def speakers_ready() -> bool:
    """화자 분리 갈래를 쓸 수 있나(sherpa-onnx와 모델이 있나). 없으면 마무리에서 예전처럼(화자 없이)"""
    try:
        import speakers as sp, sherpa_onnx  # noqa: F401
        return all(Path(x).exists() for x in (sp.SEG_MODEL, sp.EMB_MODEL))
    except Exception:
        return False


def jobs_group(groups: list, i: int) -> int:
    """묶음 번호 i(0부터)가 든 무리 번호"""
    for g, ix in enumerate(groups):
        if i in ix: return g
    return -1


PROGRESS = False
ETA: "E.Eta | None" = None   # 이번 실행의 예상 시간(run_jobs가 묶음을 정한 뒤 만든다)
ETA_EVENTS = ("plan", "start", "step", "done", "error")


PRINT_LOCK = threading.Lock()   # 겹치기 실행: 준비·전사·마무리 갈래가 같이 진행 줄을 낸다


def progress(event: str, **kw):
    """--progress-json이면 기계가 읽는 진행 한 줄. 사람이 읽는 줄과 섞여 나오므로 접두어로 가른다. 실행 중이면 eta(eta.py)를 붙인다."""
    if PROGRESS:
        with PRINT_LOCK:
            if ETA and event in ETA_EVENTS:
                kw["eta"] = ETA.snapshot(kw.get("percent"), transcribing=kw.get("stage", "transcribe") == "transcribe", file=kw.get("file"), files=kw.get("files"))
            print("@@progress " + json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def plan_audio(jobs: list) -> list:
    """묶음마다 파일 오디오 길이(초). 길이를 못 읽은 파일(scan이 1ms로 둔 것)은 0"""
    return [[(it.end - it.start) / 1000 if it.end - it.start > 1 else 0 for it in b.items] for b in jobs]


def bundle_info(b: "B.Bundle") -> dict:
    return {"source": b.source, "first": Path(b.items[0].rel).name, "start": iso(b.start), "end": iso(b.end), "files": len(b.items)}


def bundle_key(b: "B.Bundle") -> str:
    """대기열·잠금 파일 plan의 묶음 키: <출처>/<첫 파일 이름>(데스크톱 TranscribeQueue.BundleKey와 같음)"""
    return f"{b.source}/{Path(b.items[0].rel).name}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="이 파일이 든 묶음 하나만")
    ap.add_argument("--dry-run", action="store_true", help="분석은 하되 올리지 않는다")
    ap.add_argument("--plan", action="store_true", help="묶음과 상태만 보이고 분석하지 않는다")
    ap.add_argument("--redo", action="store_true", help="이미 분석한 묶음도 다시")
    ap.add_argument("--redo-from", action="append", type=redo_entry, metavar="NAME@VERSION#N",
                    help="--redo와 함께: 이 파이프라인 항목의 결과가 있는 묶음만 다시(여러 번 줄 수 있음, 새 결과는 --pipeline으로)")
    ap.add_argument("--limit", type=int, help="묶음 최대 N개만")
    ap.add_argument("--stage", choices=("transcribe", "all"), default="transcribe", help="transcribe = 전사·화자 구분만(기본), all = 요약까지")
    ap.add_argument("--min-summary-chars", type=int, default=60)
    ap.add_argument("--source", choices=list(SOURCES), help="이 원천 폴더만(예: phone)")
    ap.add_argument("--pick", action="append", help="--source 안에서 이 파일 이름이 든 묶음 하나만(이미 분석했어도 다시). 여러 번 주면 그 묶음들(2026-11-18)")
    ap.add_argument("--call", help="이 파일 이름이 든 디스코드 통화(callSources 묶음들)의 미전사 묶음 전부(--redo면 전부)")
    ap.add_argument("--pipeline", help=f"전사 파이프라인 <이름>@<버전>(--list-pipelines). 없으면 {LEGACY_PIPELINE}(전사 모델 = --asr-model → settings.json)")
    ap.add_argument("--settings-version", type=int, help="파이프라인 설정 버전(없으면 최신)")
    ap.add_argument("--run-option", action="append", type=run_option_arg, default=[], metavar="KEY=VALUE",
                    help="실행 옵션(결과를 바꾸지 않음, 예: parallel=3). analysis/settings.json의 runOptions 위에 얹는다. 설정 버전에 들어가지 않음")
    ap.add_argument("--list-pipelines", action="store_true", help="이 PC의 전사 파이프라인과 설정 버전을 JSON으로 보이고 끝낸다")
    ap.add_argument("--asr-model", help=f"전사 모델 이름(models/ggml-<이름>.bin) → {LEGACY_PIPELINE}에서 asrModel이 그것인 설정 버전. 없으면 settings.json의 asrModel → " + M.DEFAULT)
    ap.add_argument("--list-models", action="store_true", help="쓸 수 있는 전사 모델과 기본값을 JSON으로 보이고 끝낸다")
    ap.add_argument("--progress-json", action="store_true", help='진행을 "@@progress {json}" 줄로도 낸다')
    ap.add_argument("--only-new", action="store_true", help="--pick·--file로 고른 묶음이 이미 전사돼 있으면 건너뛴다(대기열에서 미전사로 넣은 것)")
    ap.add_argument("--no-auto-remove", action="store_true", help="말소리 없는 묶음을 휴지통으로 옮기지 않는다")
    ap.add_argument("--sweep-silent", action="store_true", help="전사하지 않고, 이미 전사된 묶음 중 말소리 없는 것을 휴지통으로(--dry-run이면 목록만)")
    a = ap.parse_args()
    global PROGRESS
    PROGRESS = a.progress_json
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)   # 앱이 파이프로 읽어도 한 줄씩 바로·한글 그대로
    except (AttributeError, ValueError):
        pass
    if a.list_models:
        name, path = M.resolve(None)
        print(json.dumps({"default": name if path else None, "configured": M.configured(), "settings": str(M.settings_path()),
                          "models": [{"name": n, "bytes": size} for n, _, size in M.available()]}, ensure_ascii=False))
        return
    warn = lambda m: print("  " + m, file=sys.stderr)
    if a.list_pipelines:
        print(json.dumps(P.listing(P.discover(warn=warn), local=P.local_run_options(M.settings_path())), ensure_ascii=False))
        return
    global CHOSEN
    if not a.sweep_silent:
        try:
            CHOSEN = choose_pipeline(a.pipeline, a.settings_version, a.asr_model, P.discover(warn=warn), create=not a.plan,
                                     server=lambda p: server_settings_versions(p, warn))
            CHOSEN.run_options = run_options_for(CHOSEN.pipe, a.run_option, warn)
        except ValueError as e:
            if not a.plan:
                progress("error", message=str(e)); print(e); sys.exit(2)
            print("  (파이프라인을 고르지 못함:", str(e) + ")")
    if a.pick and not a.source: ap.error("--pick에는 --source가 필요합니다")
    if a.redo_from and not a.redo: ap.error("--redo-from에는 --redo가 필요합니다")
    if a.call and (a.pick or a.file or a.source): ap.error("--call은 --pick·--file·--source와 함께 쓸 수 없습니다")
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


def load_favorites(call):
    """서버 즐겨찾기 (출처, 이름). 못 읽으면 None(그러면 자동 휴지통 이동을 하지 않는다)."""
    try:
        return T.favorites(call("GET", "/api/recordings/favorites"))
    except Exception as e:
        print(f"  즐겨찾기를 읽지 못해 말소리 없는 묶음을 휴지통으로 옮기지 않습니다: {str(e)[-200:]}")
        return None


def all_silent(call, device: str, file: str) -> bool:
    """그 묶음의 서버 결과(파이프라인마다 하나)가 모두 말소리 없음인가. 못 읽으면 False(옮기지 않음)."""
    try:
        rows = call("GET", f"/api/recordings/detail?device={urllib.parse.quote(device)}&file={urllib.parse.quote(file)}")
    except Exception as e:
        print(f"  전사 결과를 읽지 못해 휴지통으로 옮기지 않습니다: {str(e)[-200:]}")
        return False
    rows = rows if isinstance(rows, list) else [rows] if rows else []
    return bool(rows) and all(T.is_silent(r.get("segments") or [], r.get("transcript")) for r in rows)   # 결과 없음([])이면 옮기지 않음


def sweep_silent(a, call):
    """이미 전사된 묶음 중 말소리 없는 것(서버 분석 글 0자, 결과가 여럿이면 모두)을 휴지통으로. 묶음 파일이 이 PC에 모두 있을 때만."""
    rows = call("GET", "/api/recordings?from=2000-01-01&to=2100-01-01") or []
    now = epoch_ms(dt.datetime.now().astimezone())
    gone, keep, favs = T.listed(ROOT), T.kept(ROOT), load_favorites(call)
    if favs is None: return
    rule, local = bundle_rule(lambda path: call("GET", path)), {}
    n, n_files = 0, 0
    for r in T.silent_bundles(rows):   # 행 = 결과 하나(전사 파이프라인). 다른 결과에 말이 있으면 뺀다
        src = r.get("source")
        if src not in T.SOURCES or (a.source and src != a.source): continue
        files = T.bundle_files(r)
        pre = REL_PREFIX.get(src, "")
        paths = [ROOT / src / f["file"].removeprefix(pre) for f in files]
        names = [p.name for p in paths]
        when = r["startedAt"][:16].replace("T", " ")
        if all((src, x) in gone for x in names) or T.protected({(src, x) for x in names}, keep, favs): continue   # 되살린 것·즐겨찾기는 그대로
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
    if not a.plan and not FFMPEG: sys.exit(f"없음: {A / 'ffmpeg'}/**/ffmpeg.exe")
    call = api()
    get = lambda path: call("GET", path)
    rule = bundle_rule(get)
    now = epoch_ms(dt.datetime.now().astimezone())
    print(f"묶음 규칙: 간격 {rule['gapSec']}초 이하 · 최대 {rule['maxSec'] // 60}분 · 묶지 않음 {', '.join(rule['singleSources']) or '-'}")

    if a.call:
        return run_jobs(a, call, *call_jobs(a, get, rule, now))
    target = Path(a.file).resolve() if a.file else None
    folders = [source_of(target)] if target else [x for x in SOURCES if not a.source or x == a.source]
    jobs, n_done, n_progress = [], 0, 0
    for folder in folders:
        if not (ROOT / folder).exists(): continue
        bundles = B.make_bundles(scan(folder), rule)
        if target or a.pick:
            hit = lambda it: Path(it.path).resolve() == target if target else Path(it.rel).name in a.pick
            for b in bundles:
                if not any(hit(it) for it in b.items): continue
                if B.in_progress(b, now, rule):
                    progress("refused", reason="in-progress", **bundle_info(b))
                    print(f"진행 중인 묶음이라 분석하지 않습니다({hms(b.start)} ~ {hms(b.end)[6:]}, 마지막 파일 뒤 {rule['gapSec']}초가 지나지 않음).")
                    sys.exit(4)
                if a.only_new and not a.redo:
                    done = set(get("/api/recordings/analyzed?device=" + urllib.parse.quote(SOURCES[folder])) or [])
                    if all(it.rel in done for it in b.items):
                        progress("skipped", reason="already", **bundle_info(b))
                        print("이미 전사된 묶음이라 건너뜁니다:", bundle_key(b)); n_done += 1
                        continue
                jobs.append(b)
            continue
        want = wanted(a, get, SOURCES[folder])
        for b in bundles:
            state = "진행 중" if B.in_progress(b, now, rule) else "분석할 것" if want(b) else "분석됨"
            if a.plan:
                print(f"  {folder:14} {hms(b.start)} ~ {hms(b.end)[6:]}  파일 {len(b.items):3}개  {state}")
            if state == "진행 중": n_progress += 1
            elif state == "분석됨": n_done += 1
            else: jobs.append(b)
    if a.limit: jobs = jobs[:a.limit]
    if (target or a.pick) and not jobs and not n_done:
        progress("missing", file=a.file or ", ".join(a.pick), source=a.source)
        print("그 파일이 든 묶음을 찾지 못했습니다(이 PC의 녹음 폴더에 파일이 없음):", a.file or f"{a.source}/{', '.join(a.pick)}")
    return run_jobs(a, call, jobs, n_done, n_progress)


def redo_entry(text: str) -> tuple:
    """--redo-from 'name@version#n' → (name, version, n)"""
    key, _, n = text.strip().rpartition("#")
    try:
        name, version = P.parse_key(key)
        if not n.isdigit() or int(n) < 1: raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(f"<이름>@<버전>#<설정 버전> 꼴이어야 합니다: {text}")
    return name, version, int(n)


def wanted(a, get, device: str):
    """이 기기 묶음 중 전사할 것을 고르는 함수(묶음 → bool).
    --redo-from: 그 항목들의 결과에 든 파일이 하나라도 있는 묶음만. --redo: 전부. 아니면 어느 파이프라인 결과도 없는 묶음(파일 하나라도 빠졌으면)."""
    if a.redo and not a.redo_from: return lambda b: True
    q = "/api/recordings/analyzed?device=" + urllib.parse.quote(device)
    if a.redo_from:
        hit = set()
        for name, version, n in a.redo_from:
            hit |= set(get(q + f"&pipeline={urllib.parse.quote(name)}&version={urllib.parse.quote(version)}&settingsVersion={n}") or [])
        return lambda b: any(it.rel in hit for it in b.items)
    done = set(get(q) or [])
    return lambda b: not all(it.rel in done for it in b.items)


def call_jobs(a, get, rule, now):
    """--call: 그 파일이 든 디스코드 통화의 묶음들(출처별 묶음 그대로). 진행 중인 묶음이 있으면 통화째 거절(종료 코드 4)."""
    bundles = []
    for folder in rule["callSources"]:
        if folder in SOURCES and (ROOT / folder).exists(): bundles += B.make_bundles(scan(folder), rule)
    found = next((c for c in B.make_calls(bundles, rule) if any(Path(it.rel).name == a.call for b in c for it in b.items)), None)
    if found is None:
        # 통화가 아니어도(한 출처뿐) 그 파일이 든 묶음 하나는 처리한다
        found = [b for b in bundles if any(Path(it.rel).name == a.call for it in b.items)]
    if not found:
        progress("missing", file=a.call, source="call")
        print("그 파일이 든 디스코드 통화를 찾지 못했습니다(이 PC의 녹음 폴더에 파일이 없음):", a.call)
        return [], 0, 0
    print(f"디스코드 통화 {hms(min(b.start for b in found))} ~ {hms(max(b.end for b in found))[6:]}: 묶음 {len(found)}개("
          + ", ".join(f"{s} {sum(1 for b in found if b.source == s)}" for s in rule["callSources"]) + ")")
    late = [b for b in found if B.in_progress(b, now, rule)]
    if late:
        progress("refused", reason="in-progress", **bundle_info(late[0]))
        print(f"아직 녹음이 이어질 수 있는 통화라 전사하지 않습니다(마지막 파일 뒤 {rule['gapSec']}초가 지나지 않음).")
        sys.exit(4)
    want = {dev: wanted(a, get, dev) for dev in sorted({SOURCES[b.source] for b in found})}
    jobs = [b for b in found if want[SOURCES[b.source]](b)]
    return jobs, len(found) - len(jobs), 0


def run_jobs(a, call, jobs, n_done, n_progress):
    global ETA
    if CHOSEN: print("전사 파이프라인:", CHOSEN.label())
    print(("전사만 합니다(요약 안 함). " if a.stage == "transcribe" else "") + f"분석할 묶음 {len(jobs)}개(파일 {sum(len(b.items) for b in jobs)}개) · 이미 분석 {n_done} · 진행 중이라 건너뜀 {n_progress}")
    groups = make_groups(jobs, plan_audio(jobs)) if jobs else []
    if not a.plan:
        runlock.set_plan([bundle_key(b) for b in jobs])
        if jobs: ETA = E.Flow(plan_audio(jobs), flow_key(), groups)
        progress("plan", total=len(jobs), files=sum(len(b.items) for b in jobs), done=n_done, inProgress=n_progress, dryRun=a.dry_run, stage=a.stage,
                 pipeline=CHOSEN.ref(), asrModel=CHOSEN.settings.get("asrModel"), groups=len(groups), multiInput=CHOSEN.pipe.multi)
    if a.plan or not jobs:
        if not a.plan: progress("end", analyzed=0, total=0)
        return
    if len(groups) < len(jobs): print(f"전사 무리 {len(groups)}개(" + ("여러 입력: 무리마다 파이프라인 한 번" if CHOSEN.pipe.multi else "파일마다 차례로") + ", 준비·화자 구분·올리기와 겹침)")
    if not a.dry_run:
        try:   # 결과는 서버 목록에 있는 항목으로만 올릴 수 있다(없으면 400). 이미 있으면 그대로
            call("POST", "/api/transcription/pipelines", CHOSEN.ref())
        except Exception as e:
            sys.exit(f"전사 파이프라인을 서버 목록에 등록하지 못했습니다({CHOSEN.pipe.key} 설정 {CHOSEN.settings_version}): {str(e)[-200:]}")

    llm = Llm() if a.stage == "all" else None
    spk = {"save": not a.dry_run}   # 화자 분리기·'나'·목소리 저장소(처음 쓸 때 읽음)
    failed = []   # 묶음 하나가 실패해도(GPU 메모리 부족·깨진 파일 등) 나머지는 계속한다(2026-11-12). 다음 실행 때 다시 시도된다
    flow = run_flow(jobs, call, overlap=llm is None)
    try:
        for j, announce in flow:
            i, b, used = j.i + 1, j.b, j.used
            try:
                if j.error is not None: raise j.error
                body = finish_bundle(b, j.wavs, j.parts, llm, spk, a.min_summary_chars, announce, j.diar)
                if not a.dry_run:
                    call("PUT", "/api/recordings/analysis", body)
            except Exception as e:
                failed.append(b)
                ETA.finish(j.i, ok=False)
                msg = (str(e) or type(e).__name__).strip()
                print(f"[{i}/{len(jobs)}] {b.source} {hms(b.start)}~{hms(b.end)[6:]}  실패, 건너뜀: {msg[-300:]}", flush=True)
                progress("error", message=f"{b.source} {hms(b.start)} 묶음 실패(건너뜀): {msg[-200:]}", **bundle_info(b))
                continue
            ETA.finish(j.i, ok=True)
            if not a.dry_run: mark_used(call, used)
            segs = body["segments"]
            left = ETA.snapshot()
            print(f"[{i}/{len(jobs)}] {b.source} {hms(b.start)}~{hms(b.end)[6:]}  파일 {len(b.items)}개 {(b.end - b.start) / 1000:.0f}초"
                  f" → 말 {body['speechMs'] / 1000:.0f}초, {len(segs)}구간 ({time.time() - j.t0:.1f}초)"
                  + (f" · 남은 예상 {E.human(left['remainingSec'])}({left['endsAt'][11:16]} 끝)" if left["remainingSec"] is not None and i < len(jobs) else ""))
            progress("done", i=i, total=len(jobs), speechMs=body["speechMs"], segments=len(segs), seconds=round(time.time() - j.t0, 1),
                     uploaded=not a.dry_run, **bundle_info(b))
            if (not a.no_auto_remove and b.source in T.SOURCES and T.is_silent(segs, body["transcript"])
                    and not T.protected({(b.source, it.path.name) for it in b.items}, T.kept(ROOT), load_favorites(call))   # 되살린 것·즐겨찾기(그때 읽음)는 그대로
                    and (a.dry_run or all_silent(call, body["device"], body["file"]))):   # 다른 파이프라인 결과에 말이 있으면 그대로
                if a.dry_run:
                    print(f"    말소리 없음 → 휴지통 후보(dry-run이라 옮기지 않음)")
                else:
                    moved = T.move_bundle(ROOT, b.source, [it.path for it in b.items], iso(b.start), spans=[(iso(it.start), iso(it.end)) for it in b.items])
                    print(f"    말소리 없음 → 휴지통으로 파일 {len(moved)}개({T.TRASH_DAYS}일 뒤 영구 삭제, 녹음 화면에서 되살리기)")
                    progress("trashed", source=b.source, start=iso(b.start), files=len(moved))
            if a.dry_run:
                for s in segs: print(f"    {s['startMs'] / 1000:7.1f}–{s['endMs'] / 1000:7.1f}  {(s['speaker'] + ('(추정)' if s.get('speakerGuess') else '') + ': ') if s['speaker'] else ''}{s['text']}"
                                   + (f"  (원래: {s['original']})" if s.get("original") else ""))
                if body["summary"]: print("  요약:", body["summary"])
        print(f"끝: 묶음 {len(jobs) - len(failed)}개 완료, 실패 {len(failed)}개" + (" — " + ", ".join(f"{x.source} {hms(x.start)}" for x in failed[:20]) if failed else ""))
        progress("end", analyzed=len(jobs) - len(failed), total=len(jobs), failed=len(failed))
    finally:
        flow.close()   # 중단·오류로 나가도 바로 갈래를 멈추고 파이프라인·임시 폴더를 정리(예외의 traceback이 제너레이터를 붙잡아 두지 않게)
        if llm: llm.close()


def flow_key() -> str:
    """예상 시간 기록 열쇠(eta.py Flow): 파이프라인·설정 버전·실행 옵션 + flow(여러 입력이면 multi, 아니면 overlap)"""
    return E.key(CHOSEN.ref(), {**CHOSEN.run_options, "flow": "multi" if CHOSEN.pipe.multi else "overlap"})

if __name__ == "__main__":
    main()
