"""사람 목소리(2026-11-16, 음성 전사 개선 3-③): 통화 상대 목소리를 자동 등록하고, 대면 녹음(폰·노트북 마이크)의 화자1·화자2를 등록된 목소리와 견줘 이름을 붙인다.

numpy만 쓴다(화자 분리·임베딩은 speakers.py). 목소리 데이터는 이 PC에만 둔다. 서버에 올리지 않는다.

저장(models/diar/voices/, speakers.VOICES):
  voices.npy   표본 임베딩 행렬 (N, 192) float32. 행 i = voices.json samples[i]. 단위 벡터
  voices.json  {"version": 1, "dim": 192, "nextId": 정수, "nextCluster": 정수, "samples": [표본…]}
    표본 = {id, person(등록된 사람 이름, 모르는 화자면 null), source(나온 녹음 원천 "call"|"phone"|"laptop-mic"),
           file(녹음 파일 "call/<날짜>/통화 <상대>_….m4a" 등), at(녹음 시각 ISO), sec(그 화자가 말한 초), added(등록 시각),
           label(모르는 화자의 그 녹음 안 이름 "화자1"), cluster(모르는 화자 묶음 번호), confirmed(모르는 화자에 사람이 이름을 정함)}
  표본 하나 = 768바이트(npy) + 메타 250바이트 안팎(json). 통화 500개면 0.5MB.
  사람의 평균 = 그 사람 표본들의 평균(단위 벡터로)을 읽을 때 계산한다. 표본을 더하거나 지우면 평균이 따라 바뀐다.

규칙(기준값은 2026-11-16 통화 몇 개로 맞춤, speakers.py test·voices로 다시 볼 수 있다):
  통화 등록(call_sample): '나'로 찾은 묶음이 있어야 한다. '나' 아닌 묶음(말 MIN_TURN_SEC 이상)을 SAME 기준으로 목소리 무리로 묶어
    가장 많이 말한 무리를 상대로 본다. 둘째 무리도 MIN_ENROLL_SEC 이상 말했으면(바꿔 줌·스피커폰) 등록 안 함.
    상대 무리와 이 통화의 '나' 묶음 평균이 SEPARATE 이상 닮으면(분리가 잘 안 됨, 통화 음질 탓에 '나' 묶음이 상대 쪽에 섞임) 등록 안 함.
    상대 무리의 말이 MIN_ENROLL_SEC 미만이어도 안 함. 상대가 번호뿐(연락처에 없음)이면 등록하지 않는다.
    그 사람 표본이 CHECK_AFTER개 이상인데 평균과 CONSISTENT 미만이면(다른 사람이 받았거나 분리 실패) 등록하지 않는다.
    2026-11-16 실제 통화 3개(앞 5분): 상대 묶음은 '나'와 0.4~0.55라 묶음마다 '나'와 견주는 방식은 못 쓴다. 대신 상대 무리↔'나' 평균이
    잘 갈린 통화 0.39·0.48, 안 갈린 통화 0.69. 같은 사람 두 통화 0.70, 다른 사람 0.43~0.55(전화 음질이라 서로 꽤 닮는다).
  대면 이름 붙이기(name_groups): 화자N 묶음과 가장 닮은 사람이 SURE 이상이고 둘째와 MARGIN 이상 차이 나면 이름, GUESS 이상이면 이름 + 추정,
    그 아래면 화자N 그대로. 표본이 하나뿐인 사람은 추정까지만. 한 사람에 두 화자가 걸리면 더 닮은 쪽만.
  모르는 화자(add_unknown): 이름이 안 붙은 화자N(말 MIN_UNKNOWN_SEC 이상)을 표본으로 쌓고, 가장 닮은 묶음(UNKNOWN_SAME 이상)에 넣거나 새 묶음을 만든다.
    unknown_groups = 녹음 둘 이상에 나온 묶음("이 사람 누구?" 후보). confirm_unknown으로 이름을 정하면 그 사람 표본이 된다.
"""
import datetime as dt, json, os, re
from pathlib import Path
import numpy as np

DIM = 192
ME_NAME = "나"
MIN_TURN_SEC = 3.0       # 이보다 짧게 말한 묶음은 등록·이름 붙이기에서 뺀다(잡음·맞장구)
MIN_ENROLL_SEC = 10.0    # 통화 상대가 이만큼은 말해야 등록
SAME = 0.55              # 한 녹음 안 묶음끼리 평균 이 이상이면 같은 목소리(speakers.MERGE_THRESHOLD와 같음)
SEPARATE = 0.6           # 통화의 상대 무리와 '나' 묶음 평균이 이 이상 닮으면 분리가 애매 → 등록 안 함
CHECK_AFTER = 3          # 그 사람 표본이 이만큼 쌓인 뒤부터 새 표본을 평균과 견준다
CONSISTENT = 0.5         # 새 표본과 그 사람 평균이 이 미만이면 등록 안 함
SURE = 0.6               # 대면 녹음 화자와 사람 평균이 이 이상(+ 둘째와 MARGIN 차이)이면 이름
MARGIN = 0.08
GUESS = 0.45             # 이 이상이면 이름 + 추정
MIN_UNKNOWN_SEC = 5.0
UNKNOWN_SAME = 0.6       # 모르는 화자 표본끼리 이 이상이면 같은 묶음


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def is_number(name: str) -> bool:
    """연락처에 없어 번호만 있는 통화 상대(010…, 053… 등)"""
    return bool(re.fullmatch(r"[\d\s+\-()]+", name or ""))


class Store:
    """표본 목록 + 임베딩 행렬. save()는 npy를 먼저, json을 나중에(각각 임시 파일 → 바꿔치기). 읽을 때 행 수가 다르면 짧은 쪽에 맞춘다."""
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.samples, self.emb = [], np.zeros((0, DIM), np.float32)
        self.next_id, self.next_cluster = 1, 1
        j, n = self.folder / "voices.json", self.folder / "voices.npy"
        if j.exists():
            d = json.loads(j.read_text(encoding="utf-8"))
            self.samples = d.get("samples", [])
            self.next_id, self.next_cluster = d.get("nextId", len(self.samples) + 1), d.get("nextCluster", 1)
            self.emb = np.load(n).astype(np.float32) if n.exists() else np.zeros((0, DIM), np.float32)
            k = min(len(self.samples), len(self.emb))
            self.samples, self.emb = self.samples[:k], self.emb[:k]

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        j, n = self.folder / "voices.json", self.folder / "voices.npy"
        with open(n.with_suffix(".tmp.npy"), "wb") as f: np.save(f, self.emb.astype(np.float32))
        os.replace(n.with_suffix(".tmp.npy"), n)
        body = {"version": 1, "dim": DIM, "nextId": self.next_id, "nextCluster": self.next_cluster, "samples": self.samples}
        j.with_suffix(".tmp").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(j.with_suffix(".tmp"), j)

    # ---- 표본 ----
    def add(self, emb, **meta) -> dict:
        s = {"id": self.next_id, "person": None, **meta, "added": now_iso()}
        self.next_id += 1
        self.samples.append(s)
        self.emb = np.vstack([self.emb, unit(emb)[None, :]])
        return s

    def _keep(self, keep):
        idx = [i for i, s in enumerate(self.samples) if keep(s)]
        n = len(self.samples) - len(idx)
        self.samples, self.emb = [self.samples[i] for i in idx], self.emb[idx] if idx else np.zeros((0, DIM), np.float32)
        return n

    def remove_sample(self, sid: int) -> int:
        return self._keep(lambda s: s["id"] != sid)

    def remove_person(self, name: str) -> int:
        return self._keep(lambda s: s["person"] != name)

    def remove_file(self, file: str, person_only=None) -> int:
        """그 녹음에서 나온 표본을 지운다(다시 전사할 때 겹치지 않게). person_only=True면 등록 표본만, False면 모르는 화자만"""
        return self._keep(lambda s: not (s.get("file") == file and (person_only is None or (s["person"] is not None) == person_only)))

    def rows(self, person) -> list:
        return [i for i, s in enumerate(self.samples) if s["person"] == person]

    # ---- 사람 ----
    def people(self) -> dict:
        """이름 → (평균 단위 벡터, 표본 수)"""
        out = {}
        for name in dict.fromkeys(s["person"] for s in self.samples if s["person"]):
            r = self.rows(name)
            out[name] = (unit(self.emb[r].mean(axis=0)), len(r))
        return out

    def summary(self) -> list:
        """사람마다 표본 수·말한 초·최근 녹음·표본과 평균의 유사도(평균·최소, 낮은 표본은 틀린 등록일 수 있다)"""
        out = []
        for name, (mean, n) in self.people().items():
            r = self.rows(name)
            sims = self.emb[r] @ mean
            ss = [self.samples[i] for i in r]
            out.append({"person": name, "samples": n, "sec": round(sum(s.get("sec") or 0 for s in ss), 1),
                        "last": max((s.get("at") or "" for s in ss), default=""),
                        "simMean": round(float(sims.mean()), 3), "simMin": round(float(sims.min()), 3),
                        "items": [{**s, "sim": round(float(x), 3)} for s, x in zip(ss, sims)]})
        return sorted(out, key=lambda p: (-p["samples"], p["person"]))

    def close_people(self, threshold=SURE) -> list:
        """서로 닮은 등록 사람 쌍(같은 사람이 연락처 이름 여럿으로 등록됐을 수 있다) → [(이름1, 이름2, 유사도)]"""
        ppl = list(self.people().items())
        out = []
        for i in range(len(ppl)):
            for j in range(i + 1, len(ppl)):
                x = float(ppl[i][1][0] @ ppl[j][1][0])
                if x >= threshold: out.append((ppl[i][0], ppl[j][0], round(x, 3)))
        return sorted(out, key=lambda t: -t[2])


# ---- 통화 상대 등록 ----

def _dur(turns, k) -> float:
    return sum(b - a for a, b, kk in turns if kk == k) / 1000


def _weighted(embs: dict, turns: list, ks) -> np.ndarray:
    """묶음들의 말한 길이로 가중 평균(단위 벡터)"""
    return unit(sum(embs[k] * _dur(turns, k) for k in ks))


def voice_groups(embs: dict, keys: list, threshold=SAME) -> list:
    """한 녹음 안 묶음들을 평균 연결로 목소리 무리로 묶는다(분리기가 한 사람을 잘게 나눈 것을 다시 합침). → [[묶음번호…]]"""
    groups = [[k] for k in keys]
    while True:
        best = (threshold, None)
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                x = float(np.mean([embs[i] @ embs[j] for i in groups[a] for j in groups[b]]))
                if x >= best[0]: best = (x, (a, b))
        if best[1] is None: return groups
        a, b = best[1]
        groups[a] += groups.pop(b)


def call_sample(embs: dict, turns: list, names: dict):
    """통화 한 개의 화자 분리 결과(speakers.Diarizer.diarize + name_clusters)에서 상대 목소리 표본을 고른다.
    → ((임베딩, 말한 초), None) 또는 (None, 등록하지 않는 까닭)"""
    mine = [k for k in embs if names.get(k) == ME_NAME]
    if not mine:
        return None, "'나'를 못 찾음"
    others = [k for k in embs if names.get(k) != ME_NAME and _dur(turns, k) >= MIN_TURN_SEC]
    if not others:
        return None, "상대 말이 짧음"
    groups = sorted(voice_groups(embs, others), key=lambda g: -sum(_dur(turns, k) for k in g))
    secs = [sum(_dur(turns, k) for k in g) for g in groups]
    if len(groups) > 1 and secs[1] >= MIN_ENROLL_SEC:
        return None, f"상대 쪽 목소리가 여럿({secs[0]:.0f}초·{secs[1]:.0f}초)"
    emb, sec = _weighted(embs, turns, groups[0]), secs[0]
    x = float(emb @ _weighted(embs, turns, mine))
    if x >= SEPARATE:
        return None, f"상대와 '나'가 잘 안 갈림({x:.2f})"
    if sec < MIN_ENROLL_SEC:
        return None, f"상대 말이 짧음({sec:.0f}초)"
    return (emb, sec), None


def enroll_call(store: Store, partner: str, file: str, at: str | None, embs: dict, turns: list, names: dict):
    """통화 상대 표본을 등록한다(같은 파일의 옛 표본은 바꿔 넣음). store.save()는 부르는 쪽이. → (표본 또는 None, 메시지)"""
    if not partner or partner == ME_NAME or is_number(partner):
        return None, "상대 이름 없음(번호뿐)"
    got, why = call_sample(embs, turns, names)
    if got is None:
        return None, why
    emb, sec = got
    # 이 파일의 옛 표본은 빼고 견준다(다시 전사)
    rows = [i for i in store.rows(partner) if store.samples[i].get("file") != file]
    if len(rows) >= CHECK_AFTER:
        x = float(unit(store.emb[rows].mean(axis=0)) @ emb)
        if x < CONSISTENT:
            return None, f"{partner} 기존 목소리와 다름({x:.2f})"
    store.remove_file(file, person_only=True)
    s = store.add(emb, person=partner, source="call", file=file, at=at, sec=round(sec, 1))
    mean = unit(store.emb[store.rows(partner)].mean(axis=0))
    return s, f"{partner} 등록(표본 {len(store.rows(partner))}개, 평균과 {float(mean @ unit(emb)):.2f})"


# ---- 대면 녹음 이름 붙이기 ----

def match(people: dict, emb):
    """가장 닮은 사람 → (이름, 유사도, 추정 여부) 또는 None"""
    if not people:
        return None
    sc = sorted(((float(m @ emb), name, n) for name, (m, n) in people.items()), reverse=True)
    best, name, n = sc[0]
    second = sc[1][0] if len(sc) > 1 else -1.0
    if best >= SURE and best - second >= MARGIN and n >= 2:
        return name, best, False
    if best >= GUESS:
        return name, best, True
    return None


def group_embs(embs: dict, turns: list, names: dict) -> dict:
    """같은 이름(화자N)인 묶음들을 말한 길이로 가중 평균 → {이름: (임베딩, 말한 초, 묶음번호들)}"""
    out = {}
    for k, e in embs.items():
        nm, d = names.get(k), _dur(turns, k)
        if nm is None or nm == ME_NAME: continue
        v, sec, ks = out.get(nm, (np.zeros(DIM, np.float32), 0.0, []))
        out[nm] = (v + e * d, sec + d, ks + [k])
    return {nm: (unit(v), sec, ks) for nm, (v, sec, ks) in out.items()}


def name_groups(embs: dict, turns: list, names: dict, people: dict):
    """대면 녹음의 화자N을 등록된 목소리와 견줘 이름을 바꾼다. → (새 names, 추정인 이름 set, {화자N: (임베딩, 말한 초)} 이름 못 붙인 것)"""
    groups = group_embs(embs, turns, names)
    hits = {}   # 사람 → (유사도, 화자N, 추정)
    for g, (e, sec, _) in groups.items():
        if sec < MIN_TURN_SEC: continue
        m = match(people, e)
        if m and (m[0] not in hits or m[1] > hits[m[0]][0]):
            hits[m[0]] = (m[1], g, m[2])
    rename = {g: (p, guess) for p, (_, g, guess) in hits.items()}
    out = {k: (rename[nm][0] if nm in rename else nm) for k, nm in names.items()}
    guessed = {p for p, guess in rename.values() if guess}
    unnamed = {g: (e, sec) for g, (e, sec, _) in groups.items() if g not in rename}
    return out, guessed, unnamed


# ---- 모르는 화자 ----

def _clusters(store: Store) -> dict:
    """묶음 번호 → 행 목록(모르는 화자 표본만)"""
    out = {}
    for i, s in enumerate(store.samples):
        if s["person"] is None and s.get("cluster") is not None:
            out.setdefault(s["cluster"], []).append(i)
    return out


def add_unknown(store: Store, file: str, at: str | None, source: str, unnamed: dict) -> list:
    """이름 못 붙인 화자들(name_groups의 셋째 값)을 모르는 화자 표본으로. 같은 파일의 옛 표본은 바꿔 넣는다. → 더한 표본들"""
    store.remove_file(file, person_only=False)
    added = []
    for label, (e, sec) in unnamed.items():
        if sec < MIN_UNKNOWN_SEC: continue
        e = unit(e)
        best, cid = -1.0, None
        for c, rows in _clusters(store).items():
            if any(store.samples[i].get("file") == file for i in rows): continue   # 한 녹음 안의 다른 화자는 다른 사람
            x = float(unit(store.emb[rows].mean(axis=0)) @ e)
            if x > best: best, cid = x, c
        if best < UNKNOWN_SAME:
            cid = store.next_cluster; store.next_cluster += 1
        added.append(store.add(e, person=None, source=source, file=file, at=at, sec=round(sec, 1), label=label, cluster=cid))
    return added


def recluster(store: Store, threshold=UNKNOWN_SAME) -> int:
    """모르는 화자 묶음을 처음부터 다시(평균 연결 응집, 같은 녹음끼리는 합치지 않음). 기준을 바꿨을 때 쓴다. → 묶음 수"""
    idx = [i for i, s in enumerate(store.samples) if s["person"] is None]
    groups = [[i] for i in idx]
    while True:
        best = (threshold, None)
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                fa = {store.samples[i].get("file") for i in groups[a]}
                if fa & {store.samples[i].get("file") for i in groups[b]}: continue
                x = float(unit(store.emb[groups[a]].mean(axis=0)) @ unit(store.emb[groups[b]].mean(axis=0)))
                if x >= best[0]: best = (x, (a, b))
        if best[1] is None: break
        a, b = best[1]
        groups[a] += groups.pop(b)
    store.next_cluster = 1
    for g in groups:
        for i in g: store.samples[i]["cluster"] = store.next_cluster
        store.next_cluster += 1
    return len(groups)


def unknown_groups(store: Store, min_files=2) -> list:
    """여러 녹음에 나온 모르는 화자 묶음("이 사람 누구?" 후보), 녹음 많은 순 → [{cluster, files, sec, samples(표본 id), cohesion}]"""
    out = []
    for c, rows in _clusters(store).items():
        files = sorted({store.samples[i].get("file") for i in rows})
        if len(files) < min_files: continue
        mean = unit(store.emb[rows].mean(axis=0))
        out.append({"cluster": c, "files": files, "sec": round(sum(store.samples[i].get("sec") or 0 for i in rows), 1),
                    "samples": [store.samples[i]["id"] for i in rows], "cohesion": round(float((store.emb[rows] @ mean).mean()), 3)})
    return sorted(out, key=lambda g: (-len(g["files"]), -g["sec"]))


def confirm_unknown(store: Store, cluster: int, person: str) -> int:
    """모르는 화자 묶음에 이름을 정한다 → 그 표본들은 그 사람 표본(confirmed 표시, cluster·label은 지움). → 바꾼 수"""
    n = 0
    for s in store.samples:
        if s["person"] is None and s.get("cluster") == cluster:
            s["person"] = person; s["confirmed"] = True; s.pop("cluster", None); s.pop("label", None); n += 1
    return n
