"""묶음 규칙 시험: python -m unittest tools/recordings/test_bundles.py (데스크톱 RecordingsPage 묶기와 같은 결과여야 한다)"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bundles import Item, make_bundles, make_calls, in_progress

RULE = {"gapSec": 60, "maxSec": 1800, "singleSources": ["call"], "callSources": ["discord-output", "laptop-mic"]}
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


    def test_call_joins_two_sources_within_gap(self):
        # 상대(0~100, 200~300)·내 쪽(150~250)이 잇고, 300+60=360에 시작하는 내 쪽도 같은 통화, 370+61=431은 아님
        items = [it("discord-output", 0, 100), it("discord-output", 200, 300), it("laptop-mic", 150, 250), it("laptop-mic", 360, 370),
                 it("laptop-mic", 431, 440), it("discord-output", 1000, 1010)]
        calls = make_calls(make_bundles(items, RULE), RULE)
        self.assertEqual(len(calls), 1)
        self.assertEqual([(b.source, b.start // S) for b in calls[0]], [("discord-output", 0), ("laptop-mic", 150), ("discord-output", 200), ("laptop-mic", 360)])

    def test_call_needs_both_sources_and_ignores_others(self):
        items = [it("discord-output", 0, 10), it("discord-output", 1000, 1010), it("discord-bot", 5, 20), it("phone", 5, 20)]
        self.assertEqual(make_calls(make_bundles(items, RULE), RULE), [])

    def test_call_has_no_length_limit(self):
        # 각 출처는 30분에서 나뉘어도 통화는 하나
        items = [it("discord-output", t, t + 50) for t in range(0, 4000, 100)] + [it("laptop-mic", t + 20, t + 70) for t in range(0, 4000, 100)]
        bundles = make_bundles(items, RULE)
        self.assertGreater(len(bundles), 2)
        self.assertEqual([len(c) for c in make_calls(bundles, RULE)], [len(bundles)])


if __name__ == "__main__":
    unittest.main()
