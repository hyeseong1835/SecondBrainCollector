import os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import sbpaths as P


class SbPathsTests(unittest.TestCase):
    def test_prefer_new_then_legacy(self):
        with tempfile.TemporaryDirectory() as d:
            new, old = Path(d) / "new.json", Path(d) / "old.json"
            self.assertEqual(P.prefer(new, old), new)        # 둘 다 없으면 새 자리
            old.write_text("{}"); self.assertEqual(P.prefer(new, old), old)
            new.write_text("{}"); self.assertEqual(P.prefer(new, old), new)

    def test_home_is_user_profile(self):
        if os.environ.get("SECONDBRAIN_DATA_DIR"): self.skipTest("SECONDBRAIN_DATA_DIR")
        self.assertEqual(P.HOME, Path.home() / ".secondbrain")
        self.assertEqual(P.ANALYSIS, P.HOME / "analysis")
        self.assertEqual(P.TOOLS.parts[-1], "analysis")
        self.assertEqual(P.tools_candidates()[:2], [P.ANALYSIS, P.LEGACY_ANALYSIS])


if __name__ == "__main__":
    unittest.main()
