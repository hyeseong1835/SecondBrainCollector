"""화자 구분(2026-11-11): sherpa-onnx로 화자 분리(pyannote segmentation 3.0 + 화자 임베딩)를 하고, '나' 기준 임베딩과 견줘 구간마다 speaker를 붙인다.

analyze.py가 쓰는 함수: load_wav, Diarizer.diarize, name_clusters, apply_voices, label_segments, Me.load, enroll_bot_tracks
CLI:
  python speakers.py enroll [--max-calls 30]   # 통화 녹음으로 '나' 기준을 만든다(me.npy)
  python speakers.py test FILE [--speakers N]   # 파일 하나를 분리해 화자별 시간·'나' 유사도·등록 목소리와 견준 이름을 보인다(저장 안 함)
사람 목소리(2026-11-16, voices.py. 이 PC에만, 서버에 올리지 않음):
  python speakers.py voices [--json] [--person 이름]   # 등록된 사람(표본 수·평균과 유사도), 서로 닮은 사람 쌍, 여러 녹음에 나온 모르는 화자
  python speakers.py forget 이름 | --sample ID | --file "call/…m4a"   # 틀린 등록 지우기
  python speakers.py enroll-calls [--max-calls 20] [--per-person 3] [--max-sec 300] [--redo] [FILE…]   # 지난 통화들로(FILE을 주면 그것만) 상대 목소리를 한 번에 등록(전사 안 함)
  python speakers.py who CLUSTER 이름   # 모르는 화자 묶음에 이름 정하기
  python speakers.py enroll-bot [--max-bundles 10] [--max-sec 300] [--redo] [--dry-run] [FILE…]   # 지난 디스코드 봇 녹음(사람별 파일)으로 목소리 등록(전사 안 함)
  python speakers.py alias 별칭 사람이름 | --remove 별칭   # 디스코드 표시 이름 ↔ 연락처 이름 잇기(그 별칭 표본을 사람 이름으로 옮김)
디스코드(2026-11-18): discord-output(내 PC로 들린 디스코드 소리, 내 목소리 없음)은 '나'를 찾지 않고 화자N + 등록 목소리 이름만(NO_ME_SOURCES).
  discord-bot 전사 때 사람별 파일로 그 사람 목소리를 자동 등록(--dry-run이면 저장 안 함). 이름 붙이기는 녹음과 같은 음질 갈래(voices.grade)의 평균을 먼저 쓴다.

'나' 등록 방식(통화 29개·상대 15명, CPU 약 10분): 삼성 통화 녹음은 모노라 채널로 가를 수 없다. 대신 통화마다 화자를 둘로 나누고,
상대가 서로 다른 통화들에 공통으로 나오는 쪽(=나)을 고른다. 상대는 통화마다 바뀌고 나는 늘 같으니
'각 통화에서 한 묶음씩 골라 서로 가장 닮게' 고르면 내 묶음들이 뽑힌다. 그 평균이 me.npy.
모델·기준: 분석 도구 폴더(sbpaths.TOOLS, 보통 %LOCALAPPDATA%/SecondBrain/analysis)/models/diar/ (pip install sherpa-onnx)
  2026-11-16: %LOCALAPPDATA%를 바로 쓰던 것을 sbpaths.TOOLS로(Claude 셸에서 받은 모델은 패키지 사본에만 있어 못 찾았다)
"""
import argparse, json, os, re, subprocess, sys, tempfile, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths
import voices as V

DIAR = sbpaths.TOOLS / "models" / "diar"
SEG_MODEL = DIAR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = DIAR / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
ME_FILE = DIAR / "me.npy"
VOICES = DIAR / "voices"   # 사람 목소리(voices.py)
ME_NAME = "나"
# 기준값(2026-11-11, 통화 12개로 맞춤): 거리 0.65로 넉넉히 잘게 묶으면 '나' 묶음은 '나'와 0.6~0.8, 상대 묶음은 0.1~0.5
ME_THRESHOLD = 0.58      # 묶음 임베딩과 '나'의 코사인 유사도가 이 이상이면 '나'
ME_FALLBACK = 0.5        # 통화에서 기준을 넘는 묶음이 없을 때, 가장 닮은 묶음이 이 이상이면 그래도 '나'
CLUSTER_THRESHOLD = 0.65 # 분리기의 묶음 거리 기준. 작을수록 화자가 많아진다(잘게 나뉜 건 name_clusters가 다시 합친다)
MERGE_THRESHOLD = 0.55   # 폰 녹음에서 '나' 아닌 묶음끼리 이 이상 닮으면 같은 화자로 합친다
CALL_ROOT = Path(os.environ["USERPROFILE"]) / "SecondBrainRecordings" / "call"
SR = 16000
NO_ME_SOURCES = ("discord-output",)   # 내 목소리가 없는 원천: name_clusters에 '나' 후보를 주지 않는다
BOT_MAX_SEC = 300        # 봇 등록: 한 사람의 이은 소리 앞 이만큼만 화자 분리(CPU로 실시간의 약 1/9)


_PROC_DIARIZER = None   # diarize_path가 쓰는 프로세스별 Diarizer


def diarize_path(path: str):
    """analyze.py 화자 분리 갈래(2026-11-18, 다른 프로세스): 16kHz wav 파일 → Diarizer.diarize (turns, embs). 프로세스마다 Diarizer 하나를 띄워 다시 쓴다.
    프로세스로 따로 도는 까닭: sherpa-onnx가 화자 분리 중 GIL을 놓지 않아 스레드로 돌리면 analyze.py의 다른 갈래(wav 만들기·전사 진행 읽기)가 멈췄다(실측)."""
    global _PROC_DIARIZER
    if _PROC_DIARIZER is None: _PROC_DIARIZER = Diarizer()
    return _PROC_DIARIZER.diarize(load_wav(Path(path)))


def load_wav(path: Path) -> np.ndarray:
    """16kHz 모노 s16 wav → float32 [-1, 1]"""
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2, "16kHz 모노 s16 wav만"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class Diarizer:
    """화자 분리기. 화자 수(num_speakers)를 알면 그만큼, 모르면 거리 기준으로 묶는다. 같은 설정의 엔진은 다시 쓴다."""
    def __init__(self):
        import sherpa_onnx
        self.so = sherpa_onnx
        self._engines = {}
        self.ext = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=4))

    def _engine(self, n):
        if n not in self._engines:
            so = self.so
            cfg = so.OfflineSpeakerDiarizationConfig(
                segmentation=so.OfflineSpeakerSegmentationModelConfig(pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)), num_threads=4),
                embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=4),
                clustering=so.FastClusteringConfig(num_clusters=n or -1, threshold=CLUSTER_THRESHOLD),
                min_duration_on=0.3, min_duration_off=0.5)
            if not cfg.validate():
                raise RuntimeError("화자 분리 설정 오류(모델 경로 확인): " + str(DIAR))
            self._engines[n] = so.OfflineSpeakerDiarization(cfg)
        return self._engines[n]

    def embed(self, samples: np.ndarray) -> np.ndarray:
        s = self.ext.create_stream()
        s.accept_waveform(SR, samples)
        s.input_finished()
        return _unit(self.ext.compute(s))

    def diarize(self, samples: np.ndarray, num_speakers: int | None = None):
        """→ (turns, embs). turns: [(시작ms, 끝ms, 묶음번호)], embs: {묶음번호: 단위 임베딩}(묶음의 말을 이어 붙여 다시 뽑음)"""
        if len(samples) < SR:   # 1초 미만
            return [], {}
        res = self._engine(num_speakers).process(samples).sort_by_start_time()
        turns = [(int(r.start * 1000), int(r.end * 1000), int(r.speaker)) for r in res]
        embs = {}
        for k in sorted({t[2] for t in turns}):
            parts = [samples[s * SR // 1000:e * SR // 1000] for s, e, kk in turns if kk == k]
            audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
            if len(audio) >= SR // 2:   # 0.5초 이상 말한 묶음만
                embs[k] = self.embed(audio[:SR * 120])   # 2분이면 충분
        return turns, embs


class Me:
    def __init__(self, vec, threshold=ME_THRESHOLD):
        self.vec, self.threshold = _unit(vec), threshold

    @classmethod
    def load(cls):
        return cls(np.load(ME_FILE)) if ME_FILE.exists() else None

    def score(self, emb) -> float:
        return float(np.dot(self.vec, emb))


def name_clusters(embs: dict, me: "Me | None", turns: list, partner: str | None = None) -> dict:
    """묶음번호 → 이름.
    - '나'와 ME_THRESHOLD 이상 닮은 묶음은 모두 '나'(분리기가 내 목소리를 여러 묶음으로 나눌 수 있다)
    - 통화(partner 있음): 기준을 넘는 묶음이 없으면 가장 닮은 묶음이 ME_FALLBACK 이상일 때 '나'. 나머지는 모두 상대 이름
    - 폰 녹음: 나머지끼리 MERGE_THRESHOLD 이상 닮으면 합치고, 많이 말한 순서로 화자1·화자2…"""
    dur = {k: sum(b - a for a, b, kk in turns if kk == k) for k in embs}
    score = {k: me.score(e) for k, e in embs.items()} if me else {}
    names = {k: ME_NAME for k in embs if score.get(k, 0) >= ME_THRESHOLD}
    if partner and not names and score:
        best = max(score, key=score.get)
        if score[best] >= ME_FALLBACK:
            names[best] = ME_NAME
    rest = sorted((k for k in embs if k not in names), key=lambda k: -dur[k])
    if partner:
        names.update({k: partner for k in rest})
        return names
    groups = []   # [(대표 임베딩, 이름)]
    for k in rest:
        hit = next((g for g in groups if float(np.dot(g[0], embs[k])) >= MERGE_THRESHOLD), None)
        if hit is None:
            hit = (embs[k], f"화자{len(groups) + 1}")
            groups.append(hit)
        names[k] = hit[1]
    return names


def apply_voices(store: "V.Store", source: str, file: str, at: str | None, turns: list, embs: dict, names: dict,
                 me: "Me | None", partner: str | None = None, save=True):
    """사람 목소리(voices.py). 통화면 상대 목소리를 등록하고, 대면 녹음이면 화자N에 등록된 이름을 붙이고 못 붙인 화자는 모르는 화자로 쌓는다.
    save=False면 저장소를 바꾸지 않는다(dry-run·test). → (names, 추정인 이름 set, 한 줄 메시지)"""
    if partner:
        s, msg = V.enroll_call(store, partner, file, at, embs, turns, names)
        if s and save: store.save()
        return names, set(), ("" if s else "목소리 등록 안 함: ") + msg
    people = store.people(grade=V.grade(source))   # 같은 음질 갈래 평균 먼저, 없으면 전체 평균(추정까지만)
    names, guessed, unnamed = V.name_groups(embs, turns, names, people)
    added = V.add_unknown(store, file, at, source, unnamed) if save else []
    if save: store.save()
    named = sorted({n for n in names.values() if n not in (ME_NAME,) and not n.startswith("화자")})
    msg = ", ".join(n + ("(추정)" if n in guessed else "") for n in named) or ("이름 붙인 화자 없음" if people else "등록된 목소리 없음")
    return names, guessed, msg + (f", 모르는 화자 {len(added)}명 쌓음" if added else "")


def enroll_bot_tracks(store: "V.Store", d: "Diarizer", me: "Me | None", tracks: list, save=True, max_sec=None):
    """디스코드 봇 묶음의 사람별 소리로 목소리 등록. tracks = [(표시 이름, file, at, float32 소리)].
    소리는 앞 max_sec(기본 BOT_MAX_SEC)초만 화자 분리한다. save면 하나라도 등록됐을 때 저장. → (등록 수, 사람별 메시지)"""
    n, msgs = 0, []
    for display, file, at, x in tracks:
        turns, embs = d.diarize(np.asarray(x, np.float32)[:SR * (max_sec or BOT_MAX_SEC)])
        got, msg = V.enroll_bot(store, display, file, at, embs, turns, me)
        n += got is not None
        msgs.append(msg if got else "등록 안 함: " + msg)
    if n and save: store.save()
    return n, msgs


def label_segments(segs: list, turns: list, names: dict, guessed: set = frozenset()) -> None:
    """전사 구간(startMs·endMs)마다 가장 많이 겹치는 화자 이름을 speaker에 적는다(제자리). 겹침이 없으면 None.
    그 이름이 guessed(등록 목소리와 덜 닮아 추정)에 있으면 speakerGuess = True(서버 구간 표에는 아직 담을 열이 없다)."""
    for s in segs:
        overlap = {}
        for a, b, k in turns:
            if k not in names: continue   # 너무 짧아 임베딩이 없는 묶음
            o = min(s["endMs"], b) - max(s["startMs"], a)
            if o > 0:
                overlap[names[k]] = overlap.get(names[k], 0) + o   # 이름별로 더한다('나'가 여러 묶음일 수 있다)
        s["speaker"] = max(overlap, key=overlap.get) if overlap else None
        if s["speaker"] in guessed: s["speakerGuess"] = True
        else: s.pop("speakerGuess", None)


def group_words(words: list, gap_ms=1200, max_ms=30000) -> list:
    """단어 구간(whisper -ml 1 -sow)을 이어 붙여 문장 구간으로. 화자가 바뀌거나, 틈이 gap_ms 이상이거나, max_ms를 넘으면 끊는다.
    화자 없는 단어(겹치는 화자 없음)는 앞 구간에 붙인다. 단어 하나라도 suspect(전사 의심)면 묶은 구간도 suspect.
    단어 하나라도 original(단어장 고침 짝으로 바꾸기 전 글)이 있으면 묶은 구간의 original = 단어들의 바꾸기 전 글을 이은 것."""
    out = []
    for w in words:
        cur = out[-1] if out else None
        same = cur and (w["speaker"] is None or w["speaker"] == cur["speaker"])
        if same and w["startMs"] - cur["endMs"] < gap_ms and w["endMs"] - cur["startMs"] <= max_ms:
            if "original" in cur or "original" in w:
                cur["original"] = cur.get("original", cur["text"]) + " " + w.get("original", w["text"])
            cur["endMs"] = max(cur["endMs"], w["endMs"]); cur["text"] += " " + w["text"]
            if w.get("suspect"): cur["suspect"] = True
        else:
            out.append(dict(w))
    return out


# ---- '나' 등록 ----

def call_partner(path: Path) -> str:
    m = re.match(r"^(?:통화\s+)?(.+)_\d{6}_\d{6}$", path.stem)
    return m.group(1) if m else path.stem


def _to_wav(ffmpeg, src: Path, dst: Path, max_sec: int | None = None):
    """max_sec를 주면 앞부분만(긴 통화를 통째로 읽으면 메모리가 모자랄 수 있다)"""
    cut = ["-t", str(max_sec)] if max_sec else []
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), *cut, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)


def pick_common(cands: list[list[np.ndarray]], iters=10):
    """통화마다 후보 임베딩(보통 2개) 중 하나씩 골라, 고른 것끼리 가장 닮게 한다. → (고른 번호들, 평균)"""
    # 시작점: 첫 통화의 후보 각각에서 출발해 반복 개선하고, 응집도가 높은 쪽을 쓴다
    best = None
    for start in range(len(cands[0])):
        center = cands[0][start]
        for _ in range(iters):
            pick = [int(np.argmax([np.dot(center, e) for e in c])) for c in cands]
            center = _unit(np.mean([c[p] for c, p in zip(cands, pick)], axis=0))
        coh = float(np.mean([np.dot(center, c[p]) for c, p in zip(cands, pick)]))
        if best is None or coh > best[0]:
            best = (coh, pick, center)
    return best[1], best[2], best[0]


def enroll(max_calls=30, min_sec=30, max_sec=180):
    """여러 상대와의 통화로 '나' 기준을 만든다. 한 상대당 최대 3통화, 길이 min_sec 이상, 앞 max_sec초만(분리는 CPU로 실시간의 약 1/9)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from analyze import FFMPEG
    files = sorted(CALL_ROOT.rglob("*.m4a"), key=lambda p: p.stat().st_size, reverse=True)
    per, chosen = {}, []
    for f in files:   # 큰 파일(긴 통화)부터, 상대별로 고르게
        who = call_partner(f)
        if per.get(who, 0) >= 3: continue
        per[who] = per.get(who, 0) + 1
        chosen.append(f)
        if len(chosen) >= max_calls: break
    d = Diarizer()
    cands, used = [], []
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "c.wav"
        for i, f in enumerate(chosen, 1):
            _to_wav(FFMPEG, f, wav)
            x = load_wav(wav)
            if len(x) < SR * min_sec: continue
            _, embs = d.diarize(x[:SR * max_sec], num_speakers=2)
            print(f"  [{i}/{len(chosen)}] {f.name[:40]}  묶음 {len(embs)}", flush=True)
            if len(embs) == 2:
                cands.append([embs[k] for k in sorted(embs)]); used.append(f)
    if len(cands) < 5:
        sys.exit(f"쓸 만한 통화가 {len(cands)}개뿐이다. 1~2분 혼잣말 녹음으로 등록이 필요하다")
    pick, center, coh = pick_common(cands)
    # 덜 닮은 통화(분리 실패 등)는 빼고 다시 평균
    sims = np.array([np.dot(center, c[p]) for c, p in zip(cands, pick)])
    keep = sims >= np.percentile(sims, 20)
    center = _unit(np.mean([c[p] for c, p, k in zip(cands, pick, keep) if k], axis=0))
    other = [float(np.dot(center, c[1 - p])) for c, p in zip(cands, pick)]
    mine = [float(np.dot(center, c[p])) for c, p in zip(cands, pick)]
    np.save(ME_FILE, center)
    print(f"통화 {len(cands)}개(상대 {len({call_partner(f) for f in used})}명)로 '나' 등록 → {ME_FILE}")
    print(f"  고른 쪽(나) 유사도 평균 {np.mean(mine):.2f} (최소 {np.min(mine):.2f}) · 다른 쪽(상대) 평균 {np.mean(other):.2f} (최대 {np.max(other):.2f})")
    return center


def _rel(path: Path) -> str:
    """녹음 파일 → analyze.py의 file 이름("call/<날짜>/…", "laptop-mic/…", 폰은 "<날짜>/…")"""
    root = CALL_ROOT.parent
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
    return rel[len("phone/"):] if rel.startswith("phone/") else rel


def test(path: Path, num_speakers=None):
    from analyze import FFMPEG, started_from_name
    d, me, store = Diarizer(), Me.load(), V.Store(VOICES)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "t.wav"
        _to_wav(FFMPEG, path, wav)
        turns, embs = d.diarize(load_wav(wav), num_speakers)
    partner = call_partner(path) if path.parent.parent == CALL_ROOT else None
    source = "call" if partner else path.parent.parent.name
    names = name_clusters(embs, None if source in NO_ME_SOURCES else me, turns, partner)
    names, guessed, msg = apply_voices(store, source, _rel(path), started_from_name(path).isoformat(), turns, embs, names, me, partner, save=False)
    people = store.people()
    for k, e in embs.items():
        sec = sum(b - a for a, b, kk in turns if kk == k) / 1000
        near = sorted(((float(m @ e), n) for n, (m, _) in people.items()), reverse=True)[:2]
        print(f"  묶음 {k:2} → {names[k] + ('(추정)' if names[k] in guessed else ''):12}  말 {sec:6.1f}초  나와 유사도 {me.score(e) if me else float('nan'):.2f}"
              + ("  닮은 사람 " + ", ".join(f"{n} {x:.2f}" for x, n in near) if near else ""))
    print("  목소리:", msg, "(저장 안 함)")


def enroll_calls(max_calls=20, per_person=3, max_sec=300, min_sec=20, redo=False, files=None):
    """지난 통화들로 상대 목소리를 한 번에 등록한다(화자 분리만, 전사 안 함). 이미 등록한 통화는 건너뛴다(--redo면 다시).
    긴 통화부터 상대마다 per_person개까지, 앞 max_sec초만(CPU로 실시간의 약 1/9). 번호뿐인 상대는 뺀다. files를 주면 그 통화들만."""
    from analyze import FFMPEG, started_from_name
    store, me = V.Store(VOICES), Me.load()
    if me is None:
        sys.exit("'나' 기준이 없다: python speakers.py enroll 먼저")
    done = {s.get("file") for s in store.samples if s["person"]}
    have = {}
    for s in store.samples:
        if s["person"]: have[s["person"]] = have.get(s["person"], 0) + 1
    chosen, per = [], {}
    for f in [Path(x) for x in files] if files else sorted(CALL_ROOT.rglob("*.m4a"), key=lambda p: p.stat().st_size, reverse=True):
        who, rel = call_partner(f), _rel(f)
        if V.is_number(who) or (rel in done and not redo): continue
        if per.get(who, 0) + (0 if redo else have.get(who, 0)) >= per_person: continue
        per[who] = per.get(who, 0) + 1
        chosen.append(f)
        if len(chosen) >= max_calls: break
    d, n_ok, why = Diarizer(), 0, {}
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "c.wav"
        for i, f in enumerate(chosen, 1):
            try:
                _to_wav(FFMPEG, f, wav, max_sec)
                x = load_wav(wav)
                if len(x) < SR * min_sec:
                    print(f"  [{i}/{len(chosen)}] {f.name[:40]}  짧음"); continue
                turns, embs = d.diarize(x)
            except Exception as e:   # 깨진 파일·메모리 부족은 그 통화만 건너뛴다
                print(f"  [{i}/{len(chosen)}] {f.name[:40]}  건너뜀: {e!r}"); continue
            partner = call_partner(f)
            names = name_clusters(embs, me, turns, partner)
            _, _, msg = apply_voices(store, "call", _rel(f), started_from_name(f).isoformat(), turns, embs, names, me, partner)
            ok = not msg.startswith("목소리 등록 안 함")
            n_ok += ok
            if not ok: why[msg] = why.get(msg, 0) + 1
            print(f"  [{i}/{len(chosen)}] {f.name[:40]}  → {msg}", flush=True)
            print("      묶음 " + " / ".join(f"{names[k]} {sum(b - a for a, b, kk in turns if kk == k) / 1000:.0f}초 나{me.score(e):.2f}" for k, e in embs.items()))
    print(f"통화 {len(chosen)}개 중 {n_ok}개 등록 → {VOICES}")
    show_voices()


def enroll_bot(max_bundles=10, max_sec=BOT_MAX_SEC, redo=False, files=None, dry_run=False):
    """지난 디스코드 봇 녹음으로 사람 목소리를 등록한다(화자 분리만, 전사 안 함). 묶음(bundles.DEFAULT_RULE)마다 사람별 파일을 이어
    enroll_bot_tracks. 최근 묶음부터 max_bundles개. 이미 등록한 묶음(그 사람 첫 파일이 표본에 있음)은 건너뛴다(--redo면 다시). files를 주면 그 파일이 든 묶음만."""
    import analyze as A, bundles as B
    store, me = V.Store(VOICES), Me.load()
    if me is None: print("  ('나' 기준 없음: 내 목소리도 등록될 수 있다. python speakers.py enroll)")
    done = {s.get("file") for s in store.samples if s["person"]}
    bundles = sorted(B.make_bundles(A.scan("discord-bot"), B.DEFAULT_RULE), key=lambda b: -b.start)
    if files:
        names = {Path(f).name for f in files}
        bundles = [b for b in bundles if any(Path(it.rel).name in names for it in b.items)]
    chosen = []
    for b in bundles:
        tracks = B.by_track(b.items)
        if not redo and all(b.items[ix[0]].rel in done for ix in tracks.values()): continue
        chosen.append((b, tracks))
        if len(chosen) >= max_bundles: break
    d, total, people = Diarizer(), 0, {}
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "b.wav"
        for i, (b, tracks) in enumerate(chosen, 1):
            got = []
            for ix in tracks.values():
                first, parts, n = b.items[ix[0]], [], 0
                for k in ix:
                    if n >= SR * max_sec: break
                    try:
                        _to_wav(A.FFMPEG, b.items[k].path, wav); x = load_wav(wav)
                    except Exception as e:
                        print(f"      {Path(b.items[k].rel).name} 건너뜀: {e!r}"); continue
                    parts.append(x); n += len(x)
                if parts: got.append((first.speaker, first.rel, A.iso(first.start), np.concatenate(parts)))
            n_ok, msgs = enroll_bot_tracks(store, d, me, got, save=not dry_run, max_sec=max_sec)
            total += n_ok
            for (who, *_), msg in zip(got, msgs):
                ok = not msg.startswith("등록 안 함")
                people.setdefault(who, [0, 0])[0 if ok else 1] += 1
            print(f"  [{i}/{len(chosen)}] {A.hms(b.start)} 파일 {len(b.items)}개 사람 {len(tracks)}명 → 등록 {n_ok}", flush=True)
            for m in msgs: print("      " + m)
    print(f"묶음 {len(chosen)}개에서 표본 {total}개 등록" + (" (--dry-run: 저장 안 함)" if dry_run else f" → {VOICES}"))
    for who, (ok, no) in sorted(people.items(), key=lambda t: -t[1][0]):
        print(f"  {who}: 등록 {ok} / 안 함 {no}" + (f" (별칭 → {store.canonical(who)})" if store.canonical(who) != who else ""))
    if not dry_run: show_voices()


def alias(name: str, person: str | None = None, remove=False):
    store = V.Store(VOICES)
    if remove:
        print("별칭 지움" if store.remove_alias(name) else "그런 별칭 없음")
    else:
        n = store.set_alias(name, person)
        print(f"{name} → {store.canonical(name)} (표본 {n}개 옮김)")
    store.save()


def show_voices(as_json=False, person=None):
    store = V.Store(VOICES)
    ppl, close, unk = store.summary(), store.close_people(), V.unknown_groups(store)
    if person:
        ppl = [p for p in ppl if p["person"] == person]
    if as_json:
        for p in ppl:
            if not person: p.pop("items")
        print(json.dumps({"people": ppl, "closePeople": close, "unknownGroups": unk, "aliases": store.aliases}, ensure_ascii=False, indent=1))
        return
    for p in ppl:
        print(f"  {p['person']:24} 표본 {p['samples']:3}  말 {p['sec']:7.1f}초  평균과 유사도 {p['simMean']:.2f}(최소 {p['simMin']:.2f})  최근 {p['last'][:16]}  "
              + " ".join(f"{g or '?'} {n}" for g, n in p["grades"].items()))
        if person:
            for s in p["items"]:
                print(f"      #{s['id']:<5} {s['sim']:.2f}  {s.get('sec', 0):6.1f}초  {s.get('file')}" + ("  (확인함)" if s.get("confirmed") else ""))
    if not ppl: print("  등록된 사람 없음")
    for a, b in store.aliases.items(): print(f"  별칭: {a} → {b}")
    for a, b, x in close: print(f"  닮은 사람: {a} ~ {b} {x:.2f}(같은 사람이면 하나를 지우거나 그대로 두기)")
    n_unk = sum(1 for s in store.samples if s["person"] is None)
    print(f"  모르는 화자 표본 {n_unk}개, 여러 녹음에 나온 묶음 {len(unk)}개")
    for g in unk[:10]:
        print(f"    묶음 {g['cluster']}: 녹음 {len(g['files'])}개 {g['sec']:.0f}초  {', '.join(g['files'][:3])}")


def forget(person=None, sample=None, file=None):
    store = V.Store(VOICES)
    n = store.remove_sample(sample) if sample is not None else store.remove_file(file) if file else store.remove_person(person)
    store.save()
    print(f"표본 {n}개 지움")


def who(cluster: int, person: str):
    store = V.Store(VOICES)
    n = V.confirm_unknown(store, cluster, person)
    store.save()
    print(f"묶음 {cluster}의 표본 {n}개 → {person}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll"); e.add_argument("--max-calls", type=int, default=30)
    t = sub.add_parser("test"); t.add_argument("file"); t.add_argument("--speakers", type=int)
    v = sub.add_parser("voices"); v.add_argument("--json", action="store_true"); v.add_argument("--person")
    f = sub.add_parser("forget"); g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("person", nargs="?"); g.add_argument("--sample", type=int); g.add_argument("--file")
    c = sub.add_parser("enroll-calls"); c.add_argument("--max-calls", type=int, default=20); c.add_argument("--per-person", type=int, default=3)
    c.add_argument("--max-sec", type=int, default=300); c.add_argument("--redo", action="store_true"); c.add_argument("files", nargs="*")
    w = sub.add_parser("who"); w.add_argument("cluster", type=int); w.add_argument("person")
    eb = sub.add_parser("enroll-bot"); eb.add_argument("--max-bundles", type=int, default=10); eb.add_argument("--max-sec", type=int, default=BOT_MAX_SEC)
    eb.add_argument("--redo", action="store_true"); eb.add_argument("--dry-run", action="store_true"); eb.add_argument("files", nargs="*")
    al = sub.add_parser("alias"); al.add_argument("name"); al.add_argument("person", nargs="?"); al.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    if a.cmd == "enroll": enroll(a.max_calls)
    elif a.cmd == "test": test(Path(a.file), a.speakers)
    elif a.cmd == "voices": show_voices(a.json, a.person)
    elif a.cmd == "forget": forget(a.person, a.sample, a.file)
    elif a.cmd == "enroll-calls": enroll_calls(a.max_calls, a.per_person, a.max_sec, redo=a.redo, files=a.files)
    elif a.cmd == "enroll-bot": enroll_bot(a.max_bundles, a.max_sec, a.redo, a.files, a.dry_run)
    elif a.cmd == "alias":
        if not a.remove and not a.person: ap.error("alias 별칭 사람이름 (또는 --remove 별칭)")
        alias(a.name, a.person, a.remove)
    else: who(a.cluster, a.person)
