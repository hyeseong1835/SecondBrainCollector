"""whisper-lite@1 품질 검사·반복 필터: whisper-cpp@2/quality.py를 그대로 베낌(2026-11-17, 파이프라인 폴더는 따로 넣고 빼므로). 원문 설명:
whisper-cpp@2 품질 검사·반복 필터(2026-11-16). 계획: 음성 전사 개선 계획.md "1단계. 반복 환각 막기".
transcribe.py가 조각마다 쓰고, tools/recordings/compare.py가 지표 잣대로 같이 쓴다(이 폴더는 공개 뒤 고치지 않으므로 잣대가 바뀌지 않는다).

- 지표(metrics): 글자 수, 말 길이, 같은 구절 연속 반복 수, 같은 글자 연속 길이, 압축률(글 바이트 / zlib 바이트, whisper와 같은 방식), 말 1초당 글자 수.
- 걸림(failures): 지표가 설정 문턱을 넘은 것들의 이름. 글자/초는 글이 30자 이상일 때만 본다(짧은 낱말 하나는 시각이 좁아 튄다).
- 필터(clean): 기호 부스러기(u/ n/ 따위) 제거 → 낱말 안 같은 조각 반복 접기(으으으… → 으) → 같은 구절(낱말 1~8개)이 문턱보다 많이 이어지면 한 번만.
  구간 여러 개에 걸친 반복도 접도록 낱말을 구간 순서대로 한 줄로 놓고 접은 뒤 구간으로 되돌린다(낱말이 다 빠진 구간은 버림).
"""
import re, zlib

# whisper가 말소리 없는 곳에서 지어내는 흔한 문장(유튜브 자막 학습 흔적). whisper-cpp@1과 같다
HALLUCINATIONS = re.compile(r"^(시청해\s*주셔서\s*감사합니다|구독과\s*좋아요|MBC\s*뉴스|자막\s*제공|한글\s*자막.*|[.\s,!?♪\-–—]*)$")
# 기호 부스러기: 한글·숫자 없이 영문 두 자 이하와 기호뿐인 짧은 낱말(2026-11-14 통화의 "u/" 수백 개, "n/")
JUNK = re.compile(r"^[A-Za-z]{0,2}[/\\|_~^*#=<>]+[A-Za-z]{0,2}$")
PUNCT = re.compile(r"[\s.,?!~…·\"'()\[\]-]+")
MAX_PHRASE_WORDS = 8


def key(word: str) -> str:
    """구절 비교용: 문장부호·공백 빼고 소문자"""
    return PUNCT.sub("", word).lower()


def phrase_repeat(words: list, max_n: int = MAX_PHRASE_WORDS) -> int:
    """같은 구절(낱말 1~max_n개)이 연속으로 나온 최대 횟수. 반복이 없으면 1, 낱말이 없으면 0"""
    ks = [key(w) for w in words]
    best = 1 if ks else 0
    for n in range(1, max_n + 1):
        i = 0
        while i + n <= len(ks):
            block, k = ks[i:i + n], 1
            while ks[i + k * n:i + (k + 1) * n] == block: k += 1
            if k > best: best = k
            i += (k - 1) * n + 1 if k > 1 else 1
    return best


def char_run(text: str) -> int:
    """공백을 뺀 같은 글자 최대 연속 길이"""
    best, last, run = 0, None, 0
    for c in text:
        if c.isspace(): continue
        run = run + 1 if c == last else 1
        last = c
        best = max(best, run)
    return best


def compression(text: str) -> float:
    """글 바이트 / zlib 압축 바이트. 반복이 많을수록 크다(whisper 기본 문턱 2.4). 빈 글은 0"""
    b = text.encode("utf-8")
    return round(len(b) / len(zlib.compress(b)), 2) if b else 0.0


def speech_ms(segs: list) -> int:
    """구간들이 덮는 시간(겹침은 한 번만)"""
    total, end = 0, None
    for s in sorted(segs, key=lambda s: s["startMs"]):
        a, b = s["startMs"], s["endMs"]
        if end is not None and a < end: a = end
        if b > a: total += b - a
        end = b if end is None else max(end, b)
    return total


def metrics(segs: list) -> dict:
    text = " ".join(s["text"] for s in segs)
    chars = sum(1 for c in text if not c.isspace())
    ms = speech_ms(segs)
    return {"chars": chars, "speechSec": round(ms / 1000, 1), "phraseRepeat": phrase_repeat(text.split()), "charRun": char_run(text),
            "compression": compression(text), "charsPerSec": round(chars / (ms / 1000), 1) if ms else None}


def failures(m: dict, s: dict) -> list:
    """설정 문턱을 넘은 지표 이름들(빈 목록 = 통과)"""
    out = []
    if m["phraseRepeat"] > s["maxPhraseRepeat"]: out.append("phraseRepeat")
    if m["charRun"] > s["maxCharRun"]: out.append("charRun")
    if m["compression"] > s["maxCompressionRatio"]: out.append("compression")
    if m["charsPerSec"] is not None and m["chars"] >= 30 and m["charsPerSec"] > s["maxCharsPerSec"]: out.append("charsPerSec")
    return out


def collapse(keys: list, max_repeat: int = 3, max_n: int = MAX_PHRASE_WORDS) -> list:
    """같은 구절(키 1~max_n개)이 max_repeat번 넘게 이어지면 첫 번만 남긴다. → 남길 자리 번호들(오름차순)"""
    keep = list(range(len(keys)))
    changed = True
    while changed:
        changed = False
        for n in range(1, max_n + 1):
            ks, out, i = [keys[j] for j in keep], [], 0
            while i < len(ks):
                block = ks[i:i + n]
                if len(block) == n:
                    k = 1
                    while ks[i + k * n:i + (k + 1) * n] == block: k += 1
                    if k > max_repeat:
                        out += keep[i:i + n]; i += k * n; changed = True
                        continue
                out.append(keep[i]); i += 1
            keep = out
    return keep


def squeeze(word: str, max_repeat: int = 3) -> str:
    """낱말 안에서 같은 조각(1~8자)이 max_repeat번 넘게 이어지면 한 번만: 으으으으 → 으"""
    return re.sub(r"(.{1,8}?)\1{%d,}" % max_repeat, r"\1", word)


def clean(segs: list, max_repeat: int = 3) -> list:
    """반복 필터. 구간 시각은 그대로 두고 글만 줄인다. 글이 다 빠진 구간은 버린다"""
    words = []   # (구간 번호, 낱말)
    for i, s in enumerate(segs):
        for w in s["text"].split():
            if JUNK.match(w): continue
            words.append((i, squeeze(w, max_repeat)))
    kept = collapse([key(w) or w for _, w in words], max_repeat)
    texts = {}
    for j in kept:
        i, w = words[j]
        texts.setdefault(i, []).append(w)
    out = []
    for i, s in enumerate(segs):
        text = " ".join(texts.get(i, []))
        if text and not HALLUCINATIONS.match(text): out.append({**s, "text": text})
    return out
