"""python -m unittest test_trash (임시 폴더에서만, 실제 녹음은 건드리지 않음)"""
import datetime as dt, tempfile, unittest
from pathlib import Path
import trash as T

NOW = dt.datetime(2026, 11, 12, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))


class TrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d = self.root / "phone" / "2026-11-11"
        d.mkdir(parents=True)
        self.files = [d / "20261111-090000-aaaa.m4a", d / "20261111-090040-bbbb.m4a"]
        for f in self.files: f.write_bytes(b"x")
        (d / "20261111-090000-aaaa.json").write_text("{}")
        (d / "20261111-100000-keep.m4a").write_bytes(b"y")

    def tearDown(self): self.tmp.cleanup()

    def test_silent(self):
        self.assertTrue(T.is_silent([], ""))
        self.assertTrue(T.is_silent([], "  "))
        self.assertFalse(T.is_silent([{"text": "어."}], "어."))

    def test_move_bundle(self):
        moved = T.move_bundle(self.root, "phone", self.files, "2026-11-11T09:00:00+09:00", NOW, spans=[("s1", "e1"), ("s2", "e2")])
        self.assertEqual(moved, ["2026-11-11/20261111-090000-aaaa.m4a", "2026-11-11/20261111-090040-bbbb.m4a"])
        for f in self.files: self.assertFalse(f.exists())
        t = self.root / "_trash" / "phone" / "2026-11-11"
        self.assertTrue((t / "20261111-090000-aaaa.m4a").exists())
        self.assertTrue((t / "20261111-090000-aaaa.json").exists())   # 옆 파일도 같이
        self.assertTrue((self.root / "phone" / "2026-11-11" / "20261111-100000-keep.m4a").exists())
        tomb = (self.root / "phone" / "deleted.tsv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(tomb[0], "name\tfile\tdeleted_at\treason")
        self.assertTrue(tomb[1].startswith("20261111-090000-aaaa.m4a\t2026-11-11/20261111-090000-aaaa.m4a\t2026-11-12T09:30:00\t말소리 없음"))
        rows = (self.root / "_trash" / "trash.tsv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(rows[0].split("\t"), T.COLUMNS)
        c = rows[1].split("\t")
        self.assertEqual(c[:3], ["phone", "20261111-090000-aaaa.m4a", "2026-11-11/20261111-090000-aaaa.m4a"])
        self.assertEqual(c[4], "2026-11-19T09:30:00+09:00")   # 7일 뒤
        self.assertEqual(c[8:], ["2", "s1", "e1"])
        self.assertEqual(T.listed(self.root), {("phone", "20261111-090000-aaaa.m4a"), ("phone", "20261111-090040-bbbb.m4a")})

    def test_existing_three_column_tombstone(self):
        (self.root / "phone" / "deleted.tsv").write_bytes("﻿name\tfile\tdeleted_at\r\nold.m4a\told.m4a\t2026-11-11T00:00:00".encode())   # 끝 줄바꿈 없음
        T.move_bundle(self.root, "phone", self.files[:1], "s", NOW)
        lines = (self.root / "phone" / "deleted.tsv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2].split("\t")[0], "20261111-090000-aaaa.m4a")

    def test_refuses_outside_source(self):
        other = self.root / "call" / "x.m4a"; other.parent.mkdir(); other.write_bytes(b"z")
        with self.assertRaises(ValueError): T.move_bundle(self.root, "phone", [other], "s", NOW)
        self.assertTrue(other.exists())
        self.assertFalse((self.root / "phone" / "deleted.tsv").exists())


if __name__ == "__main__":
    unittest.main()
