"""전사 파이프라인(2026-11-16). 계획: SecondBrainCollector/Documents/음성 전사 개선 계획.md "파이프라인 틀 인터페이스 약속".

- 파이프라인 = 폴더 하나 <name>@<version>/ (pipeline.json + 스크립트). analyze.py를 고치지 않고 폴더를 넣고 빼서 추가·제거한다.
  찾는 곳(앞이 우선, 같은 이름@버전이면 사용자 폴더): %USERPROFILE%/.secondbrain/analysis/pipelines → 저장소 tools/recordings/pipelines
- pipeline.json: {name, version, description, autoRecommend, entry, hints?, settings: {키: {type, default, label}}}
  type: string | number | bool | whisper-model(models/ggml-<이름>.bin, analyze.py --list-models 목록. analyze.py가 실행 전에 있는지 본다)
- 실행: python <entry> --wav W --out O.json --settings S.json [--words] → O.json = {"segments": [{startMs, endMs, text, suspect?}]}
  단어장 힌트(2026-11-16): 매니페스트 hints: true인 파이프라인에만 --hints H.json(H = {prompt, fixes: [{id, wrong, right}], ids})을 더 넘긴다.
  그 파이프라인은 O.json에 vocabUsed: [단어장 항목 id](실제로 쓴 힌트·바꾼 고침 짝)를 낼 수 있고, 구간에 original(바꾸기 전 글)을 둘 수 있다.
  설정 useHints(없으면 켬)·hintMax·hintChars는 analyze.py가 단어장 고르기(POST /api/vocab/pick)에 쓴다.
  S.json은 설정 버전 파일에 기본값을 채운 것. 환경 변수 SECONDBRAIN_ANALYSIS_TOOLS = 도구·모델 폴더(whisper.cpp·models, sbpaths.TOOLS).
- 설정 버전: %USERPROFILE%/.secondbrain/analysis/pipeline-settings/<name>@<version>/<N>.json (N은 1부터). 한 번 쓴 파일은 고치지 않고, 바꾸면 새 번호로 쓴다.
  파이프라인 폴더의 seed-settings/<N>.json은 사용자 폴더에 그 번호가 없을 때만 베낀다(서버 기존 결과 이전과 번호를 맞추려고).
진행도(2026-11-17): 파이프라인은 표준 출력에 한 줄 `@@progress {"percent": 0~100, "text": "글"}`(둘 다 선택)을 낼 수 있다. run()이 줄 단위로 읽어 on_progress(percent·text 딕셔너리)로 넘긴다.
  percent = 그 파이프라인이 맡은 wav 하나 안의 진행. 다른 표준 출력 줄·깨진 줄은 무시, 로그는 표준 오류로. 진행도를 안 내도 그대로 돈다. 너무 잦은 것은 Throttle이 거른다.
실행 옵션(2026-11-17): 결과를 바꾸지 않고 이 기기에서 어떻게 돌릴지만 정하는 값(예: whisper-cli 동시 실행 수 parallel). 설정과 따로 둔다.
  매니페스트 runOptions: {키: {type, default, label}}(settings와 같은 꼴). 설정 버전 파일·find()·서버 기록(이름·버전·settingsVersion)에 들어가지 않는다.
  이 기기의 값: 분석 설정 파일(asrmodels.settings_path(), %USERPROFILE%/.secondbrain/analysis/settings.json)의
  "runOptions": {"<이름>@<버전>" 또는 "<이름>": {키: 값}}. 이름@버전이 이름보다 앞서고, 매니페스트에 없는 키·형식이 틀린 값은 버린다(기본값).
  analyze.py --run-option 키=값이 그 위에 얹힌다(이번 실행만). 매니페스트에 runOptions가 있는 파이프라인에만 --run-options R.json(기본값 채움)을 더 넘긴다.
  형식 "auto-count"(2026-11-18): "auto" 또는 1 이상의 정수(예: parallel = whisper-cli 동시 실행 수, auto = 파이프라인이 자원을 재 스스로 조정).
  진행 줄에 "workers"(1 이상 정수, 지금 동시에 돌리는 프로세스 수)를 낼 수 있다(선택, run()이 percent·text와 함께 넘긴다).
  run(stats={})를 주면 O.json의 "stats"(파이프라인 통계, 선택)를 담아 준다.
"""
import json, os, re, shutil, subprocess, sys, threading, time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths

REPO = Path(__file__).parent / "pipelines"
USER = sbpaths.ANALYSIS / "pipelines"
SETTINGS = sbpaths.ANALYSIS / "pipeline-settings"
KEY = re.compile(r"^([^@\\/]+)@([^@\\/]+)$")


@dataclass
class Pipeline:
    name: str
    version: str
    description: str
    auto_recommend: bool
    entry: Path
    schema: dict
    folder: Path
    hints: bool = False   # --hints(단어장 힌트 파일)를 받는가
    run_schema: dict | None = None   # 실행 옵션 {키: {type, default, label}}(없으면 --run-options를 넘기지 않음)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


def parse_key(key: str):
    """'whisper-cpp@1' → ('whisper-cpp', '1'). 꼴이 아니면 ValueError"""
    m = KEY.match(key.strip())
    if not m: raise ValueError(f"파이프라인은 <이름>@<버전> 꼴이어야 합니다: {key}")
    return m.group(1), m.group(2)


def load(folder: Path) -> Pipeline:
    """폴더 하나의 pipeline.json. 폴더 이름과 매니페스트의 이름@버전이 다르거나 entry가 없으면 ValueError"""
    m = json.loads((folder / "pipeline.json").read_text(encoding="utf-8-sig"))
    name, version = str(m.get("name") or ""), str(m.get("version") or "")
    if f"{name}@{version}" != folder.name:
        raise ValueError(f"폴더 이름({folder.name})과 pipeline.json의 이름@버전({name}@{version})이 다릅니다")
    entry = folder / str(m.get("entry") or "")
    if not m.get("entry") or not entry.is_file():
        raise ValueError(f"entry 파일이 없습니다: {entry}")
    schema = m.get("settings") or {}
    if not isinstance(schema, dict) or not all(isinstance(v, dict) for v in schema.values()):
        raise ValueError("settings는 {키: {type, default, label}} 꼴이어야 합니다")
    run_schema = m.get("runOptions") or {}
    if not isinstance(run_schema, dict) or not all(isinstance(v, dict) for v in run_schema.values()):
        raise ValueError("runOptions는 {키: {type, default, label}} 꼴이어야 합니다")
    if both := sorted(set(schema) & set(run_schema)):
        raise ValueError(f"settings와 runOptions에 같은 키가 있습니다: {', '.join(both)}")
    return Pipeline(name, version, str(m.get("description") or ""), bool(m.get("autoRecommend")), entry, schema, folder, m.get("hints") is True, run_schema)


def discover(roots: list | None = None, warn=None) -> dict:
    """{이름@버전: Pipeline}. roots 앞쪽이 우선. 깨진 폴더는 warn(메시지)로 알리고 건너뛴다."""
    out = {}
    for root in roots if roots is not None else [USER, REPO]:
        if not root.is_dir(): continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not KEY.match(d.name) or not (d / "pipeline.json").exists() or d.name in out: continue
            try:
                out[d.name] = load(d)
            except (OSError, ValueError) as e:
                if warn: warn(f"파이프라인 건너뜀 {d}: {e}")
    return out


def settings_dir(p: Pipeline, root: Path | None = None) -> Path:
    return (root or SETTINGS) / p.key


def seed(p: Pipeline, root: Path | None = None) -> list:
    """seed-settings/<N>.json 중 사용자 폴더에 없는 번호만 베낀다(있는 것은 그대로). → 베낀 번호들"""
    src = p.folder / "seed-settings"
    if not src.is_dir(): return []
    dst, done = settings_dir(p, root), []
    for f in sorted(src.glob("*.json")):
        if not re.fullmatch(r"[1-9]\d*", f.stem) or (dst / f.name).exists(): continue
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst / f.name)
        done.append(int(f.stem))
    return done


def versions(p: Pipeline, root: Path | None = None) -> list:
    d = settings_dir(p, root)
    if not d.is_dir(): return []
    return sorted(int(f.stem) for f in d.glob("*.json") if re.fullmatch(r"[1-9]\d*", f.stem))


def defaults(p: Pipeline) -> dict:
    return {k: v.get("default") for k, v in p.schema.items()}


def effective(p: Pipeline, values: dict) -> dict:
    """기본값 위에 값을 얹는다. 매니페스트에 없는 키도 그대로 둔다."""
    return {**defaults(p), **(values or {})}


def read(p: Pipeline, n: int, root: Path | None = None) -> dict:
    """설정 버전 n(기본값 채움). 파일이 없으면 FileNotFoundError"""
    return effective(p, json.loads((settings_dir(p, root) / f"{n}.json").read_text(encoding="utf-8-sig")))


def write(p: Pipeline, values: dict, root: Path | None = None, floor: int = 0) -> int:
    """새 설정 버전(이 PC의 가장 큰 번호와 floor 중 큰 것 + 1)으로 쓴다. floor = 서버 목록의 그 파이프라인 설정 버전 최대(다른 PC가 쓴 번호와 겹치지 않게).
    있는 파일은 덮지 않는다(x 모드). → 번호"""
    d = settings_dir(p, root)
    d.mkdir(parents=True, exist_ok=True)
    n = max(versions(p, root) + [floor]) + 1
    while True:
        try:
            with open(d / f"{n}.json", "x", encoding="utf-8") as f:
                json.dump(effective(p, values), f, ensure_ascii=False, indent=2)
            return n
        except FileExistsError:   # 데스크톱이 같은 때 썼으면 다음 번호
            n += 1


def find(p: Pipeline, values: dict, root: Path | None = None) -> int | None:
    """기본값을 채워 같은 설정인 가장 이른 버전. 없으면 None. 깨진 파일은 건너뛴다."""
    want = effective(p, values)
    for n in versions(p, root):
        try:
            if read(p, n, root) == want: return n
        except (OSError, ValueError):
            pass
    return None


def find_or_write(p: Pipeline, values: dict, root: Path | None = None, floor: int = 0) -> int:
    n = find(p, values, root)
    return n if n is not None else write(p, values, root, floor)


def run_defaults(p: Pipeline) -> dict:
    return {k: v.get("default") for k, v in (p.run_schema or {}).items()}


def fits(kind, value) -> bool:
    if kind == "number": return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value
    if kind == "auto-count":   # "auto" 또는 1 이상의 정수(2.0도 정수로 본다)
        if isinstance(value, str): return value.strip().lower() == "auto"
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value >= 1 and float(value).is_integer()
    if kind == "bool": return isinstance(value, bool)
    return isinstance(value, str)


def run_values(p: Pipeline, local: dict | None = None, overrides: dict | None = None) -> dict:
    """실행 옵션 = 매니페스트 기본값 ← 이 기기 값(local[이름] ← local[이름@버전]) ← overrides(--run-option).
    매니페스트에 없는 키와 형식이 틀린 값은 버린다. 실행 옵션이 없는 파이프라인이면 {}"""
    out = run_defaults(p)
    layers = [(local or {}).get(p.name), (local or {}).get(p.key), overrides]
    for layer in layers:
        if not isinstance(layer, dict): continue
        for k, v in layer.items():
            if k in out and fits((p.run_schema[k] or {}).get("type", "string"), v): out[k] = v
    return out


def local_run_options(path: Path) -> dict:
    """분석 설정 파일의 "runOptions"({이름@버전 또는 이름: {키: 값}}). 파일이 없거나 깨졌으면 {}"""
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8-sig")).get("runOptions")
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def parse_run_option(text: str) -> tuple:
    """--run-option 키=값 → (키, 값). 값은 JSON으로 읽고(2, true, "x") 안 되면 글. '='가 없으면 ValueError"""
    k, sep, v = text.partition("=")
    if not sep or not k.strip(): raise ValueError(f"--run-option은 키=값 꼴이어야 합니다: {text}")
    try:
        return k.strip(), json.loads(v)
    except ValueError:
        return k.strip(), v


def server_versions(rows, p: Pipeline) -> list:
    """서버 목록(GET /api/transcription/pipelines 응답) 중 이 파이프라인의 설정 버전들"""
    out = []
    for r in rows or []:
        try:
            if str(r.get("name")) == p.name and str(r.get("version")) == p.version: out.append(int(r["settingsVersion"]))
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    return sorted(out)


def listing(pipes: dict, root: Path | None = None, local: dict | None = None) -> list:
    """analyze.py --list-pipelines 한 줄씩(약속의 모양 그대로). 실행 옵션이 있으면 runOptionsSchema와 이 기기 값 runOptions(local = local_run_options)"""
    out = []
    for p in pipes.values():
        seed(p, root)
        vs = versions(p, root)
        row = {"name": p.name, "version": p.version, "description": p.description, "autoRecommend": p.auto_recommend,
               "settingsSchema": p.schema, "settingsVersions": vs, "latestSettingsVersion": vs[-1] if vs else None, "folder": str(p.folder), "hints": p.hints}
        if p.run_schema: row.update(runOptionsSchema=p.run_schema, runOptions=run_values(p, local))
        out.append(row)
    return out


def wants_hints(p: Pipeline, settings: dict) -> bool:
    """이 항목으로 전사할 때 단어장 힌트를 만들어 넘길까(매니페스트 hints + 설정 useHints, 없으면 켬)"""
    return p.hints and settings.get("useHints", True) is not False


PROGRESS_PREFIX = "@@progress "
PROGRESS_GAP = 0.25   # 초당 4번 넘게 넘기지 않는다


def parse_progress(line: str) -> dict | None:
    """파이프라인 표준 출력 한 줄 → {"percent"?: 0~100 실수, "text"?: 글, "workers"?: 1 이상 정수}. 약속의 줄이 아니거나 JSON이 깨졌거나 객체가 아니거나 쓸 값이 없으면 None.
    percent는 숫자(bool·NaN 아님)만 0~100으로 자르고, text는 문자열만(앞뒤 공백을 떼고, 비면 생략), workers는 1 이상 정수만. 잘못된 칸은 버리고 나머지만 쓴다."""
    if not line.startswith(PROGRESS_PREFIX): return None
    try:
        raw = json.loads(line[len(PROGRESS_PREFIX):])
    except ValueError:
        return None
    if not isinstance(raw, dict): return None
    out = {}
    pct = raw.get("percent")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct == pct:
        out["percent"] = min(100.0, max(0.0, float(pct)))
    text = raw.get("text")
    if isinstance(text, str) and text.strip(): out["text"] = text.strip()
    w = raw.get("workers")
    if isinstance(w, int) and not isinstance(w, bool) and w >= 1: out["workers"] = w
    return out or None


class Throttle:
    """ok()가 PROGRESS_GAP초에 한 번만 True(처음은 True). 거른 것은 버린다(다음 것이 최신 값을 싣는다)"""
    def __init__(self, gap: float = PROGRESS_GAP, clock=time.monotonic):
        self.gap, self.clock, self.last = gap, clock, None

    def ok(self) -> bool:
        now = self.clock()
        if self.last is not None and now - self.last < self.gap: return False
        self.last = now
        return True


def run(p: Pipeline, settings: dict, wav: Path, tmp: Path, words: bool = False, tools: Path | None = None,
        hints: Path | None = None, used: set | None = None, on_progress=None, run_options: dict | None = None, stats: dict | None = None) -> list:
    """파이프라인 하나로 wav → 구간들 [{startMs, endMs, text, suspect?, original?}]. 실패하면 RuntimeError(stderr 끝)
    hints = 단어장 힌트 파일(매니페스트 hints인 파이프라인에만 넘긴다). used를 주면 O.json의 vocabUsed(쓴 단어장 id)를 더한다.
    on_progress = 파이프라인이 낸 @@progress 줄마다 부르는 함수(parse_progress의 딕셔너리, 거르지 않음).
    run_options = 실행 옵션 값(run_values). 매니페스트에 runOptions가 있으면 기본값을 채워 --run-options로 넘긴다.
    stats = 딕셔너리를 주면 O.json의 "stats"(객체일 때)를 담는다."""
    s_path, o_path = tmp / f"{wav.stem}.settings.json", tmp / f"{wav.stem}.segments.json"
    s_path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    extra = []
    if p.run_schema:
        r_path = tmp / f"{wav.stem}.run-options.json"
        r_path.write_text(json.dumps(run_values(p, overrides=run_options), ensure_ascii=False), encoding="utf-8")
        extra = ["--run-options", str(r_path)]
    o_path.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "SECONDBRAIN_ANALYSIS_TOOLS": str(tools or sbpaths.TOOLS)}
    cmd = [sys.executable, str(p.entry), "--wav", str(wav), "--out", str(o_path), "--settings", str(s_path)] + (["--words"] if words else []) + (["--hints", str(hints)] if hints and p.hints else []) + extra
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(p.folder))
    err, other = [], deque(maxlen=20)   # 표준 오류는 따로 읽어(안 읽으면 가득 차 멈춘다) 끝만 남긴다
    reader = threading.Thread(target=lambda: err.extend(deque(proc.stderr, maxlen=20)), daemon=True)
    reader.start()
    try:
        for line in proc.stdout:
            ev = parse_progress(line)
            if ev is None:
                if not line.startswith(PROGRESS_PREFIX): other.append(line)
            elif on_progress:
                on_progress(ev)
        returncode = proc.wait()
    finally:
        if proc.poll() is None: proc.kill()
        reader.join()
        proc.stdout.close(); proc.stderr.close()
    if returncode != 0:
        raise RuntimeError(f"{p.key} 실패(종료 코드 {returncode}): " + ("".join(err) or "".join(other))[-400:].strip())
    try:
        res = json.loads(o_path.read_text(encoding="utf-8-sig"))
        segs = res["segments"]
        if used is not None: used.update(int(x) for x in res.get("vocabUsed") or [])
        if stats is not None and isinstance(res.get("stats"), dict): stats.update(res["stats"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"{p.key} 결과를 읽지 못함({o_path.name}): {e}")
    out = []
    for s in segs:
        text = str(s.get("text") or "").strip()
        if not text: continue
        seg = {"startMs": int(s["startMs"]), "endMs": int(s["endMs"]), "speaker": None, "text": text}
        if s.get("suspect"): seg["suspect"] = True
        if s.get("original") and str(s["original"]).strip() != text: seg["original"] = str(s["original"]).strip()
        out.append(seg)
    return out
