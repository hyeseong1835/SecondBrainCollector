"""전사 모델 고르기(2026-11-13). analyze.py --asr-model과 데스크톱(설정·녹음 화면, Native/AsrModels.cs)이 같은 규칙을 쓴다.

- 쓸 수 있는 모델: %LOCALAPPDATA%/SecondBrain/analysis/models/ggml-*.bin(도구·모델은 옮기지 않음, sbpaths.py) 중 whisper 다국어 모델.
  뺀다: silero(VAD), *.en(영어 전용이라 -l ko와 안 맞음), for-tests(whisper.cpp 시험용 빈 모델).
- 이름: 파일 이름에서 "ggml-"와 ".bin"을 뗀 것(결과 행 asrModel과 같은 꼴). 예: batisay-ko-turbo-q8_0
- 기본 모델: %USERPROFILE%/.secondbrain/analysis/settings.json의 "asrModel"(없으면 옛 %LOCALAPPDATA%/SecondBrain/analysis/settings.json, 2026-11-14). 시험 인스턴스(환경 변수 SECONDBRAIN_INSTANCE)면
  settings.<이름>.json이 있을 때 그것. 파일이 없거나 그 모델이 없으면 DEFAULT.
- 단어 단위 타임스탬프(-ml 1 -sow, 화자 구분에 필요)와 -l ko는 whisper 다국어 모델이면 모두 된다(2026-11-13 batisay·large-v3-turbo 실측).
"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths

DEFAULT = "batisay-ko-turbo-q8_0"
ANALYSIS = sbpaths.TOOLS   # 모델 폴더의 부모
SETTINGS = sbpaths.ANALYSIS          # settings.json 폴더(2026-11-14)
LEGACY_SETTINGS = sbpaths.LEGACY_ANALYSIS


def models_dir() -> Path:
    return ANALYSIS / "models"


def name_of(path: Path) -> str:
    return path.name.removeprefix("ggml-").removesuffix(".bin")


def usable(path: Path) -> bool:
    n = name_of(path).lower()
    return path.name.startswith("ggml-") and path.suffix == ".bin" and not (
        n.startswith("silero") or n.endswith(".en") or ".en-" in n or "for-tests" in n)


def available(folder: Path | None = None) -> list:
    """[(이름, 경로, 바이트)] 이름 순."""
    folder = folder or models_dir()
    if not folder.exists(): return []
    out = [(name_of(p), p, p.stat().st_size) for p in folder.glob("ggml-*.bin") if usable(p)]
    return sorted(out, key=lambda x: x[0])


def settings_path() -> Path:
    inst = os.environ.get("SECONDBRAIN_INSTANCE")
    if inst:
        p = sbpaths.prefer(SETTINGS / f"settings.{inst}.json", LEGACY_SETTINGS / f"settings.{inst}.json")
        if p.exists(): return p
    return sbpaths.prefer(SETTINGS / "settings.json", LEGACY_SETTINGS / "settings.json")


def configured() -> str | None:
    """settings.json의 asrModel(없거나 깨졌으면 None)."""
    try:
        v = json.load(open(settings_path(), encoding="utf-8")).get("asrModel")
        return v.strip() if isinstance(v, str) and v.strip() else None
    except (OSError, ValueError, AttributeError):
        return None


def normalize(name: str) -> str:
    """'ggml-x.bin'·'x.bin'·'x' → 'x'"""
    return Path(name.strip()).name.removeprefix("ggml-").removesuffix(".bin")


def resolve(name: str | None, folder: Path | None = None):
    """고른 이름(없으면 설정값 → DEFAULT) → (이름, 경로). 없는 모델이면 (이름, None)."""
    folder = folder or models_dir()
    want = normalize(name) if name else None
    if not want:
        want = configured()
        if want and not (folder / f"ggml-{normalize(want)}.bin").exists():
            want = None   # 설정값 모델이 지워졌으면 기본으로
        want = normalize(want) if want else DEFAULT
    p = folder / f"ggml-{want}.bin"
    return want, (p if p.exists() and usable(p) else None)
