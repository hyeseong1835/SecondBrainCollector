"""이름 구간(2026-11-18 통화 이름 기록 → 2026-11-19 플랫폼별, 서버 sb.person_names, GET /api/people/names).

사람은 플랫폼마다 다른 이름으로 나온다. 서버에 "플랫폼 P에서 이름 N(식별자 A)이 from~to에 사람 X" 줄이 있고, 이것으로 그날의 이름을 사람으로 푼다.
  phonebook(전화번호부): 통화 녹음 파일 이름 "통화 <상대>_YYMMDD_HHMMSS"의 <상대> = 폰 연락처 표시 이름. 연락처 이름을 바꾸면 같은 사람이 다른 이름으로 나온다
             ("CK 김청아 누나" → "CK26 김청아님").
  discord:   사용자 id(account)마다. 사람(person) = 지정 이름. 사람이 정하지 않았으면(자동 줄뿐) 전역 이름이 기본(공통 약속 "디스코드 봇 메타데이터.md").
  kakao:     카톡 보낸 사람 이름(수집기는 쓰지 않음).
서버는 데이터에서 본 이름·식별자마다 자동 줄(auto)을 만들고, 사람이 확정·고친다.

쓰는 곳:
  analyze.py run() 시작 때 load(call) 한 번 → 이 실행 동안 BOOK(전화번호부)·DISCORD에 둔다(서버를 못 읽으면 빈 책 = 지금까지처럼 이름 그대로).
    새 서버(0053)는 GET /api/people/names, 옛 서버(0051)는 GET /api/people/call-names(전화번호부만)로 물러선다.
  partner(path): 통화 녹음의 상대 = 그날 그 이름의 사람. 화자 이름(나·상대)·목소리 등록(voices.enroll_call)·요약 맥락·단어장 caller가 이 이름을 쓴다.
  discord_name(user_id, fallback, date): 디스코드 사용자 id의 지정 이름. 없으면 fallback(부르는 쪽이 global_name → username → id).
  sync_aliases(store): 기간과 상관없이 한 사람만 가리키는 이름(전화번호부 + 지정한 디스코드 계정의 이름)은 목소리 저장소 별칭(voices.Store.set_alias)으로도 맞춘다
    → 옛 연락처 이름·옛 디스코드 이름으로 등록된 표본이 그 사람으로 합쳐진다. 이름이 때에 따라 다른 사람이면 별칭으로 만들지 않는다.
  observe_discord(call, root): 디스코드 봇 녹음 폴더의 .json(user_id·global_name)과 세션 파일 events "name"을 서버 POST /api/people/names/observe로 보낸다
    (서버가 계정마다 이름 구간을 만들고, 전역 이름이 바뀌면 다음 구간으로). 지난번 마지막 날짜 폴더부터 바뀐 파일만(상태 ~/.secondbrain/analysis/discord-names.json,
    처음엔 최근 이틀). 지난 녹음 전부(한 번): python callnames.py observe-discord --all [--dry-run]

풀기 규칙(서버 NameBook과 같다, 바꾸면 둘 다):
  이름은 공백을 모두 빼고 소문자로 견준다. 날짜(KST, 파일 이름의 YYMMDD)가 기간 안(from ≤ 날짜 ≤ to, null = 열린 끝)인 줄 중 기간이 가장 좁은 줄.
  열린 끝은 아주 넓게(한쪽 열림 < 둘 다 열림). 같으면 from이 늦은 줄, 그다음 id가 큰 줄. 날짜를 모르면 to가 열린 줄, 없으면 to가 가장 늦은 줄.
  맞는 줄이 없으면 이름 그대로.
  식별자(디스코드 id): 그 id의 줄 중 날짜가 든 줄, 없으면 날짜에 가장 가까운 줄. 지정 이름은 그 id에 확정 줄(auto=false)이 있을 때만.
"""
import datetime as dt, json, os, re
from pathlib import Path

OPEN = 1_000_000
PHONEBOOK, DISCORD_P, KAKAO = "phonebook", "discord", "kakao"


def norm(s) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _date(s):
    return dt.date.fromisoformat(s) if s else None


class Book:
    """서버 줄 [{id, platform?, account?, person, name, from, to, auto?, ...}] 중 한 플랫폼(기본 전화번호부) → 이름·식별자 풀기.
    platform이 없는 줄(옛 /call-names)은 전화번호부."""
    def __init__(self, rows=None, platform=PHONEBOOK):
        self.platform = platform
        self.rows = []
        self.by_name = {}
        self.by_account = {}
        for r in rows or []:
            if (r.get("platform") or PHONEBOOK) != platform: continue
            if not r.get("name") or not r.get("person"): continue
            acc = (r.get("account") or r.get("phone") or "").strip() or None
            e = {"id": int(r.get("id") or 0), "person": r["person"].strip(), "name": r["name"].strip(), "from": _date(r.get("from")), "to": _date(r.get("to")),
                 "account": acc, "auto": bool(r.get("auto"))}
            self.rows.append(e)
            self.by_name.setdefault(norm(e["name"]), []).append(e)
            if acc: self.by_account.setdefault(acc, []).append(e)

    def __len__(self):
        return len(self.rows)

    @staticmethod
    def _span(e):
        if e["from"] and e["to"]: return (e["to"] - e["from"]).days
        return OPEN * 2 if not e["from"] and not e["to"] else OPEN

    @staticmethod
    def _covers(e, date):
        return (e["from"] is None or e["from"] <= date) and (e["to"] is None or date <= e["to"])

    def find(self, name, date=None):
        """그날 이 이름의 줄(dict) 또는 None"""
        lst = self.by_name.get(norm(name))
        if not lst: return None
        if date is None:
            return sorted(lst, key=lambda e: (e["to"] is None, e["to"] or dt.date.max, e["id"]), reverse=True)[0]
        hits = [e for e in lst if self._covers(e, date)]
        if not hits: return None
        return sorted(hits, key=lambda e: (self._span(e), -(e["from"] or dt.date.min).toordinal(), -e["id"]))[0]

    def person(self, name, date=None) -> str:
        e = self.find(name, date)
        return e["person"] if e else (name or "").strip()

    def find_account(self, account, date=None):
        """그날 이 식별자의 줄. 그날을 덮는 줄이 없으면 가장 가까운 줄, 식별자를 모르면 None"""
        lst = self.by_account.get(str(account or "").strip())
        if not lst: return None
        if date is None:
            return sorted(lst, key=lambda e: (e["to"] is None, e["from"] or dt.date.min, e["id"]), reverse=True)[0]
        hits = [e for e in lst if self._covers(e, date)]
        if hits: return sorted(hits, key=lambda e: (self._span(e), -e["id"]))[0]
        def dist(e):
            if e["from"] and date < e["from"]: return (e["from"] - date).days
            if e["to"] and date > e["to"]: return (date - e["to"]).days
            return 0
        return sorted(lst, key=lambda e: (dist(e), -e["id"]))[0]

    def assigned(self, account) -> bool:
        """사람이 정한(확정) 줄이 있는 식별자인지(디스코드 지정 이름)"""
        return any(not e["auto"] for e in self.by_account.get(str(account or "").strip(), []))

    def unambiguous(self, assigned_only=False) -> dict:
        """이름(원래 모양) → 사람: 모든 줄이 한 사람만 가리키고 이름과 사람이 다른 것. assigned_only면 지정한 계정의 줄만(디스코드)"""
        out = {}
        for lst in self.by_name.values():
            if assigned_only: lst = [e for e in lst if e["account"] and self.assigned(e["account"])]
            if not lst: continue
            people = {e["person"] for e in lst}
            if len(people) == 1 and norm(lst[0]["name"]) != norm(lst[0]["person"]):
                out[lst[0]["name"]] = lst[0]["person"]
        return out


BOOK = Book()
DISCORD = Book(platform=DISCORD_P)


def load(call, log=print) -> Book:
    """GET /api/people/names 전부를 받아 BOOK(전화번호부)·DISCORD에 둔다. 옛 서버면 /api/people/call-names(전화번호부만).
    둘 다 실패하면(오프라인) 빈 책: 이름을 그대로 쓴다"""
    global BOOK, DISCORD
    try:
        try:
            rows = call("GET", "/api/people/names") or []
        except Exception as e:
            if "404" not in str(e): raise
            rows = call("GET", "/api/people/call-names") or []
        BOOK, DISCORD = Book(rows), Book(rows, DISCORD_P)
        if len(BOOK) or len(DISCORD): log(f"이름 구간 전화번호부 {len(BOOK)}줄 · 디스코드 {len(DISCORD)}줄")
    except Exception as e:
        BOOK, DISCORD = Book(), Book(platform=DISCORD_P)
        log(f"이름 구간을 못 읽음(이름 그대로 씀): {e}")
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


def discord_name(user_id, fallback=None, date=None, book: Book | None = None):
    """디스코드 사용자 id의 지정 이름(사람이 정한 줄이 있을 때 그날 줄의 사람). 없으면 fallback(global_name → username → id는 부르는 쪽)"""
    b = book or DISCORD
    if user_id and b.assigned(user_id):
        e = b.find_account(user_id, date)
        if e: return e["person"]
    return fallback


def sync_aliases(store, book: Book | None = None, save=True, log=print, discord: Book | None = None) -> int:
    """한 사람만 가리키는 이름(전화번호부 + 지정한 디스코드 계정의 이름)을 목소리 저장소 별칭으로(이미 같으면 그대로). 바꾼 별칭 수.
    두 플랫폼이 같은 이름을 다른 사람으로 가리키면 만들지 않는다. 사람이나 이름이 '나'면 건너뛴다('나'를 다른 이름으로 접지 않고, 무엇도 '나'로 접지 않는다)"""
    pairs = dict((book or BOOK).unambiguous())
    for name, person in (discord if discord is not None else DISCORD).unambiguous(assigned_only=True).items():
        if name in pairs and norm(pairs[name]) != norm(person): pairs.pop(name); continue
        pairs.setdefault(name, person)
    n, moved = 0, 0
    for name, person in pairs.items():
        cn, cp = store.canonical(name), store.canonical(person)
        if "나" in (person, name, cn, cp) or cn == cp: continue
        try:
            moved += store.set_alias(name, person)
            n += 1
        except ValueError as e:
            log(f"  이름 구간 별칭 건너뜀 {name} → {person}: {e}")
    if n:
        log(f"이름 구간으로 목소리 별칭 {n}개(표본 {moved}개 옮김)")
        if save: store.save()
    return n


# ───────── 디스코드 봇 녹음 → 이름 관찰 ─────────

def _bot_name(d) -> str:
    """기본 표시 이름: global_name → username → id(공통 약속 문서)"""
    return (d.get("global_name") or "").strip() or (d.get("username") or "").strip() or str(d.get("user_id") or "")


def discord_observations(files) -> list:
    """봇 폴더의 파일들 → 관찰 [{platform, account, name, date, track}] (같은 것은 하나로).
    날짜 = 그 시각(이 PC 지역 시각 ISO)의 앞 10자. 같은 날 안은 시각 순.
    사람별 .json: global_name 필드가 있으면(2026-11-19 뒤 파일) 전역 이름 → username, track=true. 옛 파일은 speaker(별명일 수 있음)를 track=false로(없을 때만 만든다).
    세션 파일(*.session.json): events type "name"(처음 앎·바뀜)을 그 시각 날짜로 track=true. 봇 계정(bot=true)은 뺀다."""
    out = {}
    def add(uid, name, at, track):
        uid, name, at = str(uid or "").strip(), (name or "").strip(), str(at or "")
        date = at[:10]
        if not uid or not name or len(date) < 10: return
        k = (uid, norm(name), date)
        if k not in out or (track and not out[k]["track"]) or (track == out[k]["track"] and at < out[k]["at"]):
            out[k] = {"platform": DISCORD_P, "account": uid, "name": name, "date": date, "track": bool(track), "at": at}
    for f in files:
        f = Path(f)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict): continue
        if d.get("kind") == "discord-bot-session" or f.name.endswith(".session.json"):
            users = d.get("users") or {}
            for ev in d.get("events") or []:
                if ev.get("type") != "name": continue
                uid = str(ev.get("user_id") or "")
                if ev.get("bot") or (users.get(uid) or {}).get("bot"): continue
                add(uid, _bot_name({**ev, "user_id": uid}), ev.get("at"), True)
            continue
        if d.get("source") != "discord-bot" or d.get("bot"): continue
        if "global_name" in d: add(d.get("user_id"), _bot_name(d), d.get("start"), True)
        else: add(d.get("user_id"), d.get("speaker") or d.get("username"), d.get("start"), False)
    # 서버는 같은 날 안에서 보낸 순서대로 본다(같은 날 이름이 바뀌면 나중 것): 날짜 → 시각 순
    return [{k: v for k, v in x.items() if k != "at"} for x in sorted(out.values(), key=lambda x: (x["date"], x["at"], x["account"], not x["track"]))]


def _state_path() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".secondbrain" / "analysis" / "discord-names.json"


def observe_discord(call, root, state: Path | None = None, all_files=False, dry_run=False, log=print, today: dt.date | None = None) -> dict:
    """봇 녹음 폴더(root = …/SecondBrainRecordings/discord-bot)에서 지난번 뒤로 바뀐 .json을 읽어 POST /api/people/names/observe(500건씩).
    날짜 폴더(<yyyy-MM-dd>)는 지난번 마지막 폴더부터만 본다(옛 폴더 파일은 바뀌지 않는다. 파일이 수천 개라 다 읽으면 몇 분 걸린다).
    상태가 없으면(처음) 최근 이틀 폴더만: 지난 녹음 전부는 python callnames.py observe-discord --all로 한 번.
    돌려줌 {files, items, created, updated}. dry_run이면 보내지 않고 상태도 안 쓴다. 실패하면(옛 서버 404·오프라인) 상태를 두고 다음에 다시"""
    state = state or _state_path()
    root = Path(root)
    since, first = 0.0, ""
    if all_files:
        pass
    elif state.exists():
        try:
            st = json.loads(state.read_text(encoding="utf-8"))
            since, first = float(st.get("scannedUntil") or 0), str(st.get("lastFolder") or "")
        except (OSError, ValueError):
            since, first = 0.0, ""
    else:
        first = ((today or dt.date.today()) - dt.timedelta(days=1)).isoformat()
    files, newest, last = [], since, first
    if root.exists():
        for d in sorted(x for x in root.iterdir() if x.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x.name) and x.name >= first):
            last = max(last, d.name)
            for f in d.glob("*.json"):
                try: m = f.stat().st_mtime
                except OSError: continue
                if m > since:
                    files.append(f); newest = max(newest, m)
    items = discord_observations(files)
    res = {"files": len(files), "items": len(items), "created": 0, "updated": 0}
    if dry_run: return res
    if items:
        try:
            for i in range(0, len(items), 500):
                r = call("POST", "/api/people/names/observe", {"items": items[i:i + 500]}) or {}
                res["created"] += int(r.get("created") or 0); res["updated"] += int(r.get("updated") or 0)
        except Exception as e:
            log(f"디스코드 이름 관찰을 못 보냄(다음에 다시): {e}")
            return res
    if files or not state.exists(): _save_state(state, newest, last)
    if res["created"] or res["updated"]: log(f"디스코드 이름 구간: 새 줄 {res['created']} · 바뀐 줄 {res['updated']}(파일 {res['files']}개)")
    return res


def _save_state(state: Path, newest: float, last_folder: str = ""):
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"scannedUntil": newest, "lastFolder": last_folder}), encoding="utf-8")


if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser(description="이름 구간 도구")
    p.add_argument("cmd", choices=["observe-discord"])
    p.add_argument("--all", action="store_true", help="상태와 상관없이 모든 파일")
    p.add_argument("--dry-run", action="store_true", help="서버에 보내지 않고 관찰만 센다")
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from analyze import api, ROOT
    call = None if a.dry_run else api()
    r = observe_discord(call, ROOT / "discord-bot", all_files=a.all, dry_run=a.dry_run)
    print(json.dumps(r, ensure_ascii=False))
