"""Spanish/Catalan/Latin name normalization for blocking and matching.

Two outputs per name part:
  * ``norm_*``  — lowercased, accent-stripped, Latin→vernacular folded (a stable comparison key).
  * ``block_key_*`` — a coarse phonetic code that collapses the ibérico spelling variation the
    corpus is full of (x↔j↔g, b↔v, silent h, qu/ck→k, ll→y, ç→s) so "Ginés"/"Xinés" and
    "Vidal"/"Bidal" land in the same block. This is the cheap recall filter run *before* any
    vector/ANN lookup (plan §3); abydos Beider-Morse is the M2 upgrade.

Pure Python, no DB — unit-tested directly.
"""
from __future__ import annotations

import unicodedata

# The clergy wrote in Latin; the tree is in the vernacular. Fold the common given names so
# "Joannes" blocks/compares with "Joan"/"Juan". Surnames are left as-is (rarely Latinized).
LATIN_FOLD: dict[str, str] = {
    "joannes": "joan", "ioannes": "joan", "joanne": "joan", "johannes": "joan",
    "jacobus": "jaume", "iacobus": "jaume", "jacobi": "jaume",
    "aegidius": "gil", "egidius": "gil",
    "eulalia": "olalla",
    "guillelmus": "guillem", "guilelmus": "guillem",
    "petrus": "pere", "petri": "pere",
    "franciscus": "francesc", "francisci": "francesc",
    "maria": "maria", "mariae": "maria",
    "antonius": "antoni", "antonii": "antoni",
    "michael": "miquel", "michaelis": "miquel",
    "bartholomeus": "bartomeu",
    "raymundus": "ramon", "raimundus": "ramon",
    "vincentius": "vicenc", "vincentii": "vicenc",
    "isabella": "isabel",
    "catharina": "caterina", "catarina": "caterina",
    "elisabeth": "isabel",
    "stephanus": "esteve",
    "laurentius": "llorenc",
    "matheus": "mateu", "matthaeus": "mateu",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return strip_accents(s).lower().strip()


def norm_given(given: str | None) -> str:
    """Lowercase + accent-strip + Latin→vernacular fold for a given name (first token folded)."""
    g = _clean(given)
    if not g:
        return ""
    # fold each token; the clergy's Latin form is usually the whole given name
    return " ".join(LATIN_FOLD.get(tok, tok) for tok in g.split())


def norm_surname(surname: str | None) -> str:
    return _clean(surname)


# Phonetic folding rules applied left-to-right after accent-stripping (order matters).
def spanish_phonetic(token: str) -> str:
    t = _clean(token)
    if not t:
        return ""
    t = t.replace("ç", "s")
    out: list[str] = []
    i = 0
    n = len(t)
    while i < n:
        c = t[i]
        nxt = t[i + 1] if i + 1 < n else ""
        if c == "h":  # silent
            i += 1
            continue
        if c == "l" and nxt == "l":  # ll → y
            out.append("y")
            i += 2
            continue
        if c == "q" and nxt == "u":  # qu → k
            out.append("k")
            i += 2
            continue
        if c == "g" and nxt in "ei":  # ge/gi → j sound
            out.append("j")
            i += 1
            continue
        if c == "c" and nxt in "ei":  # ce/ci → s sound
            out.append("s")
            i += 1
            continue
        if c == "c":  # hard c → k
            out.append("k")
            i += 1
            continue
        if c == "x":  # x → j (ibérico)
            out.append("j")
            i += 1
            continue
        if c in "vb":  # b↔v merge
            out.append("b")
            i += 1
            continue
        if c == "z":  # z → s (seseo)
            out.append("s")
            i += 1
            continue
        if c == "y":
            out.append("i")
            i += 1
            continue
        out.append(c)
        i += 1
    s = "".join(out)
    # collapse doubled letters and drop vowels after the first char (keep skeleton)
    collapsed: list[str] = []
    for ch in s:
        if collapsed and collapsed[-1] == ch:
            continue
        collapsed.append(ch)
    head = collapsed[0] if collapsed else ""
    tail = [ch for ch in collapsed[1:] if ch not in "aeiou"]
    return (head + "".join(tail))[:8]


def block_key_given(given: str | None) -> str:
    g = norm_given(given)
    first = g.split()[0] if g else ""
    return spanish_phonetic(first)


def block_key_surname(surname: str | None) -> str:
    s = norm_surname(surname)
    first = s.split()[0] if s else ""
    return spanish_phonetic(first)


def split_name(name_raw: str | None) -> tuple[str, str]:
    """Best-effort given/surname split from a verbatim name (first token given, rest surname)."""
    toks = (name_raw or "").split()
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0], ""
    return toks[0], " ".join(toks[1:])


def compute_keys(given: str | None, surname: str | None) -> dict[str, str]:
    """All four derived columns for a PersonMention in one call."""
    return {
        "norm_given": norm_given(given),
        "norm_surname": norm_surname(surname),
        "block_key_given": block_key_given(given),
        "block_key_surname": block_key_surname(surname),
    }


# Map the role labels an LLM emits in Spanish/Catalan/Latin → the canonical keys ROLE_RELATION
# (linkage.service) understands, so the discovery flywheel maps relatives correctly regardless of
# the document's language. Default → "other". Substring match (lowercased, accent-stripped).
_ROLE_SYNONYMS: list[tuple[tuple[str, ...], str]] = [
    (("cabeza de familia", "cap de familia", "cabeza", "head of household", "titular"), "head"),
    (("padre", "pare", "pater", "father"), "father"),
    (("madre", "mare", "mater", "mother"), "mother"),
    (("suegro", "sogre", "father-in-law"), "spouse_father"),
    (("suegra", "sogra", "mother-in-law"), "spouse_mother"),
    (("esposo", "esposa", "marido", "mujer", "conyuge", "conjuge", "espos", "muller", "spouse", "wife", "husband"), "spouse"),
    (("padrino", "padri", "godfather"), "godfather"),
    (("madrina", "padrina", "godmother"), "godmother"),
    (("hijo", "fill", "filius", "son"), "son"),
    (("hija", "filla", "filia", "daughter"), "daughter"),
    (("nino", "nina", "criatura", "parvulo", "child", "infante"), "child"),
    (("hermano", "hermana", "germa", "germana", "sibling", "brother", "sister"), "sibling"),
    (("abuelo", "abuela", "avi", "avia", "grandparent", "grandfather", "grandmother"), "grandparent"),
    (("nieto", "nieta", "net", "neta", "grandchild"), "grandchild"),
    (("cunado", "cunada", "yerno", "nuera", "in-law", "in_law"), "in_law"),
    (("criado", "criada", "sirviente", "servant", "domestico"), "servant"),
    (("huesped", "realquilado", "lodger", "boarder"), "lodger"),
    (("testador", "testator"), "testator"),
    (("heredero", "heredera", "heir"), "heir"),
    (("albacea", "executor"), "executor"),
    (("notario", "notary"), "notary"),
    (("procesado", "acusado", "reo", "defendant"), "defendant"),
    (("demandante", "querellante", "plaintiff"), "plaintiff"),
    (("juez", "judge"), "judge"),
    (("victima", "victim"), "victim"),
    (("mozo", "soldado", "quinto", "recluta", "soldier", "miliciano"), "soldier"),
    (("vecino", "residente", "morador", "resident", "habitante"), "resident"),
    (("testigo", "testimoni", "witness"), "witness"),
    (("declarante", "declarant"), "declarant"),
    (("oficiante", "rector", "cura", "parroco", "officiant", "priest", "presbitero", "pbro"), "officiant"),
    (("bautizado", "bautizada", "difunto", "difunta", "confirmado", "contrayente", "principal", "elector"), "principal"),
    (("pariente", "familiar", "relative"), "relative"),
]

# already-canonical keys pass through unchanged
_CANONICAL_ROLES = {
    "head", "father", "mother", "spouse", "spouse_father", "spouse_mother", "godfather",
    "godmother", "son", "daughter", "child", "sibling", "grandparent", "grandchild", "in_law",
    "servant", "lodger", "testator", "heir", "executor", "notary", "defendant", "plaintiff",
    "judge", "victim", "soldier", "resident", "witness", "declarant", "officiant", "principal",
    "relative", "party", "other",
}


def normalize_role(raw: str | None) -> str:
    """LLM role label (any language) → canonical ROLE_RELATION key. Default 'other'."""
    if not raw:
        return "other"
    r = strip_accents(raw).lower().strip()
    if r in _CANONICAL_ROLES:
        return r
    for needles, canonical in _ROLE_SYNONYMS:
        if any(n in r for n in needles):
            return canonical
    return "other"


def parse_age(stated_age: str | None) -> int | None:
    """Extract an integer age in years from a free-text 'stated_age' (e.g. '61 años', 'de edad de
    LXI', '6 meses' → 0). Returns None if no age is parseable."""
    if not stated_age:
        return None
    s = strip_accents(stated_age).lower()
    import re

    if any(u in s for u in ("mes", "dia", "hora", "semana")) and "ano" not in s and "any" not in s:
        return 0  # infants stated in months/days → ~0 years
    m = re.search(r"\d{1,3}", s)
    if m:
        return int(m.group())
    # crude Roman numeral fallback (parish records: 'LXI anys')
    roman = {"m": 1000, "d": 500, "c": 100, "l": 50, "x": 10, "v": 5, "i": 1}
    tokens = [t for t in re.findall(r"[mdclxvi]+", s) if len(t) >= 2]  # avoid stray 'i'/'d' in words
    if tokens:
        rn = max(tokens, key=len)
        total = prev = 0
        for ch in reversed(rn):
            val = roman.get(ch, 0)
            total += -val if val < prev else val
            prev = max(prev, val)
        if 0 < total < 130:
            return total
    return None
