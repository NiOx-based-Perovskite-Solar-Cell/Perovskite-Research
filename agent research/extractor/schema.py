"""ฟิลด์ที่ agent ต้องสกัด — จุดเดียวที่นิยาม ใช้ทั้ง prompt, parser และ compare

PoC เริ่มจากฟิลด์ที่เทียบง่ายและมีในไฟล์คนกรอกครบ: ผล J-V ของอุปกรณ์ + วัสดุแต่ละชั้น
ขยายทีหลังโดยเพิ่มบรรทัดใน FIELDS (kind: num = ตัวเลข, str = ข้อความ)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    kind: str          # "num" | "str"
    desc: str
    rel_tol: float = 0.02   # num: ยอมให้ต่างได้กี่ % (สัมพัทธ์)
    abs_tol: float = 0.0    # num: หรือต่างได้กี่หน่วยสัมบูรณ์


FIELDS: list[Field] = [
    Field("pce", "num", "Champion PCE (%)", abs_tol=0.05),
    Field("voc", "num", "Champion Voc (V)", abs_tol=0.005),
    Field("jsc", "num", "Champion Jsc (mA/cm2)", abs_tol=0.05),
    Field("ff", "num", "Champion FF (%)", abs_tol=0.1),
    Field("htl", "str", "วัสดุ HTL หลัก เช่น NiOx, Cu:NiOx"),
    Field("htl_treatment", "str", "สารที่ใช้ปรับผิว/เคลือบ HTL ถ้ามี"),
    Field("etl", "str", "วัสดุ ETL"),
    Field("perovskite", "str", "สูตรเพอรอฟสไกต์ เช่น Cs0.05MA0.05FA0.9PbI3"),
]
FIELD_NAMES = [f.name for f in FIELDS]


def json_spec() -> str:
    """ข้อความอธิบายรูปแบบ JSON ที่ต้องการ ฝังใน prompt"""
    lines = [f'    "{f.name}": <{"number|null" if f.kind == "num" else "string|null"}>,  // {f.desc}'
             for f in FIELDS]
    return ('{\n  "devices": [\n    {\n      "label": <string>,  // ชื่อกลุ่มในเปเปอร์ เช่น Control / Target\n'
            + "\n".join(lines) + "\n    }\n  ]\n}")
