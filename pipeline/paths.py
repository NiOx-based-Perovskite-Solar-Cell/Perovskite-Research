"""หา project root แบบไม่เดาจาก cwd

notebook เดิมใช้ `Path.cwd().parent` ซึ่งถอยขึ้นได้ชั้นเดียว พอย้าย notebook
ลงไปอยู่ notebooks/prepare_data/ ก็หา processed/ ไม่เจอแล้ว
ตรงนี้ไล่ขึ้นจากตำแหน่งไฟล์นี้จนเจอ marker จึงย้ายโฟลเดอร์ได้อิสระ
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MARKERS = ("Raw Data", "DATA_PREPARATION.md")


def find_root(start: Path | None = None) -> Path:
    """ไล่ขึ้นจาก start (ค่าเริ่มต้น = ที่อยู่ของไฟล์นี้) หาโฟลเดอร์ที่มี marker ครบ"""
    start = (start or Path(__file__).resolve()).resolve()
    for cand in (start, *start.parents):
        if all((cand / m).exists() for m in MARKERS):
            return cand
    raise FileNotFoundError(
        f"หา project root ไม่เจอ -- ต้องมี {MARKERS} อยู่ในโฟลเดอร์เดียวกัน (เริ่มไล่จาก {start})")


@dataclass(frozen=True)
class Paths:
    """ที่อยู่ทุกโฟลเดอร์ที่ pipeline อ่าน/เขียน

    root ชี้ที่ไหนก็ได้ที่มี "Raw Data" -- `verify` ใช้ความสามารถนี้ชี้ไปที่สำเนา
    ข้อมูล baseline ในโฟลเดอร์ชั่วคราว เพื่อเทียบผลโดยไม่แตะ processed/ ของจริง
    """

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "Raw Data"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def normalize(self) -> Path:
        return self.processed / "normalize"

    @property
    def prepared(self) -> Path:
        return self.processed / "prepared"

    @property
    def prepared_v2(self) -> Path:
        return self.processed / "prepared_v2"

    @property
    def audit(self) -> Path:
        return self.processed / "audit"

    @property
    def review(self) -> Path:
        return self.processed / "review"

    @property
    def overrides(self) -> Path:
        # override เป็นส่วนหนึ่งของ "กติกา" ไม่ใช่ของชุดข้อมูล -> ผูกกับที่อยู่ของโค้ด
        # (จึงใช้ชุดเดียวกันเสมอ แม้ root จะชี้ไปที่สำเนา baseline ตอน verify)
        return Path(__file__).resolve().parent / "overrides"

    def raw_workbooks(self) -> list[Path]:
        """ไฟล์ Excel ชั้นบนสุดเท่านั้น -- เท่ากับ glob.glob('Raw Data/*.xlsx') ของ notebook
        (โฟลเดอร์ย่อยอย่าง _superseded/ จึงไม่ถูกนับ ตรงตามพฤติกรรมเดิม)
        """
        return sorted(p for p in self.raw.glob("*.xlsx") if not p.name.startswith("~$"))


def default_paths() -> Paths:
    return Paths(find_root())
