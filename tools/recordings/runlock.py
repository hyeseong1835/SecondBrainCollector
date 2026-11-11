"""analyze.py 동시 실행 막기(2026-11-11). 데스크톱 녹음 화면(Native/AnalyzeRunner.cs)도 같은 잠금 파일·같은 프로세스 판정을 쓴다.

- 잠금 파일: %LOCALAPPDATA%/SecondBrain/analysis/analyze.lock = {"pid", "started", "args"} JSON. 끝나면 지운다.
  죽은 PID(취소·강제 종료로 남은 파일)는 낡은 잠금으로 보고 덮어쓴다.
- 잠금을 모르는 옛 analyze.py(앱 밖에서 이미 돌던 것)도 막도록, 명령줄에 analyze.py가 든 python 프로세스를 함께 본다(자기와 조상 제외).
- --plan은 잠그지 않는다(분석하지 않으므로).
- 두 실행이 겹치면 llama-server 포트(8089)·GPU를 같이 써서 한쪽이 요약 중에 끊긴다(2026-11-11 실제로 앱 밖 전체 실행이 멈춤). 예외 옵션은 두지 않는다.
"""
import ctypes, datetime as dt, json, os, subprocess, sys
from pathlib import Path

LOCK = Path(os.environ.get("LOCALAPPDATA", ".")) / "SecondBrain" / "analysis" / "analyze.lock"


def pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if os.name != "nt":
        try: os.kill(pid, 0); return True
        except OSError: return False
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h: return False
    try:
        code = ctypes.c_ulong()
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def is_analyzer_cmd(cmd: str | None) -> bool:
    """python … analyze.py … 인지(데스크톱 AnalyzeRunner.IsAnalyzerCommand와 같은 판정)."""
    if not cmd: return False
    c = cmd.replace("\\", "/").lower()
    return "python" in c and ("/analyze.py" in c or " analyze.py" in c or '"analyze.py' in c)


def processes() -> list:
    """python 프로세스들 [{pid, ppid, cmd}]. Windows는 CIM(명령줄까지)."""
    if os.name != "nt": return []
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "@(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(p.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    if isinstance(data, dict): data = [data]
    return [{"pid": int(d["ProcessId"]), "ppid": int(d["ParentProcessId"] or 0), "cmd": d.get("CommandLine") or ""} for d in data]


def others(procs: list | None = None, me: int | None = None) -> list:
    """나(와 조상: py 런처·venv 래퍼)를 뺀, 돌고 있는 analyze.py 프로세스들."""
    procs = processes() if procs is None else procs
    me = os.getpid() if me is None else me
    by = {p["pid"]: p for p in procs}
    mine, cur = {me}, me
    while cur in by and by[cur]["ppid"] not in mine:   # 조상(WindowsApps python 스텁·venv 래퍼가 같은 명령줄로 진짜 python을 띄운다)
        cur = by[cur]["ppid"]; mine.add(cur)
    return [p for p in procs if p["pid"] not in mine and is_analyzer_cmd(p["cmd"])]


def read() -> dict | None:
    try:
        return json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def acquire(args: list) -> dict | None:
    """잠근다. 이미 다른 analyze.py가 돌면 잠그지 않고 그 정보({pid, started?, cmd?, via})를 돌려준다."""
    held = read()
    hp = int((held or {}).get("pid") or 0)
    if held and hp != os.getpid() and pid_alive(hp):
        return {**held, "via": "lock"}
    other = others()
    if other:
        return {"pid": other[0]["pid"], "cmd": other[0]["cmd"], "via": "process"}
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "args": args},
                               ensure_ascii=False), encoding="utf-8")
    return None


def release():
    held = read()
    if held and int(held.get("pid") or 0) == os.getpid():
        try: LOCK.unlink()
        except OSError: pass
