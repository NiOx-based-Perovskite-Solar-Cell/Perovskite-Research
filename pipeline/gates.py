"""ด่านตรวจแบบมีนโยบาย -- แทน assert เปล่า ๆ ของ notebook

notebook เดิมใช้ `assert` ทุกที่ ผลคือข้อมูลชุดใหม่ที่มีรูปแบบไม่เคยเจอ
จะทำให้ทั้ง pipeline ตายกลางทาง แล้วต้องไล่แก้ทีละเคสก่อนรันต่อได้

ตรงนี้แยกสิ่งที่เจอเป็น 2 ชนิด:

  BLOCK  = "โค้ดพัง / ข้อมูลเสียหาย" เช่นจำนวนแถวเปลี่ยน, ไฟล์บนดิสก์ไม่ตรงกับ memory,
           device_id ซ้ำ -- ต้องหยุด เพราะผลลัพธ์เชื่อไม่ได้

  REVIEW = "เจอของใหม่ที่กติกายังไม่รู้จัก" เช่นหน่วยแบบใหม่, ค่าหลุดช่วงฟิสิกส์,
           เลขค้างในช่อง description -- ไม่ได้แปลว่าโค้ดพัง แต่ต้องมีคนดู
           -> pipeline เดินต่อ ติดธงที่แถวนั้น แล้วรวมเป็น processed/review/review_queue.csv

โหมด strict ดัน REVIEW ขึ้นเป็น BLOCK ทั้งหมด (ได้พฤติกรรมเดิมของ notebook กลับมา)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BLOCK = "block"
REVIEW = "review"
INFO = "info"


class PipelineBlocked(RuntimeError):
    """ยกเมื่อมี finding ระดับ BLOCK -- ไม่เขียนผลลัพธ์ต่อ"""


@dataclass
class Finding:
    level: str
    stage: str
    code: str
    message: str
    rows: pd.DataFrame | None = None

    def head(self, n: int = 8) -> str:
        if self.rows is None or self.rows.empty:
            return ""
        return self.rows.head(n).to_string(index=False, max_colwidth=40)


@dataclass
class GateReport:
    strict: bool = False
    quiet: bool = False
    findings: list[Finding] = field(default_factory=list)
    stage: str = ""

    # ---------- บันทึกผล ----------

    def _add(self, level: str, code: str, message: str,
             rows: pd.DataFrame | None = None) -> Finding:
        if level == REVIEW and self.strict:
            level = BLOCK
        f = Finding(level, self.stage, code, message, rows)
        self.findings.append(f)
        if not self.quiet:
            tag = {BLOCK: "⛔ BLOCK ", REVIEW: "⚠️  REVIEW", INFO: "   info "}[level]
            print(f"{tag} [{self.stage}/{code}] {message}")
            if level != INFO and (body := f.head()):
                print("\n".join("        " + ln for ln in body.splitlines()))
        return f

    def block(self, code: str, message: str, rows: pd.DataFrame | None = None) -> Finding:
        return self._add(BLOCK, code, message, rows)

    def review(self, code: str, message: str, rows: pd.DataFrame | None = None) -> Finding:
        return self._add(REVIEW, code, message, rows)

    def info(self, code: str, message: str) -> Finding:
        return self._add(INFO, code, message)

    def note(self, message: str) -> None:
        """ข้อความเล่าเรื่องระหว่างรัน (ไม่เข้า review queue)"""
        if not self.quiet:
            print(f"   · {message}")

    # ---------- ตัวช่วย ----------

    def require(self, ok: bool, code: str, message: str,
                rows: pd.DataFrame | None = None) -> bool:
        """invariant ที่ผิดแล้วผลลัพธ์เชื่อไม่ได้ -- ผิด = BLOCK เสมอ ไม่ขึ้นกับโหมด"""
        if not ok:
            self.block(code, message, rows)
        return ok

    def expect_one(self, n: int, code: str, what: str) -> str:
        """ใช้กับ override ที่ต้องจับได้ 1 แถว

        notebook เดิม `assert n == 1` ทำให้พอ device นั้นถูกถอดออกจากชุดข้อมูล
        ทั้ง pipeline ตายทันที ที่นี่:
            0 แถว  -> REVIEW (override เก่าค้าง หรือ device หายไป) แล้วข้ามไป
            >1 แถว -> BLOCK  (กำกวมจริง ถ้าแก้ทั้งคู่อาจเขียนผิดตัว)
        """
        if n == 1:
            return "apply"
        if n == 0:
            self.review(code, f"override ไม่ตรงกับข้อมูลแล้ว (จับได้ 0 แถว) -- ข้ามไป: {what}")
            return "skip"
        self.block(code, f"override จับได้ {n} แถว (ต้อง 1) -- กำกวม: {what}")
        return "skip"

    # ---------- สรุป ----------

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.level == BLOCK]

    @property
    def reviews(self) -> list[Finding]:
        return [f for f in self.findings if f.level == REVIEW]

    def raise_if_blocked(self) -> None:
        if self.blocks:
            lines = [f"  [{f.stage}/{f.code}] {f.message}" for f in self.blocks]
            raise PipelineBlocked(
                f"หยุดที่ {len(self.blocks)} ด่าน (ข้อมูลเชื่อไม่ได้):\n" + "\n".join(lines))

    def to_frame(self) -> pd.DataFrame:
        """review queue -- 1 แถวต่อ 1 เรื่องที่ต้องมีคนดู (กระจายแถวข้อมูลออกมาด้วย)"""
        out = []
        for f in self.findings:
            if f.level == INFO:
                continue
            if f.rows is None or f.rows.empty:
                out.append({"level": f.level, "stage": f.stage, "code": f.code,
                            "message": f.message})
                continue
            for rec in f.rows.to_dict("records"):
                out.append({"level": f.level, "stage": f.stage, "code": f.code,
                            "message": f.message,
                            **{f"row.{k}": v for k, v in rec.items()}})
        return pd.DataFrame(out)

    def write(self, review_dir: Path) -> Path | None:
        df = self.to_frame()
        if df.empty:
            return None
        review_dir.mkdir(parents=True, exist_ok=True)
        path = review_dir / "review_queue.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def summary(self) -> str:
        n_b, n_r = len(self.blocks), len(self.reviews)
        if not n_b and not n_r:
            return "ผ่านทุกด่าน ไม่มีอะไรต้องดู"
        parts = []
        if n_b:
            parts.append(f"{n_b} เรื่องที่ต้องหยุด")
        if n_r:
            parts.append(f"{n_r} เรื่องที่ต้องมีคนดู")
        return " · ".join(parts)
