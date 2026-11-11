"""잠금 판정 시험: python -m unittest tools/recordings/test_runlock.py (데스크톱 AnalyzeRunner와 같은 판정)"""
import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import runlock as L


class RunLockTests(unittest.TestCase):
    def test_analyzer_command(self):
        self.assertTrue(L.is_analyzer_cmd(r"C:\Python314\python.exe analyze.py"))
        self.assertTrue(L.is_analyzer_cmd(r'"C:\Python314\python.exe" -u "C:\x\tools\recordings\analyze.py" --pick a.m4a'))
        self.assertFalse(L.is_analyzer_cmd(r"C:\Python314\python.exe speakers.py enroll"))
        self.assertFalse(L.is_analyzer_cmd(r"C:\Python314\python.exe reanalyze.py"))
        self.assertFalse(L.is_analyzer_cmd(None))

    def test_others_skips_self_and_ancestors(self):
        procs = [
            {"pid": 10, "ppid": 1, "cmd": r"C:\WindowsApps\python.exe analyze.py"},     # 스텁(내 부모)
            {"pid": 11, "ppid": 10, "cmd": r"C:\Python314\python.exe analyze.py"},      # 나
            {"pid": 20, "ppid": 1, "cmd": r"C:\Python314\python.exe analyze.py"},       # 앱 밖 다른 실행
            {"pid": 30, "ppid": 1, "cmd": r"C:\Python314\python.exe -m markitdown_mcp"},
        ]
        self.assertEqual([p["pid"] for p in L.others(procs, me=11)], [20])
        self.assertEqual(L.others(procs[:2] + procs[3:], me=11), [])

    def test_pid_alive(self):
        self.assertTrue(L.pid_alive(os.getpid()))
        self.assertFalse(L.pid_alive(0))

    def test_set_plan_only_own_lock(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            old, L.LOCK = L.LOCK, Path(d) / "analyze.lock"
            try:
                L.LOCK.write_text(json.dumps({"pid": os.getpid() + 1}), encoding="utf-8")
                L.set_plan(["phone/a.m4a"])
                self.assertNotIn("plan", L.read())          # 남의 잠금은 안 건드림
                L.LOCK.write_text(json.dumps({"pid": os.getpid(), "args": []}), encoding="utf-8")
                L.set_plan(["phone/a.m4a", "call/b.m4a"])
                self.assertEqual(L.read()["plan"], ["phone/a.m4a", "call/b.m4a"])
            finally:
                L.LOCK = old


if __name__ == "__main__":
    unittest.main()
