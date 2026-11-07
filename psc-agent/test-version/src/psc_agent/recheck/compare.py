"""Compare a recorded cell with the value re-extracted from the paper.

Numbers are compared after unit conversion and allow for rounding to either side's precision
plus a relative tolerance (looser for values read off plots). Text is compared after
normalisation; text that still differs is left for the LLM judge.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

_WORST_FIRST = ("mismatch", "unit_mismatch", "review", "match")


@dataclass(frozen=True)
class Verdict:
    status: str  # match | mismatch | unit_mismatch | review | not_found | missing_in_record
    detail: str = ""
    judgeable: bool = False  # a text difference the LLM judge may settle


# ---------------------------------------------------------------- units

_DIMS: dict[str, dict[str, float]] = {
    "length": {"nm": 1, "um": 1e3, "mm": 1e6, "cm": 1e7, "m": 1e9, "\u00c5": 0.1},
    "time": {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "h": 3600, "d": 86400},
    "molarity": {"M": 1, "mM": 1e-3, "uM": 1e-6, "mol/L": 1, "mmol/L": 1e-3, "mmol/mL": 1, "umol/mL": 1e-3},
    "mass_conc": {"g/mL": 1e3, "mg/mL": 1, "ug/mL": 1e-3, "g/L": 1, "mg/L": 1e-3},
    "amount": {"mol": 1, "mmol": 1e-3, "umol": 1e-6},
    "mass": {"g": 1e3, "mg": 1, "ug": 1e-3},
    "volume": {"L": 1e6, "mL": 1e3, "uL": 1, "nL": 1e-3},
    "spin": {"rpm": 1},
    "spin_accel": {"rpm/s": 1},
    "voltage": {"V": 1, "mV": 1e-3},
    "current_density": {"A/cm2": 1e3, "mA/cm2": 1, "uA/cm2": 1e-3, "A/m2": 0.1},
    "irradiance": {"W/cm2": 1e3, "mW/cm2": 1, "W/m2": 0.1},
    "power": {"kW": 1e3, "W": 1, "mW": 1e-3},
    "energy": {"eV": 1, "meV": 1e-3},
    "pressure": {"Pa": 1, "kPa": 1e3, "MPa": 1e6, "bar": 1e5, "mbar": 100, "Torr": 133.322, "mTorr": 0.133322},
    "percent": {"%": 1},
    "wt%": {"wt%": 1},
    "mol%": {"mol%": 1},
    "vol%": {"vol%": 1},
    "at%": {"at%": 1},
    "angle": {"deg": 1},
    "resistance": {"\u03a9": 1, "k\u03a9": 1e3, "M\u03a9": 1e6},
    "area_resistance": {"\u03a9cm2": 1, "k\u03a9cm2": 1e3},
    "sheet_resistance": {"\u03a9/sq": 1},
    "conductivity": {"S/cm": 1, "mS/cm": 1e-3, "S/m": 1e-2},
    "density": {"1/cm3": 1, "1/m3": 1e-6},
    "mobility": {"cm2/Vs": 1, "m2/Vs": 1e4},
    "rate": {"\u00c5/s": 1, "nm/s": 10},
    "speed": {"mm/s": 1},
}
# unit -> (dimension, factor to the dimension's base unit, offset added after scaling)
_UNITS: dict[str, tuple[str, float, float]] = {
    u: (dim, float(f), 0.0) for dim, units in _DIMS.items() for u, f in units.items()
}
_UNITS.update({"C": ("temperature", 1.0, 0.0), "K": ("temperature", 1.0, -273.15),
               "F": ("temperature", 5 / 9, -160 / 9)})
_by_lower: dict[str, set] = defaultdict(set)
for _u, _spec in _UNITS.items():
    _by_lower[_u.lower()].add(_spec)
_LOWER = {k: next(iter(v)) for k, v in _by_lower.items() if len(v) == 1}  # "RPM", "ml"; not "mm"/"mM"
_ATOMS = {"mA", "A", "uA", "W", "mW", "g", "mg", "ug", "mol", "mmol", "umol", "nm", "um", "mm", "cm", "m",
          "L", "mL", "uL", "s", "V", "S", "mS", "eV", "\u03a9", "k\u03a9"}

_SUBS = [
    (r"\([^)]*\)", " "),  # "M (0.5 mmol in 5 mL)" -> "M"
    (r"[\u03bc\u00b5\u039c]", "u"),  # micro sign, Greek mu, and the Greek capital Mu typo
    (r"[\u2212\u2013\u2014]", "-"),
    (r"(?i)\bdeg(?:rees?)?\s*C\b|[\u00b0\u00ba]\s*C\b|\bcelsius\b", "C"),
    (r"[\u00b0\u00ba]|(?i:\bdeg(?:rees?)?\b)", " deg "),
    (r"(?i)\b(k?)ohms?\b", lambda m: ("k" if m.group(1) else "") + "\u03a9"),
    (r"(?i)\bsec(?:ond)?s?\b", "s"),
    (r"(?i)\bmin(?:ute)?s?\b", "min"),
    (r"(?i)\b(?:hrs?|hours?)\b", "h"),
    (r"(?i)\bdays?\b", "d"),
    (r"(?i)%\s*v/v|\bv/v\s*%|\bvol\.?\s*%", "vol%"),
    (r"(?i)%\s*w/w|\bw/w\s*%|\bwt\.?\s*%", "wt%"),
    (r"(?i)\bmol\.?\s*%", "mol%"),
    (r"(?i)\bat\.?\s*%", "at%"),
    (r"[\u00b7\u22c5*^]", " "),
]
_FACTOR = re.compile(r"^([^\d+\-]+?)(-?\d+)?$")


def canonical_unit(unit) -> str | None:
    """``mg mL-1`` / ``mg/ml`` -> ``mg/mL``; ``mAcm-2`` -> ``mA/cm2``; unparseable text is squashed."""
    if unit is None:
        return None
    s = unicodedata.normalize("NFKC", str(unit))
    for pat, rep in _SUBS:
        s = re.sub(pat, rep, s)
    s = s.strip()
    if not s:
        return None
    num_part, _, den_part = s.partition("/")
    num: list[str] = []
    den: list[str] = []
    for part, in_den in ((num_part, False), (den_part, True)):
        for f in part.split():
            m = _FACTOR.match(f)
            if not m:
                return re.sub(r"\s+", "", s)
            base, exp = m.group(1), int(m.group(2) or 1)
            if in_den:
                exp = -abs(exp)  # "mA/cm-2" is a sloppy "mA/cm2"
            factors = [(base, exp)]
            if base not in _UNITS and base.lower() not in _LOWER:  # glued: "mAcm-2", "mgL-1", "/Vs"
                for i in range(1, len(base)):
                    if base[:i] in _ATOMS and base[i:] in _ATOMS:
                        factors = [(base[:i], -1 if in_den else 1), (base[i:], exp)]
                        break
            for b, e in factors:
                (num if e > 0 else den).append(b + (str(abs(e)) if abs(e) != 1 else ""))
    return ("".join(num) or "1") + ("/" + "".join(den) if den else "")


def unit_spec(unit) -> tuple[str, float, float] | None:
    c = canonical_unit(unit)
    if c is None:
        return None
    return _UNITS.get(c) or _LOWER.get(c.lower())


# ---------------------------------------------------------------- numbers

_NUM = r"(?:\d+(?:\.\d*)?|\.\d+)(?:\s*(?:[eE]\s*[-+]?\d+|[x\u00d7]\s*10\s*\^?\s*[-+]?\d+))?"
_QTY = re.compile(
    rf"^(?P<q>~|\u2248|<|>|\u2264|\u2265|ca\.?|approx\.?)?\s*(?P<a>-?\s*{_NUM})"
    rf"(?:\s*(?:-|~|to)\s*(?P<b>-?\s*{_NUM}))?"
    rf"(?:\s*(?:\u00b1|\+/-)\s*{_NUM})?"
    rf"\s*(?P<unit>[^\d\s,;:/\-].*?)?\s*$",  # a unit never starts with - : / ("2-propanol", "50:50")
    re.I,
)
_FLOAT = re.compile(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))(?:[eE]([-+]?\d+)|[x\u00d7]10\^?([-+]?\d+))?$")
_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_BLANK = {"", "-", "n/a", "na", "none", "null", "nan", "nr", "not reported", "not given"}


@dataclass(frozen=True)
class Quantity:
    lo: float
    hi: float
    unit: str | None
    decimals: int | None  # digits after the point; None for scientific notation

    def __str__(self) -> str:
        v = f"{self.lo:g}" if self.lo == self.hi else f"{self.lo:g}-{self.hi:g}"
        return f"{v} {self.unit}" if self.unit else v


def _number(s: str) -> tuple[float, int | None]:
    m = _FLOAT.match(re.sub(r"\s+", "", s))
    mant, exp = m.group(1), m.group(2) or m.group(3)
    v = float(mant) * (10 ** int(exp) if exp else 1)
    if exp:
        return v, None
    return v, len(mant.split(".")[1]) if "." in mant else 0


def _prep(v) -> str:
    s = unicodedata.normalize("NFKC", str(v)).replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    s = s.strip()
    return s.replace(",", "") if _THOUSANDS.match(s) else s


def parse_quantities(value, unit=None) -> list[Quantity] | None:
    """``"3000, 40"`` + ``"RPM, s"`` -> two quantities; None if the cell is not purely numeric."""
    tokens = [t.strip() for t in re.split(r"[,;]", _prep(value)) if t.strip()]
    units = [u.strip() for u in re.split(r"[,;]", str(unit)) if u.strip()] if unit is not None else []
    if not tokens or (units and len(units) not in (1, len(tokens))):
        return None
    out = []
    for i, t in enumerate(tokens):
        m = _QTY.match(t)
        if not m:
            return None
        inline = m.group("unit")
        if inline and unit_spec(inline) is None and (len(inline) > 12 or " " in inline.strip()):
            return None  # prose such as "3000 rpm for 30 s"
        lo, d1 = _number(m.group("a"))
        hi, d2 = _number(m.group("b")) if m.group("b") else (lo, d1)
        u = inline or (units[i] if len(units) == len(tokens) else units[0] if units else None)
        out.append(Quantity(lo, hi, u, None if d1 is None or d2 is None else min(d1, d2)))
    return out


# ---------------------------------------------------------------- comparison

def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().casefold() in _BLANK)


def _norm_text(s) -> str:
    t = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", str(s)).casefold())
    return {"y": "yes", "true": "yes", "n": "no", "false": "no"}.get(t, t)


def _half_step(d: int | None) -> float | None:
    return None if d is None else 0.5 * 10.0 ** -d * (1 + 1e-9)


def _close(r: float, p_in_r: float, r_in_p: float, p: float, r_dec, p_dec, tol: float) -> bool:
    """Equal within tol, or equal once rounded to the coarser side's precision."""
    if abs(r - p_in_r) <= tol * max(abs(r), abs(p_in_r)):
        return True
    hr, hp = _half_step(r_dec), _half_step(p_dec)
    return (hr is not None and abs(r - p_in_r) <= hr) or (hp is not None and abs(r_in_p - p) <= hp)


def _compare_pair(r: Quantity, p: Quantity, tol: float) -> Verdict:
    rs, ps = unit_spec(r.unit), unit_spec(p.unit)
    rc, pc = canonical_unit(r.unit), canonical_unit(p.unit)
    if rs and ps and rs[0] == ps[0]:
        to_r = lambda v: (v * ps[1] + ps[2] - rs[2]) / rs[1]  # noqa: E731
        to_p = lambda v: (v * rs[1] + rs[2] - ps[2]) / ps[1]  # noqa: E731
        comparable = True
    else:
        to_r = to_p = lambda v: v  # noqa: E731
        comparable = rc == pc
    close = all(_close(a, to_r(b), to_p(a), b, r.decimals, p.decimals, tol)
                for a, b in ((r.lo, p.lo), (r.hi, p.hi)))
    pair = f"recorded {r} vs paper {p}"

    if (r.lo != r.hi) != (p.lo != p.hi):  # range against a single value, judged in recorded units
        if comparable:
            lo, hi, x = (r.lo, r.hi, to_r(p.lo)) if r.lo != r.hi else (to_r(p.lo), to_r(p.hi), r.lo)
            if lo <= x <= hi:
                return Verdict("review", f"range vs single value: {pair}")
        return Verdict("mismatch", pair)
    if comparable:
        return Verdict("match") if close else Verdict("mismatch", pair)
    if rc is None:
        return Verdict("unit_mismatch", f"unit missing in record: {pair}") if close else Verdict("mismatch", pair)
    if pc is None:
        return Verdict("match", "paper unit not given") if close else Verdict("mismatch", pair)
    if rs and ps:  # both understood, different dimensions
        return Verdict("unit_mismatch", f"same number, different unit: {pair}") if close else Verdict("mismatch", pair)
    return Verdict("review", f"units not comparable: {pair}", judgeable=True)


def _align(rq: list[Quantity], pq: list[Quantity]) -> list[tuple[Quantity, Quantity]]:
    """Pair values in order, unless reordering by unit dimension makes every pair agree."""
    dim = lambda q: (unit_spec(q.unit) or (None,))[0]  # noqa: E731
    if all(dim(a) == dim(b) for a, b in zip(rq, pq)):
        return list(zip(rq, pq))
    rest = list(pq)
    pairs = []
    for a in rq:
        b = next((b for b in rest if dim(b) is not None and dim(b) == dim(a)), None)
        if b is None:
            return list(zip(rq, pq))
        rest.remove(b)
        pairs.append((a, b))
    return pairs


def compare_values(recorded, recorded_unit, paper, paper_unit, *, tol: float) -> Verdict | None:
    """Verdict for one cell, or None when neither side has a value."""
    r_blank, p_blank = _blank(recorded), _blank(paper)
    if r_blank and p_blank:
        return None
    if p_blank:
        return Verdict("not_found", "no value found in the paper")
    if r_blank:
        return Verdict("missing_in_record", "paper reports a value, record is empty")

    rq, pq = parse_quantities(recorded, recorded_unit), parse_quantities(paper, paper_unit)
    if rq and pq:
        if len(rq) != len(pq):
            return Verdict("mismatch", f"{len(rq)} recorded value(s) vs {len(pq)} in paper")
        verdicts = [_compare_pair(a, b, tol) for a, b in _align(rq, pq)]
        worst = min(verdicts, key=lambda v: _WORST_FIRST.index(v.status)).status
        same = [v for v in verdicts if v.status == worst]
        return Verdict(worst, "; ".join(v.detail for v in same if v.detail), any(v.judgeable for v in same))

    r_txt = f"{recorded} {recorded_unit or ''}"
    p_txt = f"{paper} {paper_unit or ''}"
    if _norm_text(r_txt) == _norm_text(p_txt) or (
        _norm_text(recorded) == _norm_text(paper) and canonical_unit(recorded_unit) == canonical_unit(paper_unit)
    ):
        return Verdict("match")
    return Verdict("review", "text differs", judgeable=True)
