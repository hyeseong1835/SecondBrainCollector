"""녹음 묶음 규칙(2026-11-11). 값은 API(GET /api/recordings/bundle-rule)가 한 곳에서 내려주고, 데스크톱 녹음 화면(RecordingsPage.cs)도 같은 방식으로 묶는다.

- 같은 출처에서 앞 파일 끝(그때까지 가장 늦은 끝)과 다음 파일 시작의 간격이 gapSec 이하이면 같은 묶음. 시간이 겹쳐도 같은 묶음(디스코드 봇의 사람별 파일)
- 묶음이 maxSec보다 길면 가장 긴 간격(같으면 앞쪽)에서 둘로 나누고, 각각 다시 본다
- singleSources(통화)는 파일 하나가 한 묶음
- 마지막 파일 끝에서 gapSec이 아직 안 지났으면 진행 중(분석하지 않는다)
시각은 epoch ms 정수로 다룬다.
"""
from dataclasses import dataclass, field

DEFAULT_RULE = {"gapSec": 60, "maxSec": 1800, "singleSources": ["call"]}


@dataclass
class Item:
    source: str
    rel: str            # API의 file(원천 안 상대 경로, 통화는 "call/…")
    start: int          # epoch ms
    end: int
    path: object = None
    track: str = ""     # 디스코드 봇: 사람(user_id)
    speaker: str | None = None


@dataclass
class Bundle:
    source: str
    items: list = field(default_factory=list)

    @property
    def start(self): return min(i.start for i in self.items)

    @property
    def end(self): return max(i.end for i in self.items)


def _gaps(items):
    """정렬된 파일들 사이 간격들(앞쪽 가장 늦은 끝 기준, 겹치면 음수)."""
    out, end = [], items[0].end
    for it in items[1:]:
        out.append(it.start - end)
        end = max(end, it.end)
    return out


def _split(items, max_ms):
    if len(items) < 2 or max(i.end for i in items) - items[0].start <= max_ms:
        return [items]
    g = _gaps(items)
    k = g.index(max(g)) + 1
    return _split(items[:k], max_ms) + _split(items[k:], max_ms)


def make_bundles(items, rule=DEFAULT_RULE):
    """Item들 → 출처별 Bundle 목록(시작 순)."""
    gap, max_ms = rule["gapSec"] * 1000, rule["maxSec"] * 1000
    single = set(rule.get("singleSources") or [])
    out = []
    for src in sorted({i.source for i in items}):
        mine = sorted((i for i in items if i.source == src), key=lambda i: (i.start, i.end, i.rel))
        if src in single:
            out += [Bundle(src, [i]) for i in mine]
            continue
        groups, cur, cur_end = [], [], None
        for it in mine:
            if cur and it.start - cur_end <= gap:
                cur.append(it); cur_end = max(cur_end, it.end)
            else:
                if cur: groups.append(cur)
                cur, cur_end = [it], it.end
        if cur: groups.append(cur)
        for grp in groups:
            out += [Bundle(src, part) for part in _split(grp, max_ms)]
    return sorted(out, key=lambda b: (b.start, b.source))


def in_progress(b: Bundle, now_ms: int, rule=DEFAULT_RULE) -> bool:
    if b.source in set(rule.get("singleSources") or []):
        return False
    return now_ms - b.end < rule["gapSec"] * 1000
