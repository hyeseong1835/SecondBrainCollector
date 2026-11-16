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

    def test_silent_bundles_across_pipeline_results(self):
        def row(file, start, text, files=None, src="phone"):
            r = {"source": src, "file": file, "startedAt": start, "transcript": text, "segments": [{"text": text}] if text else []}
            if files is not None: r["files"] = files
            return r
        rows = [
            row("a.m4a", "2026-11-11T09:00", ""),                                  # whisper-cpp@1 설정 1: 말 없음
            row("a.m4a", "2026-11-11T09:00", "", '[{"file": "a.m4a"}]'),            # 설정 2도 말 없음(files가 글로 와도) → 한 번만
            row("b.m4a", "2026-11-11T10:00", ""),                                  # 다른 결과에 말 있음 → 뺌
            row("b.m4a", "2026-11-11T10:00", "네."),
            row("c.m4a", "2026-11-11T11:00", "", [{"file": "c.m4a"}, {"file": "d.m4a"}]),
            row("d.m4a", "2026-11-11T11:05", "어."),                               # 다른 묶음 키지만 같은 파일에 말 → 뺌
            row("e.m4a", "2026-11-11T08:00", "", src="laptop-mic"),
            row("e.m4a", "2026-11-11T08:00", "음.", src="phone"),                  # 출처가 다르면 상관없음
        ]
        self.assertEqual([(r["source"], r["file"]) for r in T.silent_bundles(rows)], [("laptop-mic", "e.m4a"), ("phone", "a.m4a")])

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
        self.assertEqual(c[8:], ["2", "s1", "e1", "1"])   # 폰 원본도(2026-11-12 사용자 결정)
        self.assertEqual(T.listed(self.root), {("phone", "20261111-090000-aaaa.m4a"), ("phone", "20261111-090040-bbbb.m4a")})

    def test_favorites_and_protected(self):
        rows = [{"device": "laptop", "file": "laptop-mic/2026-11-16/m.m4a", "source": "laptop-mic"}, {"device": "phone", "file": "2026-11-16/a.m4a", "source": "phone"}]
        favs = T.favorites(rows)
        self.assertEqual(favs, {("laptop-mic", "m.m4a"), ("phone", "a.m4a")})
        self.assertTrue(T.protected({("phone", "a.m4a"), ("phone", "b.m4a")}, set(), favs))   # 묶음 파일 하나라도 즐겨찾기면 뺌
        self.assertFalse(T.protected({("phone", "b.m4a")}, set(), favs))
        self.assertTrue(T.protected({("phone", "b.m4a")}, {("phone", "b.m4a")}, favs))   # 되살린 것
        self.assertTrue(T.protected({("phone", "b.m4a")}, set(), None))   # 즐겨찾기를 못 읽으면 옮기지 않음
        self.assertEqual(T.favorites(None), set())

    def test_kept(self):
        self.assertEqual(T.kept(self.root), set())
        (self.root / "_trash").mkdir()
        (self.root / "_trash" / "kept.tsv").write_bytes("﻿source\tname\tkept_at\r\nphone\ta.m4a\t2026-11-12T10:00:00+09:00\r\n".encode())
        self.assertEqual(T.kept(self.root), {("phone", "a.m4a")})

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
