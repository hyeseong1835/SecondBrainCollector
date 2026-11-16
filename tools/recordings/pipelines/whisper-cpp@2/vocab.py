"""whisper-cpp@2 단어장 힌트(2026-11-16). 계획: 음성 전사 개선 계획.md "3-① 단어장"·"파이프라인 틀 인터페이스 약속"(--hints).

- 힌트 파일(analyze.py가 녹음마다 POST /api/vocab/pick으로 만든다): {"prompt": "김철수, SecondBrain", "fixes": [{id, wrong, right}], "ids": [힌트 항목 id]}
- prompt는 whisper-cli --prompt로 모든 조각에 똑같이 넣는다(transcribe.py).
- 고침 짝(apply_fixes): 전사 뒤 틀린 글 → 맞는 글. 낱말 경계에서만 바꾼다(앞은 글자·숫자가 아닐 것, 뒤는 글자·숫자가 아니거나 조사 + 경계).
  틀린 글의 공백은 공백 여러 개와도 맞고, 구간 경계에 걸쳐도 맞는다(--words면 낱말마다 구간이라 "세컨 브레인"이 두 구간). 걸친 구간들은 한 구간으로 합친다.
  바뀐 구간은 바꾸기 전 글을 original에 둔다(이미 있으면 그대로). 짝은 받은 순서(서버가 긴 틀린 글부터 준다)대로 차례로 바꾼다.
"""
import json, re
from pathlib import Path

# 낱말 뒤에 붙어도 경계로 보는 조사·어미(김철수가, SecondBrain에서). 뒤에 요가 더 붙어도 된다(김철수는요)
JOSA = "|".join(sorted("은 는 이 가 을 를 의 에 에서 에게 께 께서 한테 로 으로 와 과 도 만 랑 이랑 하고 까지 부터 처럼 보다 이나 나 요 이요 "
                      "이에요 예요 이야 야 아 씨 님 이고 고 이다 다 라고 이라고 라는 이라는 이면 면 이지 지".split(), key=len, reverse=True))


def load(path) -> dict:
    """힌트 파일 → {prompt, fixes, ids}. 없거나 깨졌으면 빈 힌트"""
    try:
        h = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {"prompt": "", "fixes": [], "ids": []}
    fixes = [f for f in h.get("fixes") or [] if isinstance(f, dict) and str(f.get("wrong") or "").strip() and f.get("right") is not None]
    return {"prompt": " ".join(str(h.get("prompt") or "").split()), "fixes": fixes, "ids": list(h.get("ids") or [])}


def pattern(wrong: str):
    """틀린 글 → 낱말 경계 정규식. 두 글자 안 되면 None(서버 규칙과 같음)"""
    words = wrong.split()
    if len("".join(words)) < 2: return None
    body = r"\s+".join(map(re.escape, words))
    return re.compile(r"(?<!\w)" + body + r"(?=(?:(?:" + JOSA + r")요?)?(?!\w))", re.IGNORECASE)


def replace(segs: list, pat, right: str) -> tuple:
    """구간들에서 pat을 right로. → (새 구간들, 바꾼 수). 글은 구간마다 공백 하나로 이어 놓고 찾는다"""
    spans, pos = [], 0
    for s in segs:
        spans.append((pos, pos + len(s["text"]))); pos += len(s["text"]) + 1
    text = " ".join(s["text"] for s in segs)
    hits = [m.span() for m in pat.finditer(text) if m.group(0) != right]
    out = list(segs)
    for a, b in reversed(hits):   # 뒤에서부터: 앞 구간의 자리는 그대로다
        i = next(k for k, (x, y) in enumerate(spans) if y > a)
        j = max(k for k, (x, y) in enumerate(spans) if x < b)
        part = out[i:j + 1]
        new = {**part[0], "endMs": max(s["endMs"] for s in part),
               "text": part[0]["text"][:a - spans[i][0]] + right + part[-1]["text"][b - spans[j][0]:],
               "original": " ".join(s.get("original", s["text"]) for s in part)}
        if any(s.get("suspect") for s in part): new["suspect"] = True
        out[i:j + 1] = [new]
        spans[i:j + 1] = [(spans[i][0], spans[j][1])]
    return out, len(hits)


def apply_fixes(segs: list, fixes: list) -> tuple:
    """→ (고친 구간들, 실제로 바꾼 고침 짝 id들)"""
    used = []
    for f in fixes:
        pat = pattern(str(f["wrong"]))
        if pat is None: continue
        segs, n = replace(segs, pat, str(f["right"]))
        if n and f.get("id") is not None: used.append(f["id"])
    return segs, used
