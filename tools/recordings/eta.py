"""전사 예상 시간(2026-11-17). analyze.py가 묶음을 처리하며 남은 시간을 셈해 --progress-json 진행 줄의 "eta"로 낸다(데스크톱 녹음 화면이 그린다).

모형: 파일 하나에 걸리는 초 = a × 오디오 초 + b (a = 실시간 배율, b = 파일마다 붙는 고정 시간: python·모델 띄우기·wav 만들기 등).
- 표본 = 끝난 파일 하나(오디오 초, 걸린 초). 걸린 초 = wav 만들기 + 전사 + 그 묶음의 나머지 시간(단어장 고르기·화자 구분·올리기)을 오디오 길이 비율로 나눈 몫.
  묶음이 끝나기 전에는 그 묶음의 끝난 파일을 몫 없이 임시 표본으로 쓴다(긴 첫 묶음에서도 몇 파일 뒤부터 보이게).
- 맞추기: 최근 가중(새것부터 DECAY^순번, 이 PC 기록은 HISTORY_WEIGHT를 더 곱함) 최소 제곱, a·b ≥ 0. 오디오 길이가 고르면(퍼짐이 작으면) b = 0으로 두고 a = 걸린 초 합 / 오디오 초 합.
- 기록: %USERPROFILE%/.secondbrain/analysis/eta-history.json {"<이름>@<버전>#<설정 버전>[?실행 옵션 k=v&…]": [[오디오 초, 걸린 초, 끝난 시각], …]}.
  열쇠마다 최근 KEEP개. 묶음이 끝날 때(실패한 묶음은 빼고) 더한다. 결과를 바꾸지 않는 실행 옵션(parallel)도 빠르기를 바꾸므로 열쇠에 넣는다.
- 표본이 MIN_SAMPLES개(이 PC 기록 + 이번 실행) 모이기 전에는 남은 시간을 null("계산 중")로 낸다.
- 오디오 길이: 묶음을 정할 때 이미 읽은 m4a 길이(analyze.scan). 길이를 모르는 파일(0)은 아는 파일들의 평균으로 채우고 unknownFiles로 센다. 지금 파일은 wav를 만든 뒤 정확한 길이로 바꾼다.
- 지금 파일: 예상 = a × 길이 + b. 파이프라인이 percent를 내면 경과로 늘인 값(경과 × (100 − p) / p)과 percent 비율로 섞는다. 화자 구분 중에는 null.

진행 줄의 eta: {"remainingSec": 초|null, "fileRemainingSec": 초|null, "endsAt": ISO|null, "filesDone", "filesTotal", "audioDoneSec", "audioTotalSec",
               "unknownFiles", "rate": a|null, "perFileSec": b|null, "samples": 쓴 표본 수, "historySamples": 그중 이 PC 기록,
               "workers"?: {"now", "min", "max", "avg"}}
- 작업자 수(2026-11-18): 파이프라인이 진행 줄에 workers(동시에 돌리는 whisper-cli 수, 자동 동시 실행)를 내면 workers(n)로 알린다.
  이번 실행에서 본 지금·최소·최대·시간 가중 평균을 eta.workers로 낸다(한 번도 안 왔으면 칸 없음). 열쇠에는 실행 옵션 값(parallel=auto)이 들어가고 실제 수는 안 들어간다.
- 겹치기 실행(2026-11-18, Flow): analyze.py가 묶음 여러 개를 한 무리로 전사하고 준비·마무리를 겹치면 파일마다 시간을 못 잰다 → 무리마다 처리량 표본(Flow 머리말).
  기록 열쇠에 "flow=multi"(여러 입력 파이프라인) 또는 "flow=overlap"을 더해 예전(파일마다 따로) 기록과 섞지 않는다. fileRemainingSec = 지금 전사 중인 무리(파일들)의 남은 초.
"""
import datetime as dt, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths

HISTORY = sbpaths.ANALYSIS / "eta-history.json"
KEEP = 200            # 열쇠마다 남기는 표본 수
DECAY = 0.9           # 새것부터 표본 하나마다 무게를 이만큼 곱한다(20개 앞이면 약 0.12, 실효 표본 약 19개)
HISTORY_WEIGHT = 0.5  # 이 PC 기록(지난 실행) 표본은 이번 실행 표본보다 덜 믿는다
MIN_SAMPLES = 3       # 이만큼 모이기 전에는 "계산 중"
MIN_SPREAD = 0.15     # 오디오 길이의 (가중) 변동 계수가 이보다 작으면 b를 맞추지 않는다


def key(ref: dict, run_options: dict | None = None) -> str:
    """기록 열쇠: 이름@버전#설정 버전 + 실행 옵션(이름순 k=v)"""
    k = f"{ref['name']}@{ref['version']}#{ref['settingsVersion']}"
    if run_options:
        k += "?" + "&".join(f"{n}={json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}" for n, v in sorted(run_options.items()))
    return k


def load_history(k: str, path: Path | None = None) -> list:
    """그 열쇠의 표본 [(오디오 초, 걸린 초)], 오래된 것부터. 파일이 없거나 깨졌으면 []"""
    try:
        rows = json.loads(Path(path or HISTORY).read_text(encoding="utf-8")).get(k) or []
        return [(float(r[0]), float(r[1])) for r in rows if isinstance(r, list) and len(r) >= 2 and float(r[0]) >= 0 and float(r[1]) > 0]
    except (OSError, ValueError, AttributeError, TypeError, IndexError):
        return []


def save_history(k: str, samples: list, path: Path | None = None, now: float | None = None):
    """표본 [(오디오 초, 걸린 초)]를 그 열쇠 끝에 더하고 최근 KEEP개만 남긴다. 쓰기에 실패해도 조용히 넘긴다(전사는 그대로)"""
    if not samples: return
    path = Path(path or HISTORY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    at = round(now if now is not None else time.time())
    rows = [r for r in data.get(k) or [] if isinstance(r, list)] + [[round(a, 2), round(s, 2), at] for a, s in samples]
    data[k] = rows[-KEEP:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def fit(samples: list, n_history: int = 0) -> tuple | None:
    """samples = [(오디오 초, 걸린 초)] 오래된 것부터(앞 n_history개는 이 PC 기록). → (a, b) 또는 None(표본 부족)
    최근 가중 최소 제곱 t = a·x + b, a·b ≥ 0. 오디오 길이가 고르면 b = 0, a = Σw·t / Σw·x."""
    if len(samples) < MIN_SAMPLES: return None
    ws = []
    for i, _ in enumerate(samples):
        w = DECAY ** (len(samples) - 1 - i)
        ws.append(w * HISTORY_WEIGHT if i < n_history else w)
    W = sum(ws)
    sx = sum(w * x for w, (x, _) in zip(ws, samples)); st = sum(w * t for w, (_, t) in zip(ws, samples))
    if W <= 0 or st <= 0: return None
    mx, mt = sx / W, st / W
    vx = sum(w * (x - mx) ** 2 for w, (x, _) in zip(ws, samples)) / W
    ratio = (st / sx, 0.0) if sx > 0 else (0.0, mt)
    if mx <= 0 or vx ** 0.5 / mx < MIN_SPREAD: return ratio
    cov = sum(w * (x - mx) * (t - mt) for w, (x, t) in zip(ws, samples)) / W
    a = cov / vx
    b = mt - a * mx
    if a < 0: return 0.0, mt
    if b < 0: return ratio
    return a, b


class Eta:
    """한 실행의 남은 시간. plan = 묶음마다 파일 길이(초) 목록(모르면 0). 시각은 clock()(초).
    analyze.py가 부르는 차례: start_bundle(i) → [start_file(k) → file_audio(초) → end_file()]… → end_bundle(ok) … 그사이 snapshot(percent)"""

    def __init__(self, plan: list, k: str | None = None, history: list | None = None, clock=time.time, path: Path | None = None, save: bool = True):
        self.key, self.path, self.save, self.clock = k, path, save, clock
        self.history = list(history if history is not None else (load_history(k, path) if k else []))
        known = [x for b in plan for x in b if x > 0]
        self.fill = sum(known) / len(known) if known else 0.0
        self.unknown = sum(1 for b in plan for x in b if x <= 0)
        self.plan = [[x if x > 0 else self.fill for x in b] for b in plan]
        self.run = []            # 이번 실행에서 끝난 묶음의 표본(몫 포함)
        self.bundle = None       # 지금 묶음 번호(0부터)
        self.file = None         # 지금 파일 번호(0부터, 묶음 안)
        self.cur = []            # 지금 묶음의 끝난 파일 [(오디오 초, 걸린 초)]
        self.closed = set()      # 끝난(실패 포함) 묶음
        self.t_bundle = self.t_file = self.t_audio = None   # 묶음 시작·파일 시작·파일 전사 시작(wav 다음)
        self.w_now = self.w_at = None   # 작업자 수(지금, 알린 시각)
        self.w_min = self.w_max = None
        self.w_area = self.w_span = 0.0  # 시간 가중 평균용(수 × 초, 초)

    # ---- 진행 알림 ----
    def start_bundle(self, i: int):
        self.bundle, self.file, self.cur, self.t_bundle = i, None, [], self.clock()

    def start_file(self, k: int):
        self.file, self.t_file, self.t_audio = k, self.clock(), None

    def file_audio(self, sec: float):
        """wav를 만든 뒤 정확한 길이(초)"""
        if self.bundle is not None and self.file is not None and sec > 0:
            row = self.plan[self.bundle]
            if self.file < len(row): row[self.file] = sec
        self.t_audio = self.clock()

    def end_file(self):
        if self.bundle is None or self.file is None or self.t_file is None: return
        self.cur.append((self.plan[self.bundle][self.file], self.clock() - self.t_file))
        self.file = self.t_file = self.t_audio = None

    def end_bundle(self, ok: bool = True):
        """묶음 끝. ok면 묶음 전체 걸린 시간을 파일 표본에 나눠 이번 실행 표본·이 PC 기록에 더한다"""
        if self.bundle is None: return
        if ok and self.cur and self.t_bundle is not None:
            total = self.clock() - self.t_bundle
            extra = max(0.0, total - sum(s for _, s in self.cur))
            audio = sum(x for x, _ in self.cur)
            share = (lambda x: extra * x / audio) if audio > 0 else (lambda x: extra / len(self.cur))
            done = [(x, s + share(x)) for x, s in self.cur]
            self.run += done
            if self.save and self.key: save_history(self.key, done, self.path)
        self.closed.add(self.bundle)
        self.bundle, self.file, self.cur, self.t_bundle = None, None, [], None

    def workers(self, n: int):
        """파이프라인이 낸 지금 작업자 수"""
        now = self.clock()
        if self.w_now is not None and self.w_at is not None:
            self.w_area += self.w_now * (now - self.w_at); self.w_span += now - self.w_at
        self.w_now, self.w_at = n, now
        self.w_min = n if self.w_min is None else min(self.w_min, n)
        self.w_max = n if self.w_max is None else max(self.w_max, n)

    def workers_info(self) -> dict | None:
        if self.w_now is None: return None
        extra = self.clock() - self.w_at
        span, area = self.w_span + extra, self.w_area + self.w_now * extra
        return {"now": self.w_now, "min": self.w_min, "max": self.w_max, "avg": round(area / span, 2) if span > 0 else float(self.w_now)}

    # ---- 셈 ----
    def model(self):
        """(a, b, 쓴 표본 수, 그중 기록 수). 표본이 모자라면 a·b가 None"""
        samples = self.history + self.run + self.cur   # 지금 묶음의 끝난 파일은 몫 없이 임시로
        ab = fit(samples, len(self.history))
        return (*(ab or (None, None)), len(samples), len(self.history))

    def snapshot(self, percent: float | None = None, transcribing: bool = True, **_) -> dict:
        """지금의 eta 딕셔너리. percent = 지금 파일 전사 진행(0~100, 없으면 None), transcribing = 전사 단계인가(화자 구분·요약이면 False). 나머지 키(file·files)는 Flow만 쓴다"""
        now = self.clock()
        a, b, n, nh = self.model()
        files_total = sum(len(r) for r in self.plan)
        audio_total = sum(sum(r) for r in self.plan)
        files_done = sum(len(self.plan[i]) for i in self.closed) + len(self.cur)
        audio_done = sum(sum(self.plan[i]) for i in self.closed) + sum(x for x, _ in self.cur)
        out = {"remainingSec": None, "fileRemainingSec": None, "endsAt": None, "filesDone": files_done, "filesTotal": files_total,
               "audioDoneSec": round(audio_done, 1), "audioTotalSec": round(audio_total, 1), "unknownFiles": self.unknown,
               "rate": None if a is None else round(a, 4), "perFileSec": None if b is None else round(b, 2), "samples": n, "historySamples": nh}
        if (w := self.workers_info()) is not None: out["workers"] = w
        if a is None or (self.unknown and self.fill <= 0): return out   # 길이를 아는 파일이 하나도 없으면 셀 수 없다
        pred = lambda x: a * x + b
        rest = 0.0
        for i, row in enumerate(self.plan):
            if i in self.closed: continue
            if i == self.bundle:
                start = len(self.cur) + (1 if self.file is not None else 0)
                rest += sum(pred(x) for x in row[start:])
            else:
                rest += sum(pred(x) for x in row)
        file_rem = None
        if self.bundle is not None and self.file is not None and self.t_file is not None:
            p = pred(self.plan[self.bundle][self.file])
            elapsed = now - self.t_file
            file_rem = max(0.0, p - elapsed)
            if transcribing and percent is not None and 0 < percent <= 100 and self.t_audio is not None:
                f = percent / 100
                pace = (now - self.t_audio) * (1 - f) / f   # 이 빠르기면 남은 시간
                file_rem = f * pace + (1 - f) * max(0.0, p * (1 - f))
            if not transcribing: file_rem = None
            rest += file_rem or 0.0
        out["remainingSec"] = round(rest, 1)
        out["fileRemainingSec"] = None if file_rem is None else round(file_rem, 1)
        out["endsAt"] = dt.datetime.fromtimestamp(now + rest).astimezone().isoformat(timespec="seconds")
        return out


class Flow(Eta):
    """겹치기 실행(analyze.py run_flow, 2026-11-18)의 남은 시간. 묶음 여러 개를 한 무리로 전사하고(GPU) 앞뒤 묶음의 준비·마무리(CPU)를 겹치므로
    파일 하나에 걸린 시간을 따로 잴 수 없다. 표본 = 무리의 묶음이 모두 끝나면(마무리 차례), 앞 무리가 끝난 뒤(처음은 실행 시작) 걸린 시간을
    그 무리의 성공한 묶음 파일들에 오디오 길이 비율로 나눈 것(= 병목 단계의 처리량). 모형·기록·작업자 수는 Eta와 같고, 기록 열쇠는 analyze.py가 따로 준다.
    차례: start_group(묶음 번호들) → audio(묶음, 파일, 초)… → end_transcribe() → finish(묶음, ok)…, 그사이 snapshot(percent, file=, files=).
    지금 무리 남은 시간(fileRemainingSec) = 무리 예상 × (1 − 진행) 쪽으로, 진행은 ((file − 1) + percent/100) / files(여러 입력이면 file = files = 1)."""

    def __init__(self, plan: list, k: str | None = None, groups: list | None = None, **kw):
        super().__init__(plan, k, **kw)
        self.groups = [list(g) for g in (groups or [[i] for i in range(len(plan))])]
        self.group_of = {i: g for g, ix in enumerate(self.groups) for i in ix}
        self.ok = {}
        self.active, self.t_active = None, None
        self.t_mark = self.clock()

    def start_group(self, ix: list):
        self.active, self.t_active = list(ix), self.clock()

    def audio(self, i: int, k: int, sec: float):
        """wav를 만든 뒤 정확한 길이(초)"""
        if sec > 0 and i < len(self.plan) and k < len(self.plan[i]): self.plan[i][k] = sec

    def end_transcribe(self):
        self.active = self.t_active = None

    def finish(self, i: int, ok: bool = True):
        """묶음 끝(마무리까지). 그 무리가 다 끝났으면 표본을 더한다"""
        self.closed.add(i); self.ok[i] = ok
        g = self.groups[self.group_of[i]] if i in self.group_of else [i]
        if not all(x in self.closed for x in g): return
        now = self.clock()
        total, self.t_mark = now - self.t_mark, now
        rows = [x for j in g if self.ok.get(j) for x in self.plan[j]]
        audio = sum(rows)
        if not rows or total <= 0: return
        done = [(x, total * x / audio if audio > 0 else total / len(rows)) for x in rows]
        self.run += done
        if self.save and self.key: save_history(self.key, done, self.path)

    def snapshot(self, percent: float | None = None, transcribing: bool = True, file: int | None = None, files: int | None = None) -> dict:
        now = self.clock()
        a, b, n, nh = self.model()
        files_total = sum(len(r) for r in self.plan)
        audio_total = sum(sum(r) for r in self.plan)
        files_done = sum(len(self.plan[i]) for i in self.closed)
        audio_done = sum(sum(self.plan[i]) for i in self.closed)
        out = {"remainingSec": None, "fileRemainingSec": None, "endsAt": None, "filesDone": files_done, "filesTotal": files_total,
               "audioDoneSec": round(audio_done, 1), "audioTotalSec": round(audio_total, 1), "unknownFiles": self.unknown,
               "rate": None if a is None else round(a, 4), "perFileSec": None if b is None else round(b, 2), "samples": n, "historySamples": nh}
        if (w := self.workers_info()) is not None: out["workers"] = w
        if a is None or (self.unknown and self.fill <= 0): return out
        pred = lambda i: sum(a * x + b for x in self.plan[i])
        active = [i for i in self.active or [] if i not in self.closed]
        rest = sum(pred(i) for i in range(len(self.plan)) if i not in self.closed and i not in active)
        grem = None
        if active:
            G = sum(pred(i) for i in active)
            elapsed = now - self.t_active
            grem = max(0.0, G - elapsed)
            if transcribing and percent is not None and 0 < percent <= 100:
                f = min(1.0, ((max(1, file or 1) - 1) + percent / 100) / max(1, files or 1))
                pace = elapsed * (1 - f) / f
                grem = f * pace + (1 - f) * max(0.0, G * (1 - f))
            rest += grem
            if not transcribing: grem = None
        out["remainingSec"] = round(rest, 1)
        out["fileRemainingSec"] = None if grem is None else round(grem, 1)
        out["endsAt"] = dt.datetime.fromtimestamp(now + rest).astimezone().isoformat(timespec="seconds")
        return out


def human(sec: float | None) -> str:
    """사람이 읽는 길이: 45초, 12분, 1시간 5분. None이면 계산 중"""
    if sec is None: return "계산 중"
    s = int(round(sec))
    if s < 60: return f"{s}초"
    if s < 3600: return f"{s // 60}분"
    return f"{s // 3600}시간 {s % 3600 // 60}분"
