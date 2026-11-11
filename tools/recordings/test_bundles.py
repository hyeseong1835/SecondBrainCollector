"""묶음 규칙 시험: python -m unittest tools/recordings/test_bundles.py (데스크톱 RecordingsPage 묶기와 같은 결과여야 한다)"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bundles import Item, make_bundles, in_progress

RULE = {"gapSec": 60, "maxSec": 1800, "singleSources": ["call"]}
S = 1000


def it(src, s, e, name=None, track=""):
    return Item(src, name or f"{src}-{s}", s * S, e * S, track=track)


class BundleTests(unittest.TestCase):
    def test_gap_threshold_inclusive(self):
        b = make_bundles([it("phone", 0, 10), it("phone", 70, 80), it("phone", 141, 150)], RULE)
        self.assertEqual([len(x.items) for x in b], [2, 1])   # 60초는 같은 묶음, 61초는 나뉨

    def test_sources_never_mix(self):
        b = make_bundles([it("phone", 0, 10), it("discord-output", 5, 15)], RULE)
        self.assertEqual(len(b), 2)

    def test_overlap_counts_and_uses_latest_end(self):
        # 봇: 사람별 파일이 겹친다. 짧은 파일이 긴 파일 안에 있어도 긴 파일 끝 기준
        b = make_bundles([it("discord-bot", 0, 300, track="a"), it("discord-bot", 10, 20, track="b"), it("discord-bot", 350, 360, track="b")], RULE)
        self.assertEqual(len(b), 1)

    def test_call_is_single(self):
        self.assertEqual(len(make_bundles([it("call", 0, 10), it("call", 20, 30)], RULE)), 2)

    def test_long_bundle_splits_at_largest_gap(self):
        # 0~2000초, 간격은 모두 50초이고 950→1010만 60초 → 거기서 나뉜다
        items = [it("phone", t, t + 50) for t in range(0, 1000, 100)] + [it("phone", 1010, 1060)] + [it("phone", t, t + 50) for t in range(1100, 2000, 100)]
        b = make_bundles(items, RULE)
        self.assertEqual([len(x.items) for x in b], [10, 10])
        self.assertTrue(all(x.end - x.start <= 1800 * S for x in b))

    def test_split_tie_takes_first(self):
        items = [it("phone", t, t + 50) for t in range(0, 2000, 100)]   # 간격이 모두 50초
        self.assertEqual([len(x.items) for x in make_bundles(items, RULE)], [1, 1, 18])

    def test_in_progress(self):
        b = make_bundles([it("phone", 0, 10)], RULE)[0]
        self.assertTrue(in_progress(b, 69 * S, RULE))
        self.assertFalse(in_progress(b, 70 * S, RULE))
        self.assertFalse(in_progress(make_bundles([it("call", 0, 10)], RULE)[0], 11 * S, RULE))


if __name__ == "__main__":
    unittest.main()
