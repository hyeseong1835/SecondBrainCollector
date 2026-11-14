"""이 PC의 SecondBrain 설정 위치(2026-11-14). 데스크톱(LifeLog.Core DataAreas)과 같은 규칙.

- 설정 폴더: %USERPROFILE%/.secondbrain (환경 변수 SECONDBRAIN_DATA_DIR로 바꿀 수 있음, 시험용).
  Claude 앱 셸에서 %LOCALAPPDATA%에 쓰면 패키지 사본(%LOCALAPPDATA%/Packages/Claude_…/LocalCache/Local)에만 남아
  바로가기로 켠 데스크톱이 못 봐서 옮겼다. 사용자 프로필의 다른 폴더는 가상화되지 않는다.
- api.json(url·key, 훅 키): .secondbrain/api.json → 없으면 옛 %LOCALAPPDATA%/SecondBrain/api.json.
- 분석 설정·잠금(settings.json·analyze.lock): .secondbrain/analysis → 설정은 없으면 옛 %LOCALAPPDATA%/SecondBrain/analysis.
- 분석 도구·모델(whisper·llama·ffmpeg, 수 GB): 옮기지 않는다. 실제 %LOCALAPPDATA%/SecondBrain/analysis → Claude 사본 순으로 찾는다(find_tools).
- 옛 위치에서 베끼는 일은 데스크톱이 한다(원본은 둔다).
"""
import os
from pathlib import Path

LOCAL = Path(os.environ.get("LOCALAPPDATA", "."))
HOME = Path(os.environ["SECONDBRAIN_DATA_DIR"]) if os.environ.get("SECONDBRAIN_DATA_DIR") else Path.home() / ".secondbrain"
ANALYSIS = HOME / "analysis"                 # 설정·대기열·잠금
LEGACY_ANALYSIS = LOCAL / "SecondBrain" / "analysis"


def tools_candidates() -> list:
    """도구·모델 폴더 후보(앞이 우선): .secondbrain/analysis → 실제 %LOCALAPPDATA% → Claude 패키지 사본들(최근 것부터).
    2026-11-14 확인: 도구를 Claude 셸에서 받아 사본에만 있었다. 사본 경로는 보통 폴더라 어디서 켜든 읽힌다."""
    out = [ANALYSIS, LEGACY_ANALYSIS]
    pk = LOCAL / "Packages"
    try:
        copies = [p / "LocalCache" / "Local" / "SecondBrain" / "analysis" for p in pk.glob("Claude_*")]
        out += sorted((c for c in copies if c.exists()), key=lambda c: c.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return out


def find_tools() -> Path:
    if os.environ.get("SECONDBRAIN_ANALYSIS_TOOLS"): return Path(os.environ["SECONDBRAIN_ANALYSIS_TOOLS"])
    return next((c for c in tools_candidates() if (c / "models").is_dir()), LEGACY_ANALYSIS)


TOOLS = find_tools()   # 도구·모델(옮기지 않음, 데스크톱 DataAreas.AnalysisToolsDir와 같은 규칙)


def prefer(new: Path, old: Path) -> Path:
    """새 위치에 있으면 새 것, 없고 옛 위치에 있으면 옛 것, 둘 다 없으면 새 것."""
    return new if new.exists() or not old.exists() else old


def api_json() -> Path:
    return prefer(HOME / "api.json", LOCAL / "SecondBrain" / "api.json")
