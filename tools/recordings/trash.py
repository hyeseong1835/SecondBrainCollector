"""말소리 없는 녹음 자동 제거(2026-11-12)의 노트북 쪽: 휴지통으로 옮기기.

전사했는데 말소리가 하나도 안 잡힌 묶음(whisper 환각 문장을 거른 뒤 글 0자)을 휴지통으로 옮긴다.
- 휴지통: %USERPROFILE%\\SecondBrainRecordings\\_trash\\<출처>\\<날짜>\\<이름>.m4a (출처 폴더 밖이라 analyze.py scan·데스크톱 목록에 안 잡힘)
- 목록: _trash\\trash.tsv (source·name·file·trashed_at·purge_after·reason·rule·bundle_start·bundle_files)
- 묘비: <출처 폴더>\\deleted.tsv에 바로 적는다(가져오기가 다시 가져오지 않게). reason 열에 이유
- 서버 분석(글 0자)·수집기 행·index.tsv 줄은 그대로 둔다. 7일 뒤 데스크톱 녹음 화면이 영구 삭제(서버 POST /api/recordings/delete·파일·
  index.tsv 줄·폰 원본)하고, 그 전에는 휴지통 목록에서 듣고 되살릴 수 있다(되살리기 = 파일을 제자리로·묘비 줄과 목록 줄 지움).
"""
import datetime as dt, shutil
from pathlib import Path

TRASH_DAYS = 7
SOURCES = ("phone", "laptop-mic")   # 통화·디스코드 일반·디스코드 봇은 빼기로 함(2026-11-12 사용자 결정)
REASON = "말소리 없음"
RULE = "환각 거른 뒤 전사 글 0자(묶음 전체)"
COLUMNS = ["source", "name", "file", "trashed_at", "purge_after", "reason", "rule", "bundle_start", "bundle_files"]


def is_silent(segments: list, transcript: str) -> bool:
    """전사 결과에 말이 하나도 없는가. analyze.py는 환각 문장(HALLUCINATIONS)을 이미 걸러 segments에 넣지 않는다."""
    return not segments and not (transcript or "").strip()


def trash_dir(root: Path) -> Path:
    return root / "_trash"


def _append(path: Path, header: list, rows: list):
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tail = b"" if new else path.read_bytes()[-1:]
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        if new: f.write("\t".join(header) + "\r\n")
        elif tail != b"\n": f.write("\r\n")   # 끝 줄바꿈 없이 끝난 파일
        for r in rows: f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\r\n")


def tombstone(root: Path, source: str, files: list, now: dt.datetime, reason: str):
    """<출처 폴더>\\deleted.tsv에 줄 더하기. 데스크톱이 만든 파일(name·file·deleted_at 세 열)에도 넷째 열로 이유를 붙인다(읽는 쪽은 name만 본다)."""
    _append(root / source / "deleted.tsv", ["name", "file", "deleted_at", "reason"],
            [[Path(f).name, f, now.strftime("%Y-%m-%dT%H:%M:%S"), reason] for f in files])


def move_bundle(root: Path, source: str, paths: list, bundle_start: str, now: dt.datetime | None = None,
                reason: str = REASON, rule: str = RULE, days: int = TRASH_DAYS) -> list:
    """묶음의 파일들(출처 폴더 안의 실제 경로)을 휴지통으로. 묘비를 먼저 적는다(도중에 멈춰도 다시 가져오지 않게).
    옆 파일(.json 등 이름이 같은 것)도 같이 옮긴다. → 옮긴 파일의 출처 폴더 기준 경로들"""
    now = now or dt.datetime.now().astimezone().replace(microsecond=0)
    base = root / source
    rels = []
    for p in paths:
        p = Path(p)
        rel = p.resolve().relative_to(base.resolve()).as_posix()   # 출처 폴더 밖이면 ValueError
        if rel.startswith("..") or p.suffix.lower() != ".m4a": raise ValueError(f"출처 폴더 안 .m4a가 아님: {p}")
        rels.append(rel)
    tombstone(root, source, rels, now, f"{reason}(자동, {rule})")
    moved = []
    for p, rel in zip(paths, rels):
        p = Path(p)
        dst = trash_dir(root) / source / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        for side in sorted(p.parent.glob(p.stem + ".*")):
            shutil.move(str(side), str(dst.parent / side.name))
        moved.append(rel)
    purge = now + dt.timedelta(days=days)
    _append(trash_dir(root) / "trash.tsv", COLUMNS,
            [[source, Path(rel).name, rel, now.isoformat(), purge.isoformat(), reason, rule, bundle_start, len(rels)] for rel in moved])
    return moved


def listed(root: Path) -> set:
    """휴지통 목록에 든 (출처, 이름)"""
    p = trash_dir(root) / "trash.tsv"
    if not p.exists(): return set()
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    return {(c[0], c[1]) for c in (l.split("\t") for l in lines[1:]) if len(c) > 1}
