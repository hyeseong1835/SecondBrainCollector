"""whisper-cli 동시 실행: 공유 대기열 작업자 풀과 자동 조정기(2026-11-18 KST). whisper-cpp@2·whisper-lite@1에 같은 파일을 둔다(파이프라인 폴더는 따로 넣고 빼므로).
결과를 바꾸지 않는다: 조각·whisper-cli 인자가 같고 조각마다 따로 전사하므로 어느 작업자가 어느 묶음으로 돌리든 조각마다 출력이 같다.

실행 옵션(매니페스트 runOptions, 설정 버전에 들어가지 않음):
- parallel: "auto"(기본) 또는 1 이상의 정수(고정). 고정이면 처음에 조각을 작업자 수로 나눈 크기(최대 BATCH)씩 가져간다(예전 번갈아 나누기보다 실행 횟수가 많지 않다).
- parallelMax: 자동일 때 상한(0 = 자원으로 계산: VRAM 여유 ÷ 프로세스당 VRAM, CPU 전용이면 논리 코어 ÷ 스레드, 최대 HARD_MAX).
- yield: 자동일 때 다른 프로그램이 GPU를 YIELD_GPU% 넘게 쓰면 하나씩 줄이고 늘리지 않는다(기본 켬).

풀(Pool): 조각 이름 대기열을 작업자 스레드가 묶음으로 가져가 launch(작업자, 묶음, started)로 whisper-cli 하나를 돌린다.
- 자동이면 묶음 크기 = 남은 조각 ÷ (2 × 목표 작업자 수), MIN_BATCH~BATCH(guided: 뒤로 갈수록 작아져 끝이 고르고, 늘린 작업자가 가져갈 몫이 남는다).
  whisper-cli를 띄우는 데 모델 올리기로 약 2초가 들어(2026-11-18 실측, 조각 하나는 1~2초) 묶음을 너무 잘게 나누지 않는다.
- 줄이기: 보통은 묶음이 끝난 작업자가 그만둔다. 급할 때(VRAM·메모리 부족)는 가장 늦게 띄운 whisper-cli를 죽이고 못 끝낸 조각을 대기열 앞에 되돌린다.
- 실패: 고정이면 예전처럼 나머지를 죽이고 "whisper 실패". 자동이면 다른 작업자가 남아 있을 때 MAX_RECOVER번까지는 그 묶음의 못 끝낸 조각을 되돌리고
  상한을 (지금 수 - 1)로 낮춘다(VRAM 부족으로 못 뜬 경우). 작업자가 하나뿐이거나 횟수를 넘기면 실패.
- 끝난 조각 = done(이름)(.json이 있고 읽힘). 묶음을 띄우기 전에 그 조각들의 .json을 지운다(남은 옛 파일을 끝난 것으로 세지 않게).

조정기(Tuner, 순수 함수에 가까움: 가짜 측정값으로 시험): TICK초마다
1) 메모리 여유 < MEM_MIN, 커밋 여유 < COMMIT_MIN, VRAM 여유 < VRAM_MIN → 하나 줄임(급함: whisper-cli를 죽이고 조각을 되돌림).
   yield이고 다른 프로그램 GPU ≥ YIELD_GPU → 하나 줄임(묶음 끝에), 늘리지 않음. 다른 프로그램이 GPU를 YIELD_GPU/3 넘게 쓰는 동안 잰 처리량은 표에 넣지 않는다.
2) 처리량 = 그 사이 끝난 조각의 오디오 초 ÷ 걸린 초. 작업자 수가 그 사이 그대로이고(바꾼 뒤 WARMUP초는 뺌) 대기열이 비지 않았던 창만 잰다(끝물 제외).
   수마다 처리량 표(지수 평균)에 더한다. 표는 파일 사이·실행 사이에 남긴다(STATE, 파이프라인·설정·장치별): 파일 하나가 짧아 그 안에서만 오르면 늘 처음부터라서.
3) 언덕 오르기: 한 칸 아래(REPROBE_SEC 안에 잰 값)가 지금과 같거나 빠르면 줄임(늘려도 안 빨라졌으면 적은 쪽: VRAM·전력).
   늘리기는 모두 맞을 때: 상한 미만, VRAM 여유 ≥ 프로세스당 VRAM(잰 값, 없으면 PER_PROC_VRAM) + VRAM_MARGIN, GPU 사용률 < GPU_SAT, CPU < CPU_SAT,
   커밋 여유 ≥ COMMIT_PER_PROC + COMMIT_MIN, 메모리 여유 ≥ MEM_GROW, 지난 한 칸이 GAIN 넘게 빨라졌고(평평한 곳에서 떠오르지 않게),
   한 칸 위를 모르거나 오래됐거나 GAIN 넘게 빨랐다. 아니면 그대로.
   처리량을 이번 실행에서 지금 수로 한 번도 못 쟀으면(바꾼 직후) 처리량으로는 움직이지 않는다. 상한 = min(HARD_MAX, parallelMax, CPU 전용 상한,
   실패로 낮춘 상한, 지금 수 + (VRAM 여유 - VRAM_MARGIN) ÷ 프로세스당 VRAM). 지금 수가 상한을 넘으면 상한으로.
4) 측정(Sampler, Windows PDH 성능 카운터·DXGI·GlobalMemoryStatusEx, ctypes만): GPU 엔진 사용률(우리 whisper-cli와 다른 프로세스 따로),
   어댑터 전용 메모리 사용량, 우리 프로세스 전용 메모리, CPU %. 실패한 값은 None → 처리량만으로 조정.
   GPU 엔진 카운터를 붙이는 데 약 1.2초가 들어(2026-11-18 실측) 뒤에서 띄운다(LazySampler: 준비 전 측정은 빈 값).
   조각이 MIN_BATCH 이하인 파일은 작업자가 하나뿐이라 조정기·측정기를 만들지 않는다(짧은 파일마다 붙는 시간을 없앰).
시작 수 = 표에서 가장 빠른 수(없으면 START), 상한 안에서.

stats(Pool.stats): {"mode", "start", "min", "max", "avg"(시간 가중), "final", "changes": [[초, 수, 이유]…], "rates": {수: 오디오 초/초}, "recovered", "batches"}
"""
import ctypes, hashlib, json, math, os, sys, threading, time
from collections import deque
from pathlib import Path

BATCH = 40          # whisper-cli 한 번에 넘기는 조각 수 상한(명령줄 길이 제한)
MIN_BATCH = 10      # 자동일 때 묶음 하한(띄우는 비용 약 2초를 묶음 시간의 15% 안으로)
START = 2           # 표가 없을 때 처음 작업자 수
HARD_MAX = 8        # 자동 상한의 상한
TICK = 15.0         # 조정 주기(초)
WARMUP = 6.0        # 작업자 수를 바꾼 뒤 처리량에서 빼는 시간(새 whisper-cli 모델 올리기)
MIN_WINDOW = 8.0    # 처리량 창 최소 길이(초)
GAIN = 0.05         # 이만큼(5%) 넘게 빨라야 더 낫다고 본다
ALPHA = 0.4         # 처리량 표 지수 평균 무게(새 값)
REPROBE_SEC = 1800  # 이보다 오래된 표 값은 다시 재 본다
GB = 1024 ** 3
PER_PROC_VRAM = 1.5 * GB   # 프로세스당 VRAM 추정(못 쟀을 때)
VRAM_MARGIN = 0.5 * GB     # 늘릴 때 남길 여유
VRAM_MIN = 0.3 * GB        # 이보다 적게 남으면 급히 줄임
MEM_MIN = 0.5 * GB         # 시스템 메모리 여유가 이보다 적으면 급히 줄임
MEM_GROW = 1.0 * GB        # 늘리려면 이만큼 여유(whisper-cli 하나의 상주 메모리는 약 200MB)
COMMIT_PER_PROC = 2.0 * GB # whisper-cli 하나가 잡는 커밋(2026-11-18 실측: 개인 메모리 약 1.75GB, 실제 상주는 약 200MB)
COMMIT_MIN = 0.5 * GB      # 커밋 여유(페이지 파일 포함)가 이보다 적으면 급히 줄임
GPU_SAT = 97.0             # GPU 사용률이 이 이상이면 늘리지 않음
CPU_SAT = 90.0             # CPU 사용률이 이 이상이면 늘리지 않음
YIELD_GPU = 30.0           # yield: 다른 프로그램 GPU 사용률이 이 이상이면 줄임
MAX_RECOVER = 2            # 자동일 때 whisper-cli 실패를 되돌려 이어 가는 횟수
MAX_CHANGES = 60           # stats.changes에 남기는 수


def state_path() -> Path:
    home = Path(os.environ["SECONDBRAIN_DATA_DIR"]) if os.environ.get("SECONDBRAIN_DATA_DIR") else Path.home() / ".secondbrain"
    return home / "analysis" / "autoparallel.json"


def parse(run: dict | None, cpu_only: bool = False) -> dict:
    """실행 옵션 → {"auto": bool, "n": 고정 수 또는 None, "max": 사용자 상한(0 = 자동), "yield": bool}.
    run이 없으면(직접 실행) 고정 1. CPU 전용 설정에서 고정 수는 늘 1(예전과 같음: 프로세스마다 -t 스레드가 같은 CPU를 다툰다)"""
    run = run or {}
    p = run.get("parallel", 1 if not run else "auto")
    mx = run.get("parallelMax") or 0
    mx = int(mx) if isinstance(mx, (int, float)) and not isinstance(mx, bool) and mx >= 1 else 0
    yld = run.get("yield", True) is not False
    if isinstance(p, str) and p.strip().lower() == "auto":
        return {"auto": True, "n": None, "max": mx, "yield": yld}
    try:
        n = max(1, int(p))
    except (TypeError, ValueError):
        n = 1
    return {"auto": False, "n": 1 if cpu_only else n, "max": mx, "yield": yld}


def state_key(pipeline: str, settings: dict, words: bool) -> str:
    """처리량 표 열쇠: 파이프라인@버전 + 설정 내용(정렬 JSON의 sha1 앞 12자) + 단어 단위 여부"""
    h = hashlib.sha1(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"{pipeline}#{h}{'+words' if words else ''}"


def load_table(key: str, path: Path | None = None) -> dict:
    """{수: [처리량, 표본 수, 시각]}. 파일이 없거나 깨졌으면 {}"""
    try:
        raw = json.loads(Path(path or state_path()).read_text(encoding="utf-8")).get(key) or {}
        out = {}
        for k, v in (raw.get("rates") or {}).items():
            if int(k) >= 1 and float(v[0]) > 0: out[int(k)] = [float(v[0]), int(v[1]), float(v[2])]
        return out
    except (OSError, ValueError, AttributeError, TypeError, IndexError):
        return {}


def save_table(key: str, table: dict, path: Path | None = None):
    """표를 그 열쇠에 쓴다(다른 열쇠는 그대로, 임시 파일 → 바꿔치기). 실패해도 조용히(전사는 그대로)"""
    path = Path(path or state_path())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict): data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = {"rates": {str(n): [round(r, 4), c, round(t)] for n, (r, c, t) in sorted(table.items())}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


class Reading:
    """한 번의 측정. 못 잰 값은 None. 바이트·퍼센트."""
    __slots__ = ("rate", "vram_total", "vram_used", "per_proc", "gpu", "gpu_ours", "gpu_others", "cpu", "mem_free", "commit_free")

    def __init__(self, **kw):
        for k in self.__slots__: setattr(self, k, kw.get(k))

    @property
    def vram_free(self):
        return None if self.vram_total is None or self.vram_used is None else self.vram_total - self.vram_used

    def brief(self) -> str:
        parts = []
        if self.rate is not None: parts.append(f"처리량 {self.rate:.2f}")
        if self.vram_free is not None: parts.append(f"VRAM 여유 {self.vram_free / GB:.1f}GB")
        if self.gpu is not None: parts.append(f"GPU {self.gpu:.0f}%")
        if self.cpu is not None: parts.append(f"CPU {self.cpu:.0f}%")
        return ", ".join(parts)


class Tuner:
    """작업자 수 결정. table = {수: [처리량, 표본 수, 시각]}(파일 사이에 남김). decide(측정, 지금 수) → (새 수, 이유 또는 None, 급한가)"""

    def __init__(self, table: dict | None = None, user_max: int = 0, yield_gpu: bool = True, cpu_cap: int | None = None, clock=time.time):
        self.table = {int(k): list(v) for k, v in (table or {}).items()}
        self.user_max, self.yield_gpu, self.cpu_cap, self.clock = user_max, yield_gpu, cpu_cap, clock
        self.measured = set()   # 이번 실행에서 잰 수
        self.ceiling = None     # 실패로 낮춘 상한(이번 실행만)

    def cap(self, r: Reading | None = None, n: int = 0) -> int:
        """지금 상한: HARD_MAX, 사용자 상한, CPU 전용 상한, 실패로 낮춘 상한, VRAM(여유로 더 띄울 수 있는 수 + 지금 수)"""
        c = HARD_MAX
        for x in (self.user_max or None, self.cpu_cap, self.ceiling):
            if x: c = min(c, x)
        if r is not None and r.vram_free is not None:   # 이미 뜬 것은 두고 더 띄울 수 있는 만큼(급한 부족은 VRAM_MIN이 맡는다)
            per = r.per_proc or PER_PROC_VRAM
            c = min(c, max(1, n + max(0, int((r.vram_free - VRAM_MARGIN) // per))))
        return max(1, c)

    def start(self, r: Reading | None = None) -> int:
        """처음 수: 표에서 가장 빠른 수(상한 안), 없으면 START"""
        cap = self.cap(r, 0)
        known = {n: v[0] for n, v in self.table.items() if n <= cap}
        n = max(known, key=lambda k: (known[k], -k)) if known else START
        return max(1, min(n, cap))

    def lower(self, n: int):
        """whisper-cli가 실패해 되돌릴 때: 상한을 n으로"""
        self.ceiling = max(1, n if self.ceiling is None else min(self.ceiling, n))

    def add(self, n: int, rate: float):
        now = self.clock()
        old = self.table.get(n)
        self.table[n] = [rate if old is None else ALPHA * rate + (1 - ALPHA) * old[0], (old[1] if old else 0) + 1, now]
        self.measured.add(n)

    def fresh(self, n: int) -> bool:
        v = self.table.get(n)
        return v is not None and self.clock() - v[2] < REPROBE_SEC

    def decide(self, r: Reading, n: int) -> tuple:
        """→ (새 수, 이유 또는 None, 급한가). 급하면 풀이 묶음 끝을 기다리지 않고 whisper-cli를 죽여 줄인다"""
        if r.mem_free is not None and r.mem_free < MEM_MIN and n > 1:
            return n - 1, f"메모리 여유 {r.mem_free / GB:.1f}GB", True
        if r.commit_free is not None and r.commit_free < COMMIT_MIN and n > 1:
            return n - 1, f"커밋 여유 {r.commit_free / GB:.1f}GB", True
        if r.vram_free is not None and r.vram_free < VRAM_MIN and n > 1:
            return n - 1, f"VRAM 여유 {r.vram_free / GB:.1f}GB", True
        if self.yield_gpu and r.gpu_others is not None and r.gpu_others >= YIELD_GPU:
            return (n - 1, f"다른 프로그램이 GPU {r.gpu_others:.0f}% 사용", False) if n > 1 else (n, None, False)
        if r.rate is not None and not (r.gpu_others is not None and r.gpu_others >= YIELD_GPU / 3):   # 다른 프로그램과 나눠 쓴 처리량은 표에 넣지 않는다
            self.add(n, r.rate)
        cap = self.cap(r, n)
        if n > cap: return cap, f"상한 {cap}", False
        if n not in self.measured: return n, None, False
        cur = self.table[n][0]
        below = self.table.get(n - 1) if n > 1 and self.fresh(n - 1) else None
        if below and below[0] >= cur:   # 늘려도 안 빨라졌으면 적은 쪽(VRAM·전력)
            return n - 1, f"{n - 1}개와 같거나 느림({cur:.2f} ≤ {below[0]:.2f})", False
        if n + 1 > cap: return n, None, False
        if r.vram_free is not None and r.vram_free < (r.per_proc or PER_PROC_VRAM) + VRAM_MARGIN: return n, None, False
        if r.gpu is not None and r.gpu >= GPU_SAT: return n, None, False
        if r.cpu is not None and r.cpu >= CPU_SAT: return n, None, False
        if r.commit_free is not None and r.commit_free < COMMIT_PER_PROC + COMMIT_MIN: return n, None, False
        if r.mem_free is not None and r.mem_free < MEM_GROW: return n, None, False
        if below and cur <= below[0] * (1 + GAIN): return n, None, False   # 지난 한 칸이 거의 안 늘었으면 더 오르지 않는다(평평한 곳에서 떠오르지 않게)
        above = self.table.get(n + 1)
        if above is None or not self.fresh(n + 1) or above[0] > cur * (1 + GAIN):
            return n + 1, f"늘려 봄({r.brief()})", False
        return n, None, False


class Pool:
    """공유 대기열 작업자 풀. launch(작업자 번호, 묶음, started) → (종료 코드, 출력 끝). started(proc)는 띄운 직후에 부른다(죽일 수 있게).
    done(이름) → 끝났는가(.json이 있고 읽힘), reset(이름) = 그 조각의 결과 파일을 지움. weights = {이름: 오디오 ms}.
    opts = parse() 결과. tuner·sampler는 자동일 때만. on_workers(수) = 작업자 수가 바뀔 때(진행도)"""

    def __init__(self, names: list, launch, done, reset, weights: dict | None = None, opts: dict | None = None,
                 tuner: Tuner | None = None, sampler=None, clock=time.monotonic, tick: float = TICK, on_workers=None, log=None):
        self.opts = opts or {"auto": False, "n": 1, "max": 0, "yield": True}
        self.auto = bool(self.opts.get("auto"))
        self.queue = deque(names)
        self.total = len(names)
        self.launch, self.done_fn, self.reset = launch, done, reset
        self.weights = weights or {}
        self.tuner, self.sampler, self.clock, self.tick = tuner, sampler, clock, tick
        self.on_workers, self.log = on_workers, log or (lambda m: print(m, file=sys.stderr, flush=True))
        self.cond = threading.Condition()
        self.workers = {}      # 번호 → {"thread", "proc", "batch", "kill": 급히 줄이려 죽임, "retire": 묶음 뒤 그만둠}
        self.next_id = 0
        self.errors = []
        self.stop = False
        self.recovered = 0
        self.batches = 0
        self.fixed_batch = None
        self.target = 1
        self.t0 = clock()
        self.changes = []      # [(초, 수, 이유)]
        self.done_log = []     # [(시각, 오디오 ms)]
        self.seen = set()      # 끝난 것으로 센 이름
        self.queue_empty_at = None
        self.changed_at = self.t0
        self.last_tick = self.t0
        self.n_time = []       # [(시각, 수)] 시간 가중 평균용

    # ---- 작업자 ----
    def active(self) -> int:
        return sum(1 for w in self.workers.values() if not w["kill"] and not w["retire"])

    def batch_size(self) -> int:
        left = len(self.queue)
        if not self.auto:
            if self.fixed_batch is None: self.fixed_batch = max(1, min(BATCH, math.ceil(self.total / max(1, self.target))))
            return min(left, self.fixed_batch)
        return min(left, max(MIN_BATCH, min(BATCH, math.ceil(left / (2 * max(1, self.target))))))

    def spawn(self):
        """목표 수까지 작업자를 띄운다(대기열에 조각이 있을 때만). 잠금 안에서 부른다"""
        while self.active() < self.target and self.queue and not self.stop:
            w = self.next_id; self.next_id += 1
            t = threading.Thread(target=self.work, args=(w,), daemon=True)
            self.workers[w] = {"thread": t, "proc": None, "batch": [], "kill": False, "retire": False}
            t.start()

    def work(self, w: int):
        me = self.workers[w]
        try:
            while True:
                with self.cond:
                    if self.stop or me["kill"] or me["retire"] or not self.queue: break
                    if self.active() > self.target:   # 줄이기: 묶음 사이에 그만둔다
                        me["retire"] = True; break
                    batch = [self.queue.popleft() for _ in range(self.batch_size())]
                    if not self.queue and self.queue_empty_at is None: self.queue_empty_at = self.clock()
                    me["batch"] = batch
                    self.batches += 1
                for n in batch: self.reset(n)
                code, tail = self.launch(w, batch, lambda p: self.started(w, p))
                with self.cond:
                    me["proc"] = None
                    self.note_done(batch)
                    if code != 0:
                        if self.stop: break
                        if me["kill"]:
                            self.requeue(batch); break
                        if self.auto and self.recovered < MAX_RECOVER and self.active() > 1:
                            self.recovered += 1
                            self.requeue(batch)
                            me["retire"] = True
                            n = self.active()
                            if self.tuner: self.tuner.lower(n)
                            self.set_target(n, f"whisper-cli 실패, 못 끝낸 조각을 되돌림: {tail.strip()[-120:]}")
                            break
                        self.fail("whisper 실패: " + tail[-400:]); break
                    me["batch"] = []
        except BaseException as e:
            with self.cond: self.fail(e)
        finally:
            with self.cond:
                self.workers.pop(w, None)
                self.spawn()
                self.cond.notify_all()

    def started(self, w: int, p):
        with self.cond:
            me = self.workers.get(w)
            if me is not None: me["proc"] = p
            if self.stop or (me and me["kill"]): p.kill()

    def requeue(self, batch: list):
        """못 끝낸 조각을 대기열 앞에(순서 유지). 잠금 안에서"""
        left = [n for n in batch if n not in self.seen and not self.done_fn(n)]
        for n in reversed(left): self.queue.appendleft(n)
        if left: self.queue_empty_at = None

    def note_done(self, names: list):
        now = self.clock()
        for n in names:
            if n not in self.seen and self.done_fn(n):
                self.seen.add(n); self.done_log.append((now, self.weights.get(n, 0)))

    def fail(self, err):
        """잠금 안에서. 처음 실패만 남기고 다른 whisper-cli를 죽인다(죽인 작업자의 종료 코드는 원인이 아니다)"""
        if not self.stop: self.errors.append(err)
        self.stop = True
        for x in self.workers.values():
            p = x["proc"]
            if p is not None and p.poll() is None: p.kill()
        self.cond.notify_all()

    def set_target(self, n: int, why: str | None):
        """잠금 안에서. 늘리면 바로 띄우고 줄이면 묶음 사이에 그만두게 한다"""
        n = max(1, int(n))
        if n == self.target: return
        self.target = n
        now = self.clock()
        self.changed_at = now
        self.changes.append((round(now - self.t0, 1), n, why or ""))
        self.n_time.append((now, n))
        if why: self.log(f"작업자 {n}개: {why}")
        self.spawn()
        if self.on_workers: self.on_workers(n)

    def shrink_now(self):
        """급히 줄이기: 목표보다 많으면 가장 늦게 띄운 작업자의 whisper-cli를 죽인다(못 끝낸 조각은 되돌린다). 잠금 안에서"""
        live = sorted((w for w, x in self.workers.items() if not x["kill"] and not x["retire"]), reverse=True)
        for w in live[:max(0, len(live) - self.target)]:
            x = self.workers[w]
            x["kill"] = True
            if x["proc"] is not None and x["proc"].poll() is None: x["proc"].kill()

    # ---- 재기 ----
    def window_rate(self, now: float) -> float | None:
        """지난 창의 처리량(오디오 초/초). 창 = 지난 조정 ~ 지금, 작업자 수를 바꾼 뒤 WARMUP초는 뺀다. 대기열이 비었던 창은 None"""
        start = max(self.last_tick, self.changed_at + WARMUP)
        if now - start < MIN_WINDOW: return None
        if self.queue_empty_at is not None and self.queue_empty_at < now: return None
        audio = sum(ms for t, ms in self.done_log if start < t <= now)
        return audio / 1000 / (now - start)

    def our_pids(self) -> set:
        out = set()
        for x in self.workers.values():
            p = x["proc"]
            if p is not None and getattr(p, "pid", None): out.add(p.pid)
        return out

    def step(self):
        """조정 한 번(run이 TICK마다 부름, 시험에서 직접)"""
        now = self.clock()
        with self.cond:
            self.note_done([n for x in self.workers.values() for n in x["batch"]])
            rate = self.window_rate(now)
            pids = self.our_pids()
            n = self.target
        r = (self.sampler.read(pids) if self.sampler else None) or Reading()
        r.rate = rate
        new, why, urgent = self.tuner.decide(r, n)
        with self.cond:
            self.last_tick = now
            if new != self.target:
                self.set_target(new, why)
                if urgent: self.shrink_now()

    def run(self):
        """다 끝날 때까지. 실패면 첫 예외를 다시 던지거나 sys.exit("whisper 실패: ...")"""
        with self.cond:
            if self.auto and self.tuner:
                r = self.sampler.read(set()) if self.sampler else None
                self.target = self.tuner.start(r)
            else:
                self.target = max(1, int(self.opts.get("n") or 1))
            self.n_time.append((self.clock(), self.target))
            self.changes.append((0.0, self.target, "시작"))
            if self.on_workers: self.on_workers(self.target)
            if not self.queue: return
            self.spawn()
        while True:
            with self.cond:
                if not self.workers and (self.stop or not self.queue): break   # 실패해도 작업자가 다 끝날 때까지 기다린다
                if not self.workers and self.queue: self.spawn()
                self.cond.wait(timeout=min(self.tick, 2.0))
                if not self.workers and (self.stop or not self.queue): break
                if self.auto: self.note_done([n for x in self.workers.values() for n in x["batch"]])   # 끝난 시각을 2초 안으로
            if self.auto and self.tuner and not self.stop and self.clock() - self.last_tick >= self.tick:
                try:
                    self.step()
                except Exception as e:   # 조정이 깨져도 전사는 지금 수로 끝까지
                    self.log(f"자동 조정 멈춤: {e}")
                    self.tuner = None
        if self.errors:
            if isinstance(self.errors[0], BaseException): raise self.errors[0]
            sys.exit(self.errors[0])

    def stats(self) -> dict:
        now = self.clock()
        pts = self.n_time + [(now, self.n_time[-1][1] if self.n_time else self.target)]
        span = pts[-1][0] - pts[0][0]
        avg = sum((b[0] - a[0]) * a[1] for a, b in zip(pts, pts[1:])) / span if span > 0 else float(pts[0][1])
        ns = [n for _, n in self.n_time] or [self.target]
        out = {"mode": "auto" if self.auto else "fixed", "start": ns[0], "min": min(ns), "max": max(ns), "avg": round(avg, 2),
               "final": self.target, "changes": [list(c) for c in self.changes[-MAX_CHANGES:]], "recovered": self.recovered, "batches": self.batches}
        if self.tuner: out["rates"] = {str(n): round(v[0], 3) for n, v in sorted(self.tuner.table.items())}
        return out


def run_pool(names: list, launch, done, reset, weights: dict | None, run: dict | None, *, key: str = "",
             cpu_only: bool = False, threads: int | None = None, on_workers=None, tuner: Tuner | None = None, sampler=None) -> dict:
    """파이프라인이 부르는 입구: 실행 옵션대로 풀을 돌리고 stats를 돌려준다. 자동이면 처리량 표(key = state_key)를 읽고(시작 수) 끝나면 쓴다.
    tuner·sampler는 시험에서 바꿔 끼운다(자동이 아니면 쓰지 않음)"""
    opts = parse(run, cpu_only)
    short = opts["auto"] and len(names) <= MIN_BATCH and tuner is None
    if short: opts = {**opts, "auto": False, "n": 1}   # 한 묶음뿐: 조정할 것이 없다(표도 안 건드림)
    if opts["auto"] and tuner is None:
        cpu_cap = max(1, (os.cpu_count() or 1) // max(1, int(threads or 4))) if cpu_only else None
        tuner = Tuner(load_table(key), opts["max"], opts["yield"] and not cpu_only, cpu_cap)
    if opts["auto"] and sampler is None: sampler = LazySampler(cpu_only)
    pool = Pool(names, launch, done, reset, weights, opts, tuner if opts["auto"] else None, sampler if opts["auto"] else None, on_workers=on_workers)
    try:
        pool.run()
    finally:
        if opts["auto"] and key and pool.tuner and pool.tuner.measured: save_table(key, pool.tuner.table)
        if sampler is not None and hasattr(sampler, "close"): sampler.close()
    st = pool.stats()
    if short: st.update(mode="auto", note=f"조각 {len(names)}개 ≤ {MIN_BATCH}: 한 묶음이라 조정 없이 1개")
    return st


# ---------------- 자원 측정(Windows) ----------------
class LazySampler:
    """Sampler를 뒤 스레드에서 만든다(GPU 엔진 카운터 붙이기 약 1.2초). 준비 전·실패면 read()는 빈 Reading"""
    def __init__(self, cpu_only: bool = False, create=None):
        self.s, self.closed = None, False
        self.t = threading.Thread(target=self._make, args=(create or Sampler.create, cpu_only), daemon=True)
        self.t.start()

    def _make(self, create, cpu_only):
        s = create(cpu_only)
        if self.closed and s is not None: s.close()
        else: self.s = s

    def read(self, ours: set) -> Reading:
        return self.s.read(ours) if self.s is not None else Reading()

    def close(self):
        self.closed = True
        if self.s is not None: self.s.close(); self.s = None


class Sampler:
    """PDH 성능 카운터(영어 이름, 언어와 상관없이) + DXGI(어댑터 LUID → 전용 VRAM) + GlobalMemoryStatusEx. 실패한 값은 None.
    read(우리 pid들) → Reading. GPU 사용률은 앞 read 이후 평균(첫 read는 None일 수 있다)."""
    PATHS = {"engine": r"\GPU Engine(*)\Utilization Percentage", "adapter": r"\GPU Adapter Memory(*)\Dedicated Usage",
             "procmem": r"\GPU Process Memory(*)\Dedicated Usage", "cpu": r"\Processor(_Total)\% Processor Time"}

    @staticmethod
    def create(cpu_only: bool = False):
        if os.name != "nt": return None
        try:
            return Sampler(cpu_only)
        except Exception as e:   # 측정을 못 하면 처리량만으로
            print(f"자원 측정 안 됨(처리량만으로 조정): {e}", file=sys.stderr, flush=True)
            return None

    def __init__(self, cpu_only: bool = False):
        from ctypes import wintypes
        self.cpu_only = cpu_only
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0: raise OSError("PdhOpenQuery")
        self.counters = {}
        for k, path in self.PATHS.items():
            if cpu_only and k != "cpu": continue
            h = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, ctypes.c_wchar_p(path), None, ctypes.byref(h)) == 0: self.counters[k] = h
        self.pdh.PdhCollectQueryData(self.query)
        self.vram = {} if cpu_only else dxgi_vram()
        self.wintypes = wintypes

    def close(self):
        if self.query: self.pdh.PdhCloseQuery(self.query); self.query = None

    def array(self, k: str) -> list:
        """[(인스턴스 이름, 값)] (PDH_FMT_DOUBLE | PDH_FMT_NOCAP100)"""
        h = self.counters.get(k)
        if h is None: return []

        class Value(ctypes.Structure):
            _fields_ = [("CStatus", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", Value)]
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        fmt = 0x00000200 | 0x00008000
        self.pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value: return []
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0: return []
        items = ctypes.cast(buf, ctypes.POINTER(Item))
        return [(items[i].szName or "", items[i].FmtValue.doubleValue) for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)]

    def read(self, ours: set) -> Reading:
        r = Reading()
        try:
            if self.pdh.PdhCollectQueryData(self.query) != 0: return r
        except OSError:
            return r
        cpu = self.array("cpu")
        if cpu: r.cpu = cpu[0][1]
        r.mem_free, r.commit_free = mem_available()
        if self.cpu_only: return r
        fill_gpu(r, self.array("engine"), self.array("adapter"), self.array("procmem"), ours, self.vram)
        return r


def parse_instance(name: str) -> dict:
    """'pid_123_luid_0x00000000_0x0000ABCD_phys_0_eng_0_engtype_3D' → {"pid": 123, "luid": "0x00000000_0x0000abcd", "eng": "0_3D"}"""
    out, parts = {}, name.split("_")
    for i, p in enumerate(parts):
        try:
            if p == "pid": out["pid"] = int(parts[i + 1])
            elif p == "luid": out["luid"] = (parts[i + 1] + "_" + parts[i + 2]).lower()
            elif p == "eng" and i + 1 < len(parts): out["eng"] = parts[i + 1]
            elif p == "engtype": out["eng"] = out.get("eng", "") + "_" + "_".join(parts[i + 1:])
        except (IndexError, ValueError):
            pass
    return out


def fill_gpu(r: Reading, engine: list, adapter: list, procmem: list, ours: set, vram: dict):
    """카운터 값들 → r의 GPU 칸(순수 함수, 시험). 우리 GPU = 우리 프로세스 전용 메모리가 가장 많은 어댑터(없으면 전용 메모리를 가장 많이 쓰는 어댑터).
    엔진 사용률 = 엔진마다 프로세스 합, 어댑터 사용률 = 엔진 중 최대(작업 관리자와 같은 방식). 우리/남 = 우리 pid만/나머지만의 같은 셈."""
    used = {parse_instance(n).get("luid"): v for n, v in adapter}
    used.pop(None, None)
    mine = {}
    per = []
    for n, v in procmem:
        i = parse_instance(n)
        if i.get("pid") in ours and i.get("luid"):
            mine[i["luid"]] = mine.get(i["luid"], 0) + v
            if v > 0: per.append(v)
    luid = max(mine, key=mine.get) if mine and max(mine.values()) > 0 else (max(used, key=used.get) if used else None)
    if luid is None: return
    if luid in used: r.vram_used = used[luid]
    if vram.get(luid): r.vram_total = vram[luid]
    big = [v for v in per if v > 200 * 1024 ** 2]   # 막 뜬(모델을 아직 안 올린) 프로세스는 뺀다
    if big: r.per_proc = sum(big) / len(big)
    tot, us, them = {}, {}, {}
    for n, v in engine:
        i = parse_instance(n)
        if i.get("luid") != luid or "eng" not in i: continue
        e = i["eng"]
        tot[e] = tot.get(e, 0) + v
        if i.get("pid") in ours: us[e] = us.get(e, 0) + v
        else: them[e] = them.get(e, 0) + v
    if tot:
        r.gpu = min(100.0, max(tot.values()))
        r.gpu_ours = min(100.0, max(us.values())) if us else 0.0
        r.gpu_others = min(100.0, max(them.values())) if them else 0.0


def mem_available() -> tuple:
    """(물리 메모리 여유, 커밋 여유) 바이트. 못 재면 (None, None)"""
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
    try:
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        return (float(m.ullAvailPhys), float(m.ullAvailPageFile)) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else (None, None)
    except (AttributeError, OSError):
        return None, None


def dxgi_vram() -> dict:
    """{luid("0xHIGH_0xLOW", 성능 카운터 인스턴스와 같은 꼴): 전용 VRAM 바이트}. DXGI를 못 쓰면 {}"""
    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

        class DESC1(ctypes.Structure):
            _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint),
                        ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                        ("SharedSystemMemory", ctypes.c_size_t), ("LuidLow", ctypes.c_uint32), ("LuidHigh", ctypes.c_int32), ("Flags", ctypes.c_uint)]
        iid = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))   # IDXGIFactory1
        factory = ctypes.c_void_p()
        if ctypes.WinDLL("dxgi").CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0: return {}

        def method(obj, index, *argtypes):
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])
        out, i = {}, 0
        try:
            while i < 16:
                ad = ctypes.c_void_p()
                if method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(factory, i, ctypes.byref(ad)) != 0: break   # EnumAdapters1
                d = DESC1()
                if method(ad, 10, ctypes.POINTER(DESC1))(ad, ctypes.byref(d)) == 0 and not (d.Flags & 2):   # GetDesc1, 소프트웨어 어댑터 제외
                    out[f"0x{d.LuidHigh & 0xffffffff:08x}_0x{d.LuidLow:08x}"] = float(d.DedicatedVideoMemory)
                method(ad, 2)(ad)   # Release
                i += 1
        finally:
            method(factory, 2)(factory)
        return out
    except (OSError, AttributeError, ValueError):
        return {}
