"""ตัวตีความค่าที่เขียนด้วยมือ -- พอร์ตตรงจาก column_prepare ช่วง 2

⚠ ข้อความใน note ทุกอันถูกเขียนลง CSV จริง (คอลัมน์ *_note ของ prepared/)
   แก้ข้อความ = ไฟล์ผลลัพธ์เปลี่ยน แล้ว `verify` จะไม่ผ่าน -- ห้ามแก้ถ้าไม่ได้ตั้งใจ

⚠ is_num มี 2 แบบ เพราะ notebook นิยาม `is_num` ซ้ำกลางทาง (cell 36)
   cell 23-35 ใช้แบบ regex · cell 36 เป็นต้นไปใช้แบบ float()
   สองแบบให้ผลต่างกันจริง เช่น "1e5" regex รับ (มี [eE] ในแพตเทิร์น) แต่ " nan " float รับ regex ไม่รับ
   -> ต้องเรียกให้ตรงจุดเดิม ไม่งั้นผลเพี้ยนเงียบ ๆ
"""

from __future__ import annotations

import re
from math import gcd

import pandas as pd

CONFIG = {
    "RT_CELSIUS": 25.0,
    "ANGSTROM_PER_NM": 10.0,
    "RANGE_REPRESENTATIVE": "mid",
}

NUM = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"

# สัญกรณ์ยกกำลังที่เขียนด้วยมือ "1.01×10-3" / "1.01 x 10^-3"
SCI_RE = re.compile(rf"({NUM})\s*[×xX·]\s*10\s*\^?\s*([+-]?\d+)")

UNICODE_FIX = str.maketrans({chr(0x03BC): chr(0x00B5),   # Greek mu -> micro sign
                             chr(0x2212): "-",           # Unicode minus -> hyphen
                             chr(0x00A0): " "})          # NBSP -> space

BAD_CHARS_RE = (chr(0x03BC) + chr(0x2212) + chr(0x00A0)).join(["[", "]"])


# ---------------------------------------------------------------- ตัวเลขหรือไม่

def is_num_re(s) -> bool:
    """แบบ cell 23 ของ notebook -- ต้องเป็นสตริงที่ match NUM ทั้งตัว"""
    return isinstance(s, str) and re.fullmatch(NUM, s.strip()) is not None


def is_num_float(val) -> bool:
    """แบบ cell 36 ของ notebook -- float() ผ่านก็พอ"""
    try:
        float(val)
        return True
    except (TypeError, ValueError):
        return False


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(UNICODE_FIX)).strip()


def variant_key(s: str) -> str:
    return re.sub(r"[\W_]", "", s.lower())


def to_num_sci(v):
    """แปลงเป็น float รองรับสัญกรณ์ยกกำลังเขียนมือ -- คืน (ค่า, note)"""
    if not isinstance(v, str):
        return pd.to_numeric(v, errors="coerce"), ""
    s = v.strip()
    m = SCI_RE.fullmatch(s)
    if m:
        x = float(m[1]) * 10 ** int(m[2])
        return x, f"สัญกรณ์ยกกำลัง: {s} -> {x:g}"
    return pd.to_numeric(s, errors="coerce"), ""


def parse_property_value(v):
    """properties.value -- ตัวเลขปกติ + สัญกรณ์ยกกำลัง + ช่วงค่า + ค่าขอบ

    คืน (ค่า, note) เหมือน to_num_sci แต่รองรับรูปแบบที่เปเปอร์เขียนจริงเพิ่ม:
      "0.18-1.2"   -> ค่ากลาง (กติกาเดียวกับความหนาและความชื้น)
      ">200"       -> 200 พร้อม note ว่าเป็นขอบล่าง
      "up to 500"  -> 500 พร้อม note ว่าเป็นขอบบน
    ⚠ ไม่แปลงหน่วย -- ค่าที่ได้อยู่ในหน่วยที่เปเปอร์เขียนไว้ (µm ยังเป็น µm)
    """
    x, note = to_num_sci(v)
    if not isinstance(v, str) or pd.notna(x):
        return x, note
    s = v.strip()
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return ((a + b) / 2,
                f"ช่วงค่า: ใช้ค่ากลาง {(a + b) / 2:g} (min={a:g}, max={b:g})")
    m = re.fullmatch(rf"(>=|≥|>)\s*({NUM})", s)
    if m:
        x = float(m[2])
        return (x, f"ค่าขอบล่าง {m[1]}{x:g}: ใช้ {x:g} -- ค่าจริงสูงกว่านี้ ห้ามใช้เป็นค่าวัด")
    m = re.fullmatch(rf"(<=|≤|<)\s*({NUM})", s)
    if m:
        x = float(m[2])
        return (x, f"ค่าขอบบน {m[1]}{x:g}: ใช้ {x:g} -- ค่าจริงต่ำกว่านี้ ห้ามใช้เป็นค่าวัด")
    m = re.fullmatch(rf"up to\s+({NUM})", s, re.I)
    if m:
        x = float(m[1])
        return (x, f"ค่าขอบบน 'up to {x:g}': ใช้ {x:g} -- ค่าจริงต่ำกว่านี้ ห้ามใช้เป็นค่าวัด")
    return (x, note)


# ---------------------------------------------------------------- thickness

def parse_thickness(v):
    """คืน (parsed, vmin, vmax, note) -- note ว่าง = ตัวเลขปกติไม่ต้องตีความ

    ใช้ is_num_re ตาม notebook (ฟังก์ชันนี้ถูกเรียกที่ cell 28 ก่อน is_num ถูกนิยามใหม่)
    """
    if not isinstance(v, str) or not v.strip():
        return (None, None, None, "")
    s = v.strip()
    if is_num_re(s):
        x = float(s)
        return (x, x, x, "")
    m = re.fullmatch(rf"({NUM})\s*Å", s)
    if m:
        x = float(m[1]) / CONFIG["ANGSTROM_PER_NM"]
        return (x, x, x, f"แปลงหน่วย: {s} = {x:g} nm (1 Å = 0.1 nm)")
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return ((a + b) / 2, a, b,
                f"ช่วงค่า: ใช้ค่ากลาง {(a + b) / 2:g} (min={a:g}, max={b:g})")
    m = re.fullmatch(rf"({NUM})\s*/\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return (a + b, min(a, b), max(a, b),
                f"ชั้นซ้อนคั่นด้วย /: ผลรวม {a + b:g} nm (ชั้นย่อย {a:g} + {b:g})")
    m = re.fullmatch(rf"({NUM})\s*,\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return (a + b, min(a, b), max(a, b),
                f"หลายชั้นย่อยในเซลล์เดียว: ผลรวม {a + b:g} nm (ชั้นย่อย {a:g} + {b:g})")
    return (None, None, None, f"parse ไม่ได้: {s!r}")


# ---------------------------------------------------------------- stability

def parse_temperature(v):
    if not isinstance(v, str) or not v.strip():
        return (None, "")
    s = v.strip()
    if is_num_float(s):
        return (float(s), "")
    if s.upper() == "RT":
        return (CONFIG["RT_CELSIUS"],
                f"RT (room temperature) -> {CONFIG['RT_CELSIUS']:g} C [assumption: CONFIG]")
    m = re.fullmatch(rf"({NUM})\s*±\s*({NUM})", s)
    if m:
        c, t = float(m[1]), float(m[2])
        return (c, f"ค่ากลาง±คลาดเคลื่อน: ใช้ {c:g} C (±{t:g})")
    return (None, f"parse ไม่ได้: {s!r}")


def parse_range_pm(v):
    """humidity: ตัวเลข | ช่วง a-b | c ± t | censored -> (parsed, min, max, note)"""
    if not isinstance(v, str) or not v.strip():
        return (None, None, None, "")
    s = v.strip()
    if is_num_float(s):
        x = float(s)
        return (x, x, x, "")
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return ((a + b) / 2, a, b,
                f"ช่วงค่า: ใช้ค่ากลาง {(a + b) / 2:g} (min={a:g}, max={b:g})")
    m = re.fullmatch(rf"({NUM})\s*±\s*({NUM})", s)
    if m:
        c, t = float(m[1]), float(m[2])
        return (c, c - t, c + t,
                f"ค่ากลาง±คลาดเคลื่อน: {c:g} (min={c - t:g}, max={c + t:g})")
    m = re.fullmatch(rf"([<>])\s*({NUM})", s)
    if m:
        x = float(m[2])
        lo, hi = (0.0, x) if m[1] == "<" else (x, None)
        return (x, lo, hi,
                f"ค่า censored {m[1]}{x:g}: ใช้ {x:g} เป็นขอบ -- ห้ามใช้เป็นค่าจริงโดยไม่ดูขอบเขต")
    return (None, None, None, f"parse ไม่ได้: {s!r}")


def parse_censored(v):
    """retention / t80: ตัวเลข | '< x' -> (parsed, censor, note)"""
    if not isinstance(v, str) or not v.strip():
        return (None, "", "")
    s = v.strip()
    if is_num_float(s):
        return (float(s), "", "")
    m = re.fullmatch(rf"([<>])\s*({NUM})", s)
    if m:
        return (float(m[2]), m[1],
                f"ค่า censored: รู้แค่ว่า {'ต่ำ' if m[1] == '<' else 'สูง'}กว่า {m[2]} "
                f"— ห้ามใช้เป็นค่าจริงโดยไม่ดูธง censor")
    return (None, "", f"parse ไม่ได้: {s!r}")


# ---------------------------------------------------------------- vendors.purity

def parse_purity(v):
    """คืน (pct, qualifier, kind, note)"""
    if not isinstance(v, str) or not v.strip() or v.strip() == "Not provided":
        return (None, "", "missing", "")
    s = v.strip()
    if is_num_float(s):
        return (float(s), "=", "percent", "")
    m = re.fullmatch(rf"([>≥])\s*({NUM})", s)
    if m:
        return (float(m[2]), m[1], "percent_lower_bound",
                f"qualifier {m[1]}: ความบริสุทธิ์อย่างน้อย {m[2]}%")
    m = re.fullmatch(rf"({NUM})\s*%", s)
    if m:
        return (float(m[1]), "=", "percent", f"ตัด '%' ออกจาก {s!r}")
    m = re.fullmatch(rf"([>≥])\s*({NUM})\s*%", s)
    if m:
        return (float(m[2]), m[1], "percent_lower_bound",
                f"qualifier {m[1]} + ตัด '%': ความบริสุทธิ์อย่างน้อย {m[2]}%")
    # เครื่องหมายที่พิมพ์ด้วยคีย์บอร์ดธรรมดา ">=" "<=" -- ความหมายเดียวกับ ≥ ≤
    #   (ยืนยันกับเปเปอร์ d5ya00072f: "nickel nitrate hexahydrate (Sigma-Aldrich, ≥97%)")
    m = re.fullmatch(rf"(>=|≥|<=|≤)\s*({NUM})\s*%?", s)
    if m:
        q = {">=": "≥", "<=": "≤"}.get(m[1], m[1])
        bound = "percent_lower_bound" if q == "≥" else "percent_upper_bound"
        word = "อย่างน้อย" if q == "≥" else "ไม่เกิน"
        return (float(m[2]), q, bound, f"qualifier {q}: ความบริสุทธิ์{word} {m[2]}%")
    if s.lower() == "anhydrous":
        return (None, "", "grade_descriptor",
                "เกรดตัวทำละลาย 'ปราศจากน้ำ' — ไม่ใช่ตัวเลขความบริสุทธิ์")
    # ต้องเช็ค wt% ก่อนช่วงค่าเปล่า ๆ: "25-28 wt%" ของสารละลาย NH3 คือ *ความเข้มข้น*
    #   ไม่ใช่ความบริสุทธิ์ (ชนิดเดียวกับ "57 wt%" และ "20 wt") -- ปล่อยให้เข้ากฎช่วงค่า
    #   จะได้ purity_pct = 26.5 ซึ่งอ่านผิดความหมาย
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})\s*wt\s*%?", s)
    if m:
        return (None, "", "concentration_wt_pct",
                f"ความเข้มข้น {m[1]}-{m[2]} wt% ของสารละลาย — ไม่ใช่ purity (ข้อมูลผิดชนิด)")
    m = re.fullmatch(rf"({NUM})\s*wt\s*%?", s)
    if m:
        return (None, "", "concentration_wt_pct",
                f"ความเข้มข้น {m[1]} wt% ของ dispersion — ไม่ใช่ purity (ข้อมูลผิดชนิด)")
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})\s*%?", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return ((a + b) / 2, "~", "percent_range",
                f"ช่วงความบริสุทธิ์ {a:g}-{b:g}% ใช้ค่ากลาง {(a + b) / 2:g}%")
    # notebook เขียนเงื่อนไขนี้ซ้ำสองครั้งด้วยอักขระเดียวกัน (U+03A9 ทั้งคู่) แม้คอมเมนต์จะบอก U+2126
    # -> ของจริงจึงไม่เคยจับ U+2126 · คงพฤติกรรมเดิมไว้ ถ้าวันหนึ่งเจอ U+2126 จริง
    #    ค่าจะตกเป็น "unrecognized" แล้วโผล่ใน review queue เอง (ไม่หายเงียบ)
    if "Ω" in s or "□" in s:
        return (None, "", "sheet_resistance_misfiled",
                f"sheet resistance ของ substrate ({s}) ถูกกรอกในช่อง purity "
                f"— Ω ในไฟล์คือ U+2126 OHM SIGN")
    return (None, "", "unrecognized", f"จำแนกไม่ได้: {s!r}")


# ---------------------------------------------------------------- materials.amount

def parse_amount(v, u):
    """คืน (parsed, ratio, note)"""
    if not isinstance(v, str) or not v.strip():
        return (None, None, "")
    s = v.strip()
    if is_num_float(s):
        return (float(s), None, "")
    m = re.fullmatch(rf"({NUM})\s*[:/]\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return (None, round(a / b, 6),
                f"อัตราส่วน {s} (หน่วย {u!r}) ไม่ใช่ปริมาณสเกลาร์ "
                f"-- เก็บเป็น amount_ratio = {a / b:g}")
    m = re.fullmatch(rf"({NUM})\s*[-–]\s*({NUM})", s)
    if m:
        a, b = float(m[1]), float(m[2])
        return ((a + b) / 2, None,
                f"ช่วงค่า: ใช้ค่ากลาง {(a + b) / 2:g} (min={a:g}, max={b:g})")
    return (None, None, f"parse ไม่ได้: {s!r}")


# ---------------------------------------------------------------- material family

def material_family(x):
    s = str(x).lower().strip()
    if "nio" in s:
        return "NiOx"
    if re.search(r"\bc60\b", s):
        return "C60"
    if "bcp" in s:
        return "BCP"
    if "pbi2" in s:
        return "PbI2"
    if "pedot" in s:
        return "PEDOT:PSS"
    if "spiro" in s:
        return "Spiro-OMeTAD"
    if any(k in s for k in ["pacz", "2pacz", "me-2pacz", "mecz"]):
        return "SAM (xPACz)"
    if any(k in s for k in ["pcbm", "pc61bm", "pc60bm"]):
        return "PCBM"
    if "sno2" in s or "sno" == s:
        return "SnO2"
    if re.search(r"\b[if]to\b", s):
        return "TCO"
    if s in ("ag", "au", "cu", "cr", "cr + au", "cu target"):
        return "Metal electrode"
    if any(k in s for k in ["peai", "pdi", "pei"]):
        return "Amine passivation"
    return None


# ---------------------------------------------------------------- solvent

SOLVENT_CANON = {
    "dmf": "DMF", "dmso": "DMSO",
    "cb": "Chlorobenzene", "chlorobenzene": "Chlorobenzene",
    # 2-propanol / isopropanol / IPA คือสารตัวเดียวกัน
    "ipa": "IPA", "anhydrousipa": "IPA", "isopropanol": "IPA",
    "2propanol": "IPA", "propan2ol": "IPA", "isopropylalcohol": "IPA",
    "ethanol": "Ethanol", "anhydrousethanol": "Ethanol",
    "ethylalcohol": "Ethanol", "etoh": "Ethanol",
    "methanol": "Methanol",
    "h2o": "Water", "water": "Water", "deionizedwater": "Water",
    "deionisedwater": "Water", "diwater": "Water",
    "acetonitrile": "Acetonitrile",
    "nmp": "NMP", "nmethyl2pyrrolidone": "NMP",
    "2me": "2-Methoxyethanol",
    "ethyleneglycol": "Ethylene glycol",
    "ethanolamine": "Ethanolamine",
    "ethylenediamine": "Ethylenediamine",          # คู่กับ ethylene glycol ในน้ำหมึก NiOx
    "dcb": "Dichlorobenzene", "odcb": "Dichlorobenzene",
    "dichlorobenzene": "Dichlorobenzene", "orthodichlorobenzene": "Dichlorobenzene",
    "octane": "Octane",                            # ใช้กระจายผลึกควอนตัมดอต CsPbI3
    "2methoxyethanol": "2-Methoxyethanol", "methoxyethanol": "2-Methoxyethanol",
    "toluene": "Toluene", "ethylacetate": "Ethyl acetate",
}
NOISE_WORDS = {"stock", "solution", "anhydrous", "solvent"}


def canon_solvent(tok):
    k = re.sub(r"[^a-z0-9]", "", str(tok).lower())
    k = re.sub(r"^(de|di)ionized", "deionized", k)
    if k in SOLVENT_CANON:
        return SOLVENT_CANON[k]
    for w in re.split(r"\s+", str(tok).strip()):
        kw = re.sub(r"[^a-z0-9]", "", w.lower())
        if kw in NOISE_WORDS:
            continue
        if kw in SOLVENT_CANON:
            return SOLVENT_CANON[kw]
    return None


def parse_solvent(v):
    """คืน dict: solvent_1, solvent_2, solvent_ratio, solvent_system, solvent_note"""
    out = {"solvent_1": None, "solvent_2": None, "solvent_ratio": None,
           "solvent_system": None, "solvent_note": ""}
    if not isinstance(v, str) or not v.strip():
        return out
    s = v.strip()

    ratio_txt, head = None, s
    m = re.search(r"\(([^)]*)\)", s)
    if m:
        head, ratio_txt = s[:m.start()].strip(), m.group(1)

    # "+" ก็เป็นตัวคั่นเหมือน / : , -- ไม่งั้น "Acetonitrile + Ethanol" จะเหลือแค่ตัวแรก
    toks = [t for t in re.split(r"[/:,+]| and ", head) if t.strip()]
    names = [canon_solvent(t) for t in toks]
    if len(names) == 1 and names[0] is None:
        k = re.sub(r"[^a-z0-9]", "", head.lower())
        for a in ("dmf", "dmso"):
            for b in ("dmf", "dmso"):
                if a != b and k == a + b:
                    names = [SOLVENT_CANON[a], SOLVENT_CANON[b]]
    names = [n for n in names if n]
    if not names:
        out["solvent_note"] = f"จำแนกชื่อไม่ได้: {s!r}"
        return out

    r1 = r2 = None
    if ratio_txt:
        nums = re.findall(r"\d+(?:\.\d+)?", ratio_txt)
        if len(nums) >= 2 and not re.search(r"mg|mol|wt", ratio_txt, re.I):
            r1, r2 = float(nums[0]), float(nums[1])
        elif nums:
            out["solvent_note"] = f"วงเล็บไม่ใช่อัตราส่วน: ({ratio_txt})"

    if len(names) == 1:
        out.update(solvent_1=names[0], solvent_system=names[0])
        return out

    s1, s2 = names[0], names[1]
    if s1 > s2:
        s1, s2 = s2, s1
        r1, r2 = (r2, r1) if r1 is not None else (None, None)
    out["solvent_1"], out["solvent_2"] = s1, s2
    if r1 and r2:
        out["solvent_ratio"] = round(r1 / r2, 3)
        a, b = r1, r2
        if float(a).is_integer() and float(b).is_integer():
            gg = gcd(int(a), int(b))
            a, b = int(a) // gg, int(b) // gg
        else:
            a, b = round(r1 / r2, 2), 1
        out["solvent_system"] = f"{s1}:{s2} ({a}:{b})"
    else:
        out["solvent_system"] = f"{s1}:{s2}"
    return out


# ---------------------------------------------------------------- properties

def metric_of(name):
    n = str(name).lower()
    for k, b in [("voc", "Voc"), ("jsc", "Jsc"), ("mobility", "mobility")]:
        if k in n:
            return b
    return "other"


def decompose(name):
    """ตัด stat -> scan -> layer -> carrier ออกจากชื่อ เหลือ base

    แถวที่ไม่ได้กรอกชื่อ property (ว่าง/NaN) คืน base ว่าง แล้วให้ด่านตรวจของ stage2
    หยิบไปขึ้น review -- เดาชื่อแทนไม่ได้ และจะปล่อยให้ pipeline ตายก็ไม่ได้
    """
    if not isinstance(name, str) or not name.strip():
        return "", "", "", "", ""
    base, stat, scan, layer, carrier = name, "", "", "", ""
    m = re.match(r"^(Champion|Average|Avg|Certified|Verified)\s+(.+)$", base)
    if m:
        stat, base = m[1].lower(), m[2]
    m = re.match(r"^(.+?)\s*\((RS|FS)\)$", base)
    if m:
        base, scan = m[1], m[2]
    m = re.match(r"^(HTL|PSK|PVK)\s+(.+)$", base)
    if m:
        layer, base = m[1], m[2]
    else:
        m = re.match(r"^(.+?)\s*\((HTL|PVK|PSK)\)$", base)
        if m:
            base, layer = m[1], m[2]
    layer = {"PVK": "PSK"}.get(layer, layer)
    m = re.match(r"^(Electron|Hole)\s+(.+)$", base)
    if m:
        carrier, base = m[1].lower(), m[2]
    return base, stat, scan, layer, carrier


# ---------------------------------------------------------------- stability env

STRESS_RULES = [
    (r"isos-l|mpp|illumin|light|1-sun", "light/operational"),
    (r"isos-t|thermal|damp heat", "thermal"),
    (r"isos-d|storage|dark", "dark storage"),
    (r"n2|nitrogen|glove", "inert (N2)"),
    (r"air|ambient", "ambient air"),
]


def stress_group(v):
    if not isinstance(v, str) or not v.strip():
        return None
    t = v.lower()
    for pat, name in STRESS_RULES:
        if re.search(pat, t):
            return name
    return "other"


# ---------------------------------------------------------------- PSK composition

CATIONS = ["FA", "MA", "Cs", "Rb", "Pb", "Sn", "Br", "Cl", "I", "K"]
TOKEN = re.compile("(" + "|".join(CATIONS) + r")(\d*\.?\d+)")

MW = {"CsI": 259.81, "CsBr": 212.81, "FAI": 171.97, "MAI": 158.97, "MABr": 111.97,
      "MACl": 67.52, "PbI2": 461.01, "PbBr2": 367.01, "PbCl2": 278.11}
ION = {"CsI": {"A": {"Cs": 1}, "X": {"I": 1}}, "CsBr": {"A": {"Cs": 1}, "X": {"Br": 1}},
       "FAI": {"A": {"FA": 1}, "X": {"I": 1}}, "MAI": {"A": {"MA": 1}, "X": {"I": 1}},
       "MABr": {"A": {"MA": 1}, "X": {"Br": 1}}, "MACl": {"A": {"MA": 1}, "X": {"Cl": 1}},
       "PbI2": {"B": {"Pb": 1}, "X": {"I": 2}}, "PbBr2": {"B": {"Pb": 1}, "X": {"Br": 2}},
       "PbCl2": {"B": {"Pb": 1}, "X": {"Cl": 2}}}
MOLAR = {"m": 1.0, "mol": 1.0, "m total": 1.0, "mmol": 0.001}
MASS = {"mg": 0.001, "g": 1.0, "g / 2ml": 1.0}
VOLATILE = {"MACl"}


def site_from_str(part):
    """อ่านสัดส่วนไอออน รองรับวงเล็บที่มีตัวคูณต่อท้าย เช่น (FA0.83MA0.17)0.95"""
    out = {}
    for inner, scale in re.findall(r"\(([^)]*)\)\s*(\d*\.?\d+)?", part):
        k = float(scale) if scale else 1.0
        for ion, frac in TOKEN.findall(inner):
            out[ion] = out.get(ion, 0) + float(frac) * k
    rest = re.sub(r"\([^)]*\)\s*\d*\.?\d*", "", part)
    for ion, frac in TOKEN.findall(rest):
        out[ion] = out.get(ion, 0) + float(frac)
    return out


ADDITIVE_RE = re.compile(r"\(\s*with\b[^)]*\)", re.I)


def strip_additives(f) -> str:
    """ตัดสารเติมแต่งที่เขียนต่อท้ายสูตรออก ก่อนอ่านสัดส่วนไอออน

    ⚠ ถ้าไม่ตัด ข้อความหลัง + จะถูกนับเข้าสัดส่วนด้วย เช่น
      "Cs0.25MA0.03FA0.72PbI2.73Br0.27 + 10% MACl + 10% PbCl2"
      -> "Cl2" ของ PbCl2 เข้าไปอยู่ X-site ทำให้ x_Br = 0.054 แทนที่จะเป็น 0.09 (คลาด 40%)
    สารเติมแต่งเป็นสิ่งที่เติมลงน้ำหมึก ไม่ใช่ส่วนหนึ่งของสูตรผลึก จึงไม่ควรเข้าสัดส่วน
    """
    s = re.split(r"\s*\+", str(f))[0]
    return ADDITIVE_RE.sub("", s).strip()


def composition_from_formula(f):
    """คืน (A_dict, X_dict, b_site) -- None ถ้าไม่มีตัวเลขสัดส่วนให้แตก"""
    s = strip_additives(f).strip("[]").strip()
    m = re.search(r"(Pb|Sn)", s)
    if not m:
        return None
    A, X = site_from_str(s[:m.start()]), site_from_str(s[m.end():])
    sA, sX = sum(A.values()), sum(X.values())
    if not sA or not sX:
        return None
    return ({k: v / sA for k, v in A.items()}, {k: v / sX for k, v in X.items()}, m.group(1))


def canon_precursor(s):
    k = re.sub(r"[^a-z0-9]", "", str(s).lower())
    for n in MW:
        if re.sub(r"[^a-z0-9]", "", n.lower()) == k:
            return n
    return None


def unit_family(u):
    u = str(u).strip().lower()
    return "molar" if u in MOLAR else ("mass" if u in MASS else None)
