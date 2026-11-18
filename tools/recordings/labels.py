"""화자 라벨 반영(2026-11-18): 데스크톱 녹음 화면에서 사람이 정한 화자 이름을 이 PC의 목소리 저장소(voices.py)에 표본으로 넣고, 모르는 화자 묶음 목록을 서버에 올린다.

  python labels.py              # 반영 안 된 라벨을 반영하고 모르는 화자 묶음 목록을 올린다(analyze.py와 같은 잠금, 잠겨 있으면 종료 코드 3)
  python labels.py --dry-run    # 할 일만 출력(저장소·평가 기록·서버에 쓰지 않음)
  python labels.py --push-only  # 묶음 목록만 올린다

analyze.py가 실제 실행을 시작할 때 sync(call, dry_run)를 한 번 부른다. 실패해도 전사는 그대로 간다(경고만).
dry-run이 아니면 sync가 atexit로 묶음 목록 올리기를 한 번 더 걸어 둔다(그 실행에서 쌓인 모르는 화자도 바로 올라가게, 저장소를 새로 읽어서).

서버 약속(SecondBrainApi 0049):
  GET  /api/recordings/speaker-labels?pending=true      반영 안 된 라벨. 서버는 라벨을 쓸 때 이미 구간 speaker를 고쳐 두므로 여기서 서버 구간을 고치지 않는다.
  POST /api/recordings/speaker-labels/applied {items: [{id, note}]}   반영했거나 반영하지 못한 라벨마다(note = 한 줄 까닭)
  PUT  /api/recordings/speaker-clusters {groups: [{cluster, cohesion, members: [{device, file, label, sec, source, at}]}]}   통째로 갈아 끼움
  목소리 임베딩은 서버에 올리지 않는다(묶음 번호·녹음·화자N·말한 초만).

라벨 반영(voices.py 공개 함수만 쓴다. voices.py·speakers.py는 고치지 않는다):
  cluster   지금 저장소의 그 묶음 표본(file, label)이 라벨을 쓸 때의 members와 절반 이상 겹칠 때만 confirm_unknown. 아니면(recluster로 번호가 바뀜) 건너뜀.
  speaker   (confirm, guess_right = 추정 이름 그대로, guess_wrong + 이름) 같은 녹음·같은 화자N의 모르는 화자 표본이 있으면 그 임베딩으로 이름 있는 표본을 다시 만든다
            (remove_sample → add, confirmed). 없으면(이름이 붙어 있던 화자) ranges 구간만 로컬 녹음에서 잘라 임베딩을 다시 뽑는다(전사 안 함).
  segment   구간 하나: 임베딩을 다시 뽑는다(모르는 화자 표본은 화자N 전체라 구간 하나로 바꾸지 않는다).
  말이 voices.MIN_TURN_SEC(3초) 미만이면 표본을 만들지 않는다. 같은 녹음에서 같은 사람으로 라벨이 만든 옛 표본은 바꿔 넣는다.
  guess_wrong이고 이름이 없으면 평가 기록만.
평가 기록(guess_right·guess_wrong): models/diar/label-eval.jsonl 한 줄 = {at, labelId, kind, device, file, guessed, person, sec, sim, top}
  sim = 다시 뽑은 임베딩과 추정 사람 평균의 유사도(표본 넣기 전), top = 가장 닮은 사람 둘 [[이름, 유사도]]. 나중에 voices.SURE·GUESS 기준값을 맞출 때 쓴다.
"""
import argparse, atexit, datetime as dt, json, os, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths
import voices as V

ROOT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "SecondBrainRecordings"   # analyze.ROOT
DEVICE_OF = {"phone": "phone", "call": "phone", "laptop-mic": "laptop", "discord-output": "laptop", "discord-bot": "laptop"}   # analyze.SOURCES
REL_PREFIX = {"call": "call/", "laptop-mic": "laptop-mic/"}   # analyze.REL_PREFIX
SR = 16000
MAX_EMBED_SEC = 120   # speakers.Diarizer.diarize와 같이 2분이면 충분


def _diar() -> Path:
    import speakers as sp
    return sp.DIAR


def eval_path() -> Path:
    return _diar() / "label-eval.jsonl"


def source_of(label: dict) -> str:
    """결과의 원천 폴더(label.source, 없으면 file 접두어로)"""
    if label.get("source"): return label["source"]
    f = label.get("file") or ""
    return next((s for s, p in REL_PREFIX.items() if f.startswith(p)), "phone")


def local_path(source: str, file: str) -> Path:
    return ROOT / source / file.removeprefix(REL_PREFIX.get(source, ""))


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1000)


def pieces(label: dict) -> list:
    """ranges(묶음 시작 기준 ms)를 묶음 파일별 조각으로 → [(파일 상대 경로, 파일 안 시작ms, 끝ms)]. files가 없으면 file 하나(오프셋 0)."""
    files = label.get("files") or [{"file": label["file"], "offsetMs": 0}]
    spans = []
    for m in files:
        end = _ms(m["endedAt"]) - _ms(m["startedAt"]) if m.get("startedAt") and m.get("endedAt") else None
        spans.append((m["file"], int(m.get("offsetMs") or 0), end))
    out = []
    for r in label.get("ranges") or []:
        for f, off, dur in spans:
            a, b = max(r["startMs"], off), r["endMs"] if dur is None else min(r["endMs"], off + dur)
            if b > a: out.append((f, a - off, b - off))
    return out


def _ffmpeg():
    return next((sbpaths.TOOLS / "ffmpeg").rglob("ffmpeg.exe"), None)   # analyze.FFMPEG


class AudioEmbedder:
    """ranges 구간만 로컬 녹음에서 잘라 화자 임베딩(speakers.Diarizer.embed). 전사는 하지 않는다. sherpa-onnx는 처음 쓸 때 싣는다.
    → (임베딩 또는 None, 말한 초, 못 뽑은 까닭)"""
    def __init__(self):
        self.d = None

    def __call__(self, label: dict):
        parts = pieces(label)
        sec = sum(b - a for _, a, b in parts) / 1000
        if sec < V.MIN_TURN_SEC: return None, sec, f"짧아 표본 안 만듦({sec:.1f}초)"
        src = source_of(label)
        ff = _ffmpeg()
        if not ff: return None, sec, "ffmpeg 없음"
        import speakers as sp
        if self.d is None: self.d = sp.Diarizer()
        audio = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, f in enumerate(dict.fromkeys(f for f, _, _ in parts)):
                p = local_path(src, f)
                if not p.exists(): return None, sec, f"녹음 파일 없음: {p.name}"
                wav = Path(tmp) / f"l{i}.wav"
                subprocess.run([str(ff), "-y", "-loglevel", "error", "-i", str(p), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
                x = sp.load_wav(wav)
                audio += [x[a * SR // 1000:b * SR // 1000] for ff_, a, b in parts if ff_ == f]
        x = np.concatenate(audio) if audio else np.zeros(0, np.float32)
        if len(x) < SR * V.MIN_TURN_SEC: return None, len(x) / SR, f"짧아 표본 안 만듦({len(x) / SR:.1f}초)"
        return self.d.embed(x[:SR * MAX_EMBED_SEC]), round(len(x) / SR, 1), None


def _top(people: dict, emb) -> list:
    return [[n, round(float(m @ emb), 3)] for n, (m, _) in sorted(people.items(), key=lambda kv: -float(kv[1][0] @ emb))[:2]]


class Applier:
    def __init__(self, store: "V.Store", embed=None, log=print):
        self.store, self.embed, self.log = store, embed or AudioEmbedder(), log
        self.evals = []

    def apply(self, lb: dict) -> str:
        """라벨 하나를 저장소에 반영(메모리에서만, 저장은 부르는 쪽). → note"""
        t, kind = lb.get("target"), lb.get("kind")
        if t == "cluster": return self._cluster(lb)
        if t not in ("speaker", "segment"): return f"모르는 대상: {t}"
        person = lb.get("speaker") if kind == "guess_right" else lb.get("person")
        guess = kind in ("guess_right", "guess_wrong")
        people = self.store.people() if guess else {}
        emb, sec, note = None, 0.0, None
        found = self._unknown(lb) if t == "speaker" else None
        if found is not None:
            i, s = found
            emb, sec = self.store.emb[i].copy(), float(s.get("sec") or 0)
        elif person or guess:
            emb, sec, note = self.embed(lb)
        if guess:
            g = lb.get("speaker")
            self.evals.append({"at": V.now_iso(), "labelId": lb["id"], "kind": kind, "device": lb.get("device"), "file": lb.get("file"),
                               "guessed": g, "person": person, "sec": round(sec, 1),
                               "sim": round(float(people[g][0] @ emb), 3) if emb is not None and g in people else None,
                               "top": _top(people, emb) if emb is not None else []})
        if not person:
            return "평가 기록만(이름 없음)" if guess else "이름 없음: 건너뜀"
        if emb is None:
            return note or "임베딩 없음"
        if found is not None: self.store.remove_sample(found[1]["id"])
        for old in [s for s in self.store.samples if s.get("file") == lb.get("file") and s["person"] == person and s.get("labelId")]:
            self.store.remove_sample(old["id"])   # 같은 녹음·같은 사람으로 라벨이 만든 옛 표본은 바꿔 넣는다
        s = self.store.add(emb, person=person, source=source_of(lb), file=lb.get("file"), at=lb.get("startedAt"), sec=round(sec, 1),
                           confirmed=True, labelId=lb["id"])
        how = f"표본 바꿈 #{found[1]['id']}" if found is not None else f"임베딩 다시 뽑음 {sec:.1f}초"
        return f"{how} → {person} #{s['id']}"

    def _unknown(self, lb: dict):
        for i, s in enumerate(self.store.samples):
            if s["person"] is None and s.get("file") == lb.get("file") and s.get("label") == lb.get("speaker"):
                return i, s
        return None

    def _cluster(self, lb: dict) -> str:
        c, person = lb.get("cluster"), (lb.get("person") or "").strip()
        if c is None or not person: return "묶음·이름 없음: 건너뜀"
        now = {(s.get("file"), s.get("label")) for s in self.store.samples if s["person"] is None and s.get("cluster") == c}
        was = {(m.get("file"), m.get("label")) for m in lb.get("members") or []}
        if not now: return f"묶음 {c} 없음: 건너뜀"
        if was and len(now & was) * 2 < len(was):
            return f"묶음이 바뀜({len(now & was)}/{len(was)} 겹침): 건너뜀"
        n = V.confirm_unknown(self.store, c, person)
        return f"묶음 {c} 표본 {n}개 → {person}"


def cluster_body(store: "V.Store") -> dict:
    """모르는 화자 묶음 목록(서버 PUT 본문). 임베딩은 넣지 않는다."""
    by = {s["id"]: s for s in store.samples}
    groups = []
    for g in V.unknown_groups(store):
        ms = [by[i] for i in g["samples"] if i in by]
        groups.append({"cluster": g["cluster"], "cohesion": g["cohesion"],
                       "members": [{"device": DEVICE_OF.get(s.get("source"), "phone"), "file": s.get("file"), "label": s.get("label"),
                                    "sec": s.get("sec") or 0, "source": s.get("source"), "at": s.get("at")} for s in ms]})
    return {"groups": groups}


def push_clusters(call, store: "V.Store", dry_run=False, log=print):
    body = cluster_body(store)
    n = sum(len(g["members"]) for g in body["groups"])
    if dry_run:
        log(f"  (dry-run) 모르는 화자 묶음 {len(body['groups'])}개(표본 {n}개)를 올릴 것")
        for g in body["groups"][:10]:
            log(f"    묶음 {g['cluster']}: " + ", ".join(f"{m['file']} {m['label']}" for m in g["members"][:3]))
        return body
    call("PUT", "/api/recordings/speaker-clusters", body)
    log(f"  모르는 화자 묶음 {len(body['groups'])}개 올림")
    return body


_AT_EXIT = False


def sync(call, dry_run=False, log=print, store=None, embed=None, at_exit=True):
    """반영 안 된 라벨을 반영하고 묶음 목록을 올린다. 예외는 잡아 경고만(전사를 막지 않는다). → 반영한 라벨 [(id, note)]"""
    global _AT_EXIT
    done = []
    try:
        import speakers as sp
        store = store if store is not None else V.Store(sp.VOICES)
        labels = sorted(call("GET", "/api/recordings/speaker-labels?pending=true") or [], key=lambda x: x["id"])
        ap = Applier(store, embed, log)
        for lb in labels:
            try: note = ap.apply(lb)
            except Exception as e: note = f"반영 실패: {e!r}"[:300]
            done.append((lb["id"], note))
            log(f"  화자 라벨 #{lb['id']} {lb.get('target')} {lb.get('speaker') or lb.get('cluster')} → {lb.get('person') or '-'}: {note}" + (" (dry-run)" if dry_run else ""))
        if dry_run:
            if ap.evals: log(f"  (dry-run) 평가 기록 {len(ap.evals)}줄을 남길 것")
        else:
            if done: store.save()
            if ap.evals:
                p = eval_path(); p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    for e in ap.evals: f.write(json.dumps(e, ensure_ascii=False) + "\n")
            if done: call("POST", "/api/recordings/speaker-labels/applied", {"items": [{"id": i, "note": n} for i, n in done]})
        push_clusters(call, store, dry_run, log)
        if at_exit and not dry_run and not _AT_EXIT:
            _AT_EXIT = True
            atexit.register(_push_at_exit, call, log)
    except Exception as e:
        log(f"  화자 라벨 반영 건너뜀: {e!r}")
    return done


def _push_at_exit(call, log):
    try:
        import speakers as sp
        push_clusters(call, V.Store(sp.VOICES), False, log)
    except Exception as e:
        log(f"  모르는 화자 묶음 올리기 건너뜀: {e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="할 일만 출력(저장소·평가 기록·서버에 쓰지 않음)")
    ap.add_argument("--push-only", action="store_true", help="모르는 화자 묶음 목록만 올린다")
    a = ap.parse_args()
    try: sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except (AttributeError, ValueError): pass
    import runlock
    from analyze import api
    if not a.dry_run:
        busy = runlock.acquire(["labels.py", *sys.argv[1:]])
        if busy:
            print(f"이미 analyze.py가 돌고 있습니다(PID {busy.get('pid')}). 끝난 뒤 다시 실행하세요."); sys.exit(3)
    try:
        call = api()
        if a.push_only:
            import speakers as sp
            push_clusters(call, V.Store(sp.VOICES), a.dry_run)
        else:
            sync(call, a.dry_run, at_exit=False)
    finally:
        if not a.dry_run: runlock.release()


if __name__ == "__main__":
    main()
