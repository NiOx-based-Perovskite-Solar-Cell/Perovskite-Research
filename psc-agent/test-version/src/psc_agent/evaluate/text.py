"""Names, formulas and solvents, reduced to something two curators (or a curator and a model) can agree on.

The property aliases follow the lab pipeline (pipeline/stage2_prepare.py BASE_ALIAS, pipeline/parsers.py decompose),
so "Avg PCE" and "Average PCE", or "Roughness RMS" and "AFM rms", are one property here too.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def vkey(s) -> str:
    """Letters and digits only, lower case: ``Spin coating`` and ``SpinCoating`` are the same key."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(s)).casefold())


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and vkey(v) in ("", "na", "none", "null", "nan", "nr"))


# ---------------------------------------------------------------- process types and properties

_PROCESS = {"plasmatreatment": "plasmatreat", "uvozone": "uvozonetreat", "uvozonetreatment": "uvozonetreat",
            "electronbeamevaporation": "ebeamevaporation", "evaporation": "thermalevaporation",
            "thermalevaporate": "thermalevaporation", "magneticstir": "stir", "stirring": "stir", "mix": "stir",
            "mixing": "stir", "spincoat": "spincoating", "annealed": "annealing", "drying": "dry",
            "washing": "wash", "cleaning": "wash", "antisolventdrip": "antisolvent", "antisolventdropping": "antisolvent"}


def process_key(name) -> str:
    k = vkey(name)
    return _PROCESS.get(k, k)


_BASE = {"eisrrec": "rrec", "eisrs": "rs", "rseis": "rs", "seriesresistance": "rs", "ef": "fermilevel",
         "efups": "fermilevel", "vbmups": "vbm", "vb": "vbm", "wf": "workfunction", "workfunctionups": "workfunction",
         "electricalconductivity": "conductivity", "hysteresisindex": "hi", "integratedjsc": "jsceqe",
         "integratedeqejsc": "jsceqe", "eqejsc": "jsceqe", "bandgap": "opticalbandgap", "energygap": "opticalbandgap",
         "afmroughness": "afmrms", "roughness": "afmrms", "rmsroughness": "afmrms", "roughnessrms": "afmrms",
         "rms": "afmrms", "rq": "afmrms", "pceavg": "pce", "kpfmcpd": "surfacepotentialcpd"}
_STAT = re.compile(r"^(champion|average|avg|mean|best|certified|verified)\b[\s:]*(.+)$", re.I)
_SCAN = re.compile(r"^(.+?)\s*\(\s*(rs|fs|reverse|forward)(?:\s+scan)?\s*\)\s*$", re.I)
CORE_METRICS = {"pce", "voc", "jsc", "ff"}


def property_key(name) -> tuple[str, str, str]:
    """``Champion Voc (RS)`` -> (``voc``, ``champion``, ``rs``). A bare PCE/Voc/Jsc/FF is the champion value."""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    stat = scan = ""
    m = _STAT.match(s)
    if m:
        stat = {"avg": "average", "mean": "average", "best": "champion"}.get(m[1].lower(), m[1].lower())
        s = m[2]
    m = _SCAN.match(s)
    if m:
        s, scan = m[1], {"reverse": "rs", "forward": "fs"}.get(m[2].lower(), m[2].lower())
    base = vkey(s)
    base = _BASE.get(base, base)
    if base in CORE_METRICS and not stat:
        stat = "champion"
    return base, stat, scan


# ---------------------------------------------------------------- text

def text_sim(a, b) -> float:
    ka, kb = vkey(a), vkey(b)
    if not ka or not kb:
        return 0.0
    if ka == kb or (min(len(ka), len(kb)) >= 3 and (ka in kb or kb in ka)):  # "Me-4PACz" vs "Me-4PACz SAM"
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def text_status(a, b) -> str:
    s = text_sim(a, b)
    return "match" if s >= 0.88 else "review" if s >= 0.6 else "mismatch"


# ---------------------------------------------------------------- perovskite formulas

_ION = re.compile(r"(Cs|FA|MA|Rb|K|Pb|Sn|Br|Cl|I)(\d*\.?\d*)")
_A_SITE, _X_SITE = ("Cs", "FA", "MA", "Rb", "K"), ("I", "Br", "Cl")


def composition(formula) -> tuple[dict[str, float], dict[str, float]] | None:
    """A-site and X-site fractions of ``Cs0.05(FA0.83MA0.12)0.95Pb(I0.83Br0.17)3``; None if it is not such a formula."""
    s = re.sub(r"\(\s*with[^)]*\)|\s+", "", str(formula), flags=re.I)
    stack: list[list[tuple[str, float]]] = [[]]
    i = 0
    while i < len(s):
        if s[i] == "(":
            stack.append([])
            i += 1
        elif s[i] == ")" and len(stack) > 1:
            group = stack.pop()
            m = re.match(r"\d*\.?\d+", s[i + 1:])
            mult = float(m[0]) if m else 1.0
            stack[-1].extend((ion, c * mult) for ion, c in group)
            i += 1 + (len(m[0]) if m else 0)
        else:
            m = _ION.match(s, i)
            if m:
                stack[-1].append((m[1], float(m[2]) if m[2] not in ("", ".") else 1.0))
                i = m.end()
            else:
                i += 1
    ions = [x for part in stack for x in part]
    sites = []
    for names in (_A_SITE, _X_SITE):
        part = {}
        for ion, c in ions:
            if ion in names:
                part[ion] = part.get(ion, 0.0) + c
        total = sum(part.values())
        if not total:
            return None
        sites.append({k: v / total for k, v in part.items()})
    return sites[0], sites[1]


def formula_status(a, b, tol: float = 0.02) -> str | None:
    """match / mismatch when both are perovskite formulas, else None (compare as text)."""
    ca, cb = composition(a), composition(b)
    if ca is None or cb is None:
        return None
    ok = all(abs(pa.get(k, 0.0) - pb.get(k, 0.0)) <= tol for pa, pb in zip(ca, cb) for k in set(pa) | set(pb))
    return "match" if ok else "mismatch"


# ---------------------------------------------------------------- solvents

_SOLVENT = {"dimethylformamide": "dmf", "nndimethylformamide": "dmf", "dimethylsulfoxide": "dmso", "cb": "chlorobenzene",
            "pcb": "chlorobenzene", "ipa": "isopropanol", "2propanol": "isopropanol", "iproh": "isopropanol",
            "etoh": "ethanol", "meoh": "methanol", "acn": "acetonitrile", "tol": "toluene", "gbl": "gammabutyrolactone",
            "ea": "ethylacetate", "dw": "water", "h2o": "water", "diw": "water", "dionizedwater": "water",
            "deionizedwater": "water", "di": "water"}
_NOISE = {"v", "vol", "volume", "anhydrous", "solvent", "solvents", "mixture", "and", "the", "of", "in", "ratio"}


def solvent_key(s) -> tuple[frozenset[str], tuple[float, ...]]:
    text = unicodedata.normalize("NFKC", str(s)).casefold()
    ratio = ()
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*[:/]\s*\d+(?:\.\d+)?)+)", text)
    if m:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", m[1])]
        ratio = tuple(round(n / nums[0], 2) for n in nums) if nums and nums[0] else ()
        text = text.replace(m[1], " ")
    toks = set()
    for t in re.split(r"[^a-z0-9\-]+", text):
        k = _SOLVENT.get(vkey(t), vkey(t))
        if k and k not in _NOISE and not k.isdigit():
            toks.add(k)
    return frozenset(toks), ratio


def solvent_status(a, b) -> str:
    (ta, ra), (tb, rb) = solvent_key(a), solvent_key(b)
    if ta == tb:
        return "match" if not ra or not rb or ra == rb else "mismatch"
    return "review" if ta <= tb or tb <= ta else "mismatch"


# ---------------------------------------------------------------- is the value in the paper at all?

def _norm_num(n: str) -> str:
    return n.rstrip("0").rstrip(".") if "." in n else n


def paper_text(pages: list[str]) -> tuple[str, set[str]]:
    """The paper reduced for ``likely_in_paper``: letters-and-digits text and the set of numbers it contains."""
    full = unicodedata.normalize("NFKC", " ".join(pages))
    full = re.sub(r"(?<=\d),(?=\d{3})", "", full)
    return vkey(full), {_norm_num(n) for n in re.findall(r"\d+(?:\.\d+)?", full)}


def likely_in_paper(value, text: tuple[str, set[str]]) -> bool:
    """Optimistic: a numeric cell if every number appears somewhere in the paper, a text cell if most of its
    words do. A value that is not in the PDF (a supplier, a spin recipe from the SI) cannot be extracted from it."""
    plain, numbers = text
    s = unicodedata.normalize("NFKC", str(value))
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", s)
    if not words:
        return bool(nums) and all(_norm_num(n) in numbers for n in nums)
    return sum(vkey(w) in plain for w in words) / len(words) >= 0.7 and all(_norm_num(n) in numbers for n in nums if "." in n)

