"""사람 id(2026-11-19, 서버 0060, 공통 약속 SecondBrainApi docs/사람 id.md): 서버 사람 표의 표시 이름(label)·옛 이름으로 목소리 이름을 맞추고,
목소리 동명이인 신호(voices.voice_checks)를 올린다. 목소리 임베딩은 올리지 않는다(녹음 이름·말한 초·유사도만).

서버 약속:
  GET  /api/persons/directory     {me: {id, name: "나"}, people: [{id, name(label), formerNames: [옛 표시 이름…]}]} (합쳐진 사람 뺌, 숨김 포함)
  POST /api/persons/voice-checks  {items: [{person(label), personId?, samples, files, similarity, groups: [{files, sec}]}]} → {count} (통째로 갈아 끼움)

쓰는 곳:
  analyze.py run() 시작(load_call_names): load(call) → 이 실행 동안 DIRECTORY에 두고 sync_aliases(store) — 옛 이름마다 목소리 별칭 → 지금 label
    (이름을 바꾸거나 합친 사람의 옛 이름으로 등록된 표본이 지금 이름으로 옮겨진다). 이름 구간 별칭(callnames.sync_aliases)보다 먼저.
    못 읽으면(옛 서버 404·오프라인) DIRECTORY = None, 아무것도 안 함(지금까지처럼).
  analyze.py run() 끝(push_at_exit, atexit): 목소리 저장소를 새로 읽어 voice_checks + personId(DIRECTORY에서 label로) → POST 한 번.
    걸린 게 없어도 빈 목록을 보낸다(지난 신호 지우기). 이번 실행에서 사람 표를 못 읽었으면 올리지 않는다. 실패해도 실행은 그대로(경고만). --dry-run은 셈만.
  python speakers.py voice-checks [--json] [--push]   (--push 없으면 이 PC에서 셈만, 서버에 묻지 않음)

옛 이름 → 별칭 규칙(former_pairs, sync_aliases):
  '나'는 어느 쪽에도 두지 않는다(목소리 '나'는 me.npy·speakers.ME_THRESHOLD로 따로). 저장소에서 '나'로 이어 둔 이름(사람이 정함)도 건드리지 않는다.
  옛 이름 = 지금 이름(공백·대소문자 무시, 서버 label 규칙)이면 건너뜀. 옛 이름이 지금 다른 사람의 label이면(그 이름을 이어 쓰는 사람) 건너뜀.
  같은 옛 이름이 여러 사람에게 있으면(나눈 동명이인) 건너뜀. 옛 이름이 저장소에서 이미 다른 사람(지금 label)의 별칭이면 건너뜀.
  지금 이름이 옛 이름의 별칭으로 있으면(반대 방향, 예전에 사람이 정함) 그 별칭을 지우고 뒤집는다.
"""
import atexit, re
import voices as V

ME_NAME = V.ME_NAME


def norm(s) -> str:
    """서버 label 견주기(공백 모두 빼고 소문자, callnames.norm과 같다)"""
    return re.sub(r"\s+", "", s or "").lower()


class Directory:
    """GET /api/persons/directory 응답 → 사람(id·label·옛 이름). 모양이 어긋난 줄은 건너뛴다"""
    def __init__(self, body=None):
        body = body if isinstance(body, dict) else {}
        me = body.get("me") if isinstance(body.get("me"), dict) else {}
        self.me_id = me.get("id")
        self.people = []
        for p in body.get("people") or []:
            if not isinstance(p, dict) or not isinstance(p.get("id"), int) or not isinstance(p.get("name"), str) or not p["name"].strip(): continue
            self.people.append({"id": p["id"], "name": p["name"].strip(),
                                "formerNames": [f.strip() for f in p.get("formerNames") or [] if isinstance(f, str) and f.strip()]})
        self.by_label = {norm(p["name"]): p["id"] for p in self.people}

    def __len__(self):
        return len(self.people)

    def id_of(self, label):
        """label(공백·대소문자 무시) → 사람 id. '나'면 나의 id, 모르면 None"""
        if label == ME_NAME: return self.me_id
        return self.by_label.get(norm(label))

    def former_pairs(self) -> list:
        """[(옛 이름, 지금 label)] — 머리말 규칙의 '나'·같은 이름·다른 사람 label·여러 사람의 옛 이름을 뺀 것"""
        owners = {}
        for p in self.people:
            for f in dict.fromkeys(norm(x) for x in p["formerNames"]):
                owners.setdefault(f, set()).add(p["id"])
        out = []
        for p in self.people:
            if p["name"] == ME_NAME or norm(p["name"]) == norm(ME_NAME): continue
            for f in dict.fromkeys(p["formerNames"]):
                k = norm(f)
                if f == ME_NAME or k == norm(ME_NAME) or k == norm(p["name"]): continue
                if k in self.by_label or len(owners.get(k, ())) > 1: continue
                out.append((f, p["name"]))
        return out


DIRECTORY: Directory | None = None   # 이 실행에서 읽은 사람 표(못 읽었으면 None)


def load(call, log=print) -> Directory | None:
    """GET /api/persons/directory를 DIRECTORY에 둔다. 실패하면(옛 서버 404·오프라인) None"""
    global DIRECTORY
    try:
        DIRECTORY = Directory(call("GET", "/api/persons/directory"))
        n = sum(len(p["formerNames"]) for p in DIRECTORY.people)
        log(f"사람 표 {len(DIRECTORY)}명 · 옛 이름 {n}개")
    except Exception as e:
        DIRECTORY = None
        log("사람 표 없음(옛 서버): 목소리 이름 그대로" if "404" in str(e) else f"사람 표를 못 읽음(목소리 이름 그대로): {e}")
    return DIRECTORY


def sync_aliases(store, directory: Directory | None = None, save=True, log=print) -> int:
    """옛 이름 → 지금 label을 목소리 저장소 별칭으로(머리말 규칙). 사람 표가 없으면 아무것도 안 함. → 새로 만든 별칭 수"""
    d = directory if directory is not None else DIRECTORY
    if d is None: return 0
    n, moved = 0, 0
    for former, name in d.former_pairs():
        cf, cn = store.canonical(former), store.canonical(name)
        if ME_NAME in (cf, cn): continue
        flip = store.aliases.get(name) == former and cf == former   # 반대 방향(지금 이름 → 옛 이름): 뒤집어 지금 이름을 사람 이름으로
        if cf == cn and not flip: continue
        if cf != former and d.id_of(cf) not in (None, d.id_of(name)):
            log(f"  옛 이름 별칭 건너뜀 {former} → {name}: 이미 {cf}의 별칭"); continue
        if flip: store.remove_alias(name)
        try:
            moved += store.set_alias(former, name)
            n += 1
        except ValueError as e:
            log(f"  옛 이름 별칭 건너뜀 {former} → {name}: {e}")
    if n:
        log(f"사람 표 옛 이름으로 목소리 별칭 {n}개(표본 {moved}개 옮김)")
        if save: store.save()
    return n


# ───────── 목소리 동명이인 신호 ─────────

def voice_check_items(store, directory: Directory | None = None) -> list:
    """voices.voice_checks에 personId(사람 표에서 label로, 모르면 뺌)를 붙인 서버 items"""
    d = directory if directory is not None else DIRECTORY
    out = []
    for it in V.voice_checks(store):
        pid = d.id_of(it["person"]) if d is not None else None
        out.append({"person": it["person"], **({"personId": pid} if pid is not None else {}),
                    **{k: v for k, v in it.items() if k != "person"}})
    return out


def push_voice_checks(call, store, dry_run=False, log=print, directory: Directory | None = None) -> dict:
    """POST /api/persons/voice-checks(통째로 갈아 끼움, 비어도 보냄). 실패는 부르는 쪽이 잡는다. → 보낸(dry-run이면 보낼) 본문"""
    body = {"items": voice_check_items(store, directory)}
    if dry_run:
        log(f"  (dry-run) 목소리 동명이인 신호 {len(body['items'])}개를 올릴 것")
        for it in body["items"][:10]: log(f"    {it['person']}: 무리 {len(it['groups'])}개 서로 {it['similarity']:.2f}")
        return body
    call("POST", "/api/persons/voice-checks", body)
    log(f"  목소리 동명이인 신호 {len(body['items'])}개 올림")
    return body


_AT_EXIT = False


def push_at_exit(call, dry_run=False, log=print):
    """실행이 끝날 때(atexit) 한 번 올린다(그 실행에서 등록된 표본까지, 저장소를 새로 읽어서). 두 번 불러도 한 번만 건다"""
    global _AT_EXIT
    if _AT_EXIT: return
    _AT_EXIT = True
    atexit.register(_push_at_exit, call, dry_run, log)


def _push_at_exit(call, dry_run=False, log=print):
    if DIRECTORY is None: return   # 사람 표를 못 읽은 서버(옛 서버·오프라인): 올릴 곳도 없다
    try:
        import speakers as sp
        push_voice_checks(call, V.Store(sp.VOICES), dry_run, log)
    except Exception as e:
        log(f"  목소리 동명이인 신호 올리기 건너뜀: {e!r}")
