"""통화 이름 기록(2026-11-18, 서버 sb.call_names, GET /api/people/call-names).

통화 녹음 파일 이름 "통화 <상대>_YYMMDD_HHMMSS"의 <상대>는 폰 연락처 표시 이름이라 연락처 이름을 바꾸면 같은 사람이 다른 이름으로 나온다
("CK 김청아 누나" → "CK26 김청아님"). 서버에 사람이 적은(또는 자동 초안을 확정한) "이 이름은 from~to에 이 사람" 줄로 그날의 이름을 사람으로 푼다.

쓰는 곳:
  analyze.py run() 시작 때 load(call) 한 번 → 이 실행 동안 BOOK에 둔다(서버를 못 읽으면 빈 책 = 지금까지처럼 이름 그대로).
  partner(path): 통화 녹음의 상대 = 그날 그 이름의 사람. 화자 이름(나·상대)·목소리 등록(voices.enroll_call)·요약 맥락·단어장 caller가 이 이름을 쓴다.
  sync_aliases(store): 기간과 상관없이 한 사람만 가리키는 이름은 목소리 저장소 별칭(voices.Store.set_alias)으로도 맞춘다
    → 옛 연락처 이름으로 등록된 표본이 그 사람으로 합쳐진다. 이름이 때에 따라 다른 사람이면(여러 사람) 별칭으로 만들지 않는다.

풀기 규칙(서버 CallNameBook과 같다, 바꾸면 둘 다):
  이름은 공백을 모두 빼고 소문자로 견준다. 날짜(KST, 파일 이름의 YYMMDD)가 기간 안(from ≤ 날짜 ≤ to, null = 열린 끝)인 줄 중 기간이 가장 좁은 줄.
  열린 끝은 아주 넓게(한쪽 열림 < 둘 다 열림). 같으면 from이 늦은 줄, 그다음 id가 큰 줄. 날짜를 모르면 to가 열린 줄, 없으면 to가 가장 늦은 줄.
  맞는 줄이 없으면 이름 그대로.
"""
import datetime as dt, re
from pathlib import Path

OPEN = 1_000_000


def norm(s) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _date(s):
    return dt.date.fromisoformat(s) if s else None


class Book:
    """서버 줄 [{id, person, name, from, to, ...}] → 이름 풀기"""
    def __init__(self, rows=None):
        self.rows = []
        self.by_name = {}
        for r in rows or []:
            if not r.get("name") or not r.get("person"): continue
            e = {"id": int(r.get("id") or 0), "person": r["person"].strip(), "name": r["name"].strip(), "from": _date(r.get("from")), "to": _date(r.get("to"))}
            self.rows.append(e)
            self.by_name.setdefault(norm(e["name"]), []).append(e)

    def __len__(self):
        return len(self.rows)

    @staticmethod
    def _span(e):
        if e["from"] and e["to"]: return (e["to"] - e["from"]).days
        return OPEN * 2 if not e["from"] and not e["to"] else OPEN

    def find(self, name, date=None):
        """그날 이 이름의 줄(dict) 또는 None"""
        lst = self.by_name.get(norm(name))
        if not lst: return None
        if date is None:
            return sorted(lst, key=lambda e: (e["to"] is None, e["to"] or dt.date.max, e["id"]), reverse=True)[0]
        hits = [e for e in lst if (e["from"] is None or e["from"] <= date) and (e["to"] is None or date <= e["to"])]
        if not hits: return None
        return sorted(hits, key=lambda e: (self._span(e), -(e["from"] or dt.date.min).toordinal(), -e["id"]))[0]

    def person(self, name, date=None) -> str:
        e = self.find(name, date)
        return e["person"] if e else (name or "").strip()

    def unambiguous(self) -> dict:
        """이름(원래 모양) → 사람: 모든 줄이 한 사람만 가리키고 이름과 사람이 다른 것"""
        out = {}
        for lst in self.by_name.values():
            people = {e["person"] for e in lst}
            if len(people) == 1 and norm(lst[0]["name"]) != norm(lst[0]["person"]):
                out[lst[0]["name"]] = lst[0]["person"]
        return out


BOOK = Book()


def load(call, log=print) -> Book:
    """GET /api/people/call-names 전부를 받아 BOOK에 둔다. 실패하면(옛 서버·오프라인) 빈 책: 이름을 그대로 쓴다"""
    global BOOK
    try:
        rows = call("GET", "/api/people/call-names") or []
        BOOK = Book(rows)
        if len(BOOK): log(f"통화 이름 기록 {len(BOOK)}줄")
    except Exception as e:
        BOOK = Book()
        log(f"통화 이름 기록을 못 읽음(이름 그대로 씀): {e}")
    return BOOK


def date_of(path) -> dt.date | None:
    """통화 녹음 파일 이름 …_YYMMDD_HHMMSS의 날짜(폰 시각 = KST). 모양이 다르면 None"""
    m = re.search(r"_(\d{6})_\d{6}$", Path(path).stem)
    if not m: return None
    try:
        return dt.datetime.strptime("20" + m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def raw_partner(path) -> str:
    """파일 이름 그대로의 상대(연락처 이름). speakers.call_partner와 같다"""
    m = re.match(r"^(?:통화\s+)?(.+)_\d{6}_\d{6}$", Path(path).stem)
    return m.group(1) if m else Path(path).stem


def partner(path, book: Book | None = None) -> str:
    """통화 녹음의 상대 = 그날 그 연락처 이름의 사람(기록에 없으면 연락처 이름 그대로)"""
    return (book or BOOK).person(raw_partner(path), date_of(path))


def sync_aliases(store, book: Book | None = None, save=True, log=print) -> int:
    """한 사람만 가리키는 통화 이름을 목소리 저장소 별칭으로(이미 같으면 그대로). 바꾼 별칭 수. 사람이 '나'면 건너뛴다"""
    n, moved = 0, 0
    for name, person in (book or BOOK).unambiguous().items():
        if person == "나" or store.canonical(name) == store.canonical(person): continue
        try:
            moved += store.set_alias(name, person)
            n += 1
        except ValueError as e:
            log(f"  통화 이름 별칭 건너뜀 {name} → {person}: {e}")
    if n:
        log(f"통화 이름 기록으로 목소리 별칭 {n}개(표본 {moved}개 옮김)")
        if save: store.save()
    return n
