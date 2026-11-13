"""python -m unittest test_asrmodels (임시 폴더에서만)"""
import json, os, tempfile, unittest
from pathlib import Path
import asrmodels as M


class AsrModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        for n in ("ggml-batisay-ko-turbo-q8_0.bin", "ggml-large-v3-turbo.bin", "ggml-silero-v5.1.2.bin",
                  "ggml-medium.en.bin", "ggml-tiny.en-q5_1.bin", "for-tests-ggml-tiny.bin", "EXAONE.gguf"):
            (self.models / n).write_bytes(b"x" * 3)
        self.old = M.ANALYSIS, os.environ.pop("SECONDBRAIN_INSTANCE", None)
        M.ANALYSIS = self.root

    def tearDown(self):
        M.ANALYSIS = self.old[0]
        if self.old[1] is not None: os.environ["SECONDBRAIN_INSTANCE"] = self.old[1]
        else: os.environ.pop("SECONDBRAIN_INSTANCE", None)
        self.tmp.cleanup()

    def settings(self, value, name="settings.json"):
        (self.root / name).write_text(json.dumps({"asrModel": value}), encoding="utf-8")

    def test_available_skips_vad_english_and_tests(self):
        self.assertEqual([n for n, _, _ in M.available()], ["batisay-ko-turbo-q8_0", "large-v3-turbo"])

    def test_default_without_settings(self):
        self.assertEqual(M.resolve(None)[0], M.DEFAULT)
        self.assertIsNotNone(M.resolve(None)[1])

    def test_settings_value_used(self):
        self.settings("large-v3-turbo")
        self.assertEqual(M.resolve(None), ("large-v3-turbo", self.models / "ggml-large-v3-turbo.bin"))

    def test_settings_missing_model_falls_back(self):
        self.settings("large-v3-q5_0")
        self.assertEqual(M.resolve(None)[0], M.DEFAULT)

    def test_explicit_name_forms(self):
        for x in ("large-v3-turbo", "ggml-large-v3-turbo.bin", "large-v3-turbo.bin"):
            self.assertEqual(M.resolve(x)[0], "large-v3-turbo")

    def test_explicit_missing_or_unusable(self):
        self.assertEqual(M.resolve("large-v3"), ("large-v3", None))
        self.assertIsNone(M.resolve("medium.en")[1])
        self.assertIsNone(M.resolve("silero-v5.1.2")[1])

    def test_instance_settings(self):
        self.settings("batisay-ko-turbo-q8_0")
        self.settings("large-v3-turbo", "settings.t1.json")
        os.environ["SECONDBRAIN_INSTANCE"] = "t1"
        self.assertEqual(M.resolve(None)[0], "large-v3-turbo")
        os.environ["SECONDBRAIN_INSTANCE"] = "t2"   # 그 인스턴스 파일이 없으면 공용
        self.assertEqual(M.resolve(None)[0], "batisay-ko-turbo-q8_0")


if __name__ == "__main__":
    unittest.main()
