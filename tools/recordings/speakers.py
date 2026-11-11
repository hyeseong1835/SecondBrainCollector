"""화자 구분(2026-11-11): sherpa-onnx로 화자 분리(pyannote segmentation 3.0 + 화자 임베딩)를 하고, '나' 기준 임베딩과 견줘 구간마다 speaker를 붙인다.

analyze.py가 쓰는 함수: load_wav, Diarizer.diarize, label_segments, Me.load
CLI:
  python speakers.py enroll [--max-calls 30]   # 통화 녹음으로 '나' 기준을 만든다(me.npy)
  python speakers.py test FILE [--speakers N]   # 파일 하나를 분리해 화자별 시간·'나' 유사도를 보인다

'나' 등록 방식(통화 29개·상대 15명, CPU 약 10분): 삼성 통화 녹음은 모노라 채널로 가를 수 없다. 대신 통화마다 화자를 둘로 나누고,
상대가 서로 다른 통화들에 공통으로 나오는 쪽(=나)을 고른다. 상대는 통화마다 바뀌고 나는 늘 같으니
'각 통화에서 한 묶음씩 골라 서로 가장 닮게' 고르면 내 묶음들이 뽑힌다. 그 평균이 me.npy.
모델·기준: %LOCALAPPDATA%/SecondBrain/analysis/models/diar/ (pip install sherpa-onnx)
"""
import argparse, os, re, subprocess, sys, tempfile, wave
from pathlib import Path
import numpy as np

LOCAL = Path(os.environ["LOCALAPPDATA"])
DIAR = LOCAL / "SecondBrain" / "analysis" / "models" / "diar"
SEG_MODEL = DIAR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = DIAR / "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
ME_FILE = DIAR / "me.npy"
ME_NAME = "나"
# 기준값(2026-11-11, 통화 12개로 맞춤): 거리 0.65로 넉넉히 잘게 묶으면 '나' 묶음은 '나'와 0.6~0.8, 상대 묶음은 0.1~0.5
ME_THRESHOLD = 0.58      # 묶음 임베딩과 '나'의 코사인 유사도가 이 이상이면 '나'
ME_FALLBACK = 0.5        # 통화에서 기준을 넘는 묶음이 없을 때, 가장 닮은 묶음이 이 이상이면 그래도 '나'
CLUSTER_THRESHOLD = 0.65 # 분리기의 묶음 거리 기준. 작을수록 화자가 많아진다(잘게 나뉜 건 name_clusters가 다시 합친다)
MERGE_THRESHOLD = 0.55   # 폰 녹음에서 '나' 아닌 묶음끼리 이 이상 닮으면 같은 화자로 합친다
CALL_ROOT = Path(os.environ["USERPROFILE"]) / "SecondBrainRecordings" / "call"
SR = 16000


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


def label_segments(segs: list, turns: list, names: dict) -> None:
    """전사 구간(startMs·endMs)마다 가장 많이 겹치는 화자 이름을 speaker에 적는다(제자리). 겹침이 없으면 None."""
    for s in segs:
        overlap = {}
        for a, b, k in turns:
            if k not in names: continue   # 너무 짧아 임베딩이 없는 묶음
            o = min(s["endMs"], b) - max(s["startMs"], a)
            if o > 0:
                overlap[names[k]] = overlap.get(names[k], 0) + o   # 이름별로 더한다('나'가 여러 묶음일 수 있다)
        s["speaker"] = max(overlap, key=overlap.get) if overlap else None


def group_words(words: list, gap_ms=1200, max_ms=30000) -> list:
    """단어 구간(whisper -ml 1 -sow)을 이어 붙여 문장 구간으로. 화자가 바뀌거나, 틈이 gap_ms 이상이거나, max_ms를 넘으면 끊는다.
    화자 없는 단어(겹치는 화자 없음)는 앞 구간에 붙인다."""
    out = []
    for w in words:
        cur = out[-1] if out else None
        same = cur and (w["speaker"] is None or w["speaker"] == cur["speaker"])
        if same and w["startMs"] - cur["endMs"] < gap_ms and w["endMs"] - cur["startMs"] <= max_ms:
            cur["endMs"] = max(cur["endMs"], w["endMs"]); cur["text"] += " " + w["text"]
        else:
            out.append(dict(w))
    return out


# ---- '나' 등록 ----

def call_partner(path: Path) -> str:
    m = re.match(r"^(?:통화\s+)?(.+)_\d{6}_\d{6}$", path.stem)
    return m.group(1) if m else path.stem


def _to_wav(ffmpeg, src: Path, dst: Path):
    subprocess.run([str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src), "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(dst)], check=True)


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


def test(path: Path, num_speakers=None):
    sys.path.insert(0, str(Path(__file__).parent))
    from analyze import FFMPEG
    d, me = Diarizer(), Me.load()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "t.wav"
        _to_wav(FFMPEG, path, wav)
        turns, embs = d.diarize(load_wav(wav), num_speakers)
    names = name_clusters(embs, me, turns, call_partner(path) if path.parent.parent == CALL_ROOT else None)
    for k, e in embs.items():
        sec = sum(b - a for a, b, kk in turns if kk == k) / 1000
        print(f"  묶음 {k:2} → {names[k]:8}  말 {sec:6.1f}초  나와 유사도 {me.score(e) if me else float('nan'):.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll"); e.add_argument("--max-calls", type=int, default=30)
    t = sub.add_parser("test"); t.add_argument("file"); t.add_argument("--speakers", type=int)
    a = ap.parse_args()
    if a.cmd == "enroll": enroll(a.max_calls)
    else: test(Path(a.file), a.speakers)
