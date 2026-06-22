"""Reconcile an old transcription with a freshly re-recognized one (another model).

Two "mix" strategies:
- frequency: token-level consensus — where the two versions differ, keep the side whose words are
  more frequent across the rest of the book (a misread surname loses to the spelling that recurs).
- llm: hand both versions of the page to an LLM and take its consensus transcription.
"""
from __future__ import annotations

import asyncio
import difflib
import re
from collections import Counter

from ..extraction.normalize import strip_accents

_WORD = re.compile(r"\W+", re.UNICODE)


def _key(tok: str) -> str:
    """Normalized comparison key: accent-stripped, lowercased, punctuation removed."""
    return _WORD.sub("", strip_accents(tok).lower())


def book_frequency(texts: list[str]) -> Counter:
    """Count normalized word forms across the book's (active) transcriptions."""
    c: Counter = Counter()
    for t in texts:
        for w in (t or "").split():
            k = _key(w)
            if k:
                c[k] += 1
    return c


def merge_by_frequency(old: str, new: str, freq: Counter) -> str:
    """Align the two token streams; on each differing run keep the side with higher total word
    frequency in the book (equal runs are kept verbatim)."""
    ot, nt = (old or "").split(), (new or "").split()
    oa, na = [_key(x) for x in ot], [_key(x) for x in nt]
    sm = difflib.SequenceMatcher(a=oa, b=na, autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(ot[i1:i2])
        else:
            fo = sum(freq.get(oa[k], 0) for k in range(i1, i2))
            fn = sum(freq.get(na[k], 0) for k in range(j1, j2))
            out.extend(ot[i1:i2] if fo >= fn else nt[j1:j2])
    return " ".join(out)


_RECONCILE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}
_RECONCILE_SYSTEM = (
    "Eres un paleógrafo experto en documentos hispánicos. Recibes dos transcripciones HTR de la MISMA "
    "página manuscrita y devuelves la transcripción final más probable y correcta, corrigiendo errores "
    "de OCR cruzando ambas. Responde solo el JSON {text}."
)


async def llm_reconcile(rc, old: str, new: str) -> str:
    """Ask the inference LLM for a consensus transcription of one page. Falls back to ``new`` on error."""
    from ..providers.service import extract_structured

    prompt = f"VERSIÓN A:\n{old or ''}\n\nVERSIÓN B:\n{new or ''}\n\nTranscripción final:"
    try:
        out = await asyncio.to_thread(
            extract_structured, rc, prompt,
            schema=_RECONCILE_SCHEMA, system=_RECONCILE_SYSTEM, schema_name="reconcile",
        )
        return out.get("text") or (new or "")
    except Exception:
        return new or ""
