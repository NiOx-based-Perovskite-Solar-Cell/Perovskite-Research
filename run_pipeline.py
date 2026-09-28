#!/usr/bin/env python3
"""รัน pipeline เตรียมข้อมูล: Raw Data/*.xlsx -> processed/prepared_v2/

    python run_pipeline.py                  # รันทั้งเส้น
    python run_pipeline.py --stage 3        # รันขั้น 3 ตัวเดียว (ใช้ผลขั้น 2 ที่มีอยู่)
    python run_pipeline.py --from 2         # รันขั้น 2 ถึงจบ
    python run_pipeline.py --strict         # ของใหม่ที่กติกาไม่รู้จัก = หยุด (พฤติกรรมเดิมของ notebook)
    python run_pipeline.py --dry-run        # คำนวณอย่างเดียว ไม่เขียนไฟล์
    python run_pipeline.py verify           # พิสูจน์ว่าผลลัพธ์ตรงกับ notebook ทุกไบต์

ขั้นที่ 1 = explore_reschema · 2 = column_prepare · 3 = recheck_comlumn_preapare
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import stage1_normalize as s1          # noqa: E402
from pipeline import stage2_prepare as s2            # noqa: E402
from pipeline import stage3_target as s3             # noqa: E402
from pipeline.gates import GateReport, PipelineBlocked  # noqa: E402
from pipeline.paths import Paths, find_root          # noqa: E402

# commit ที่ processed/ ถูกสร้างจาก Raw Data ชุดเดียวกัน -- ใช้เป็นชุดอ้างอิงของ verify
# (ถ้ามีการ commit ผลลัพธ์ชุดใหม่ ให้อัปเดตค่านี้ หรือส่ง --ref)
VERIFY_REF = "9aa453c"

VERIFY_TARGETS = {
    "normalize": ["devices", "layers", "vendors", "materials",
                  "process", "properties", "stability"],
    "prepared": ["devices", "layers", "materials", "process",
                 "properties", "stability", "vendors"],
    "prepared_v2": ["devices", "layers", "materials", "process", "properties",
                    "stability", "vendors", "target_table", "device_layer_flags",
                    "psk_composition"],
    "audit": ["text_cleanup_map", "clean_diff_cells", "value_parse_audit",
              "property_decomposition", "prepare_audit"],
}


def run_pipeline(paths: Paths, gate: GateReport, first: int, last: int,
                 write: bool) -> None:
    if first <= 1 <= last:
        print("\n=== ขั้น 1: Raw Data -> normalize ===")
        tables = s1.run(paths, gate)
        gate.raise_if_blocked()
        if write:
            s1.write(tables, paths)
    if first <= 2 <= last:
        print("\n=== ขั้น 2: normalize -> prepared ===")
        st = s2.run(paths, gate)
        if write:
            s2.write(st, paths, gate)
    if first <= 3 <= last:
        print("\n=== ขั้น 3: prepared -> prepared_v2 ===")
        st = s3.run(paths, gate)
        if write:
            s3.write(st, paths, gate)


def cmd_run(args) -> int:
    root = find_root()
    paths = Paths(root)
    gate = GateReport(strict=args.strict)
    first = args.stage or args.start or 1
    last = args.stage or 3
    print(f"root: {root}\nworkbook: {len(paths.raw_workbooks())} ไฟล์"
          f"{'  [dry-run: ไม่เขียนไฟล์]' if args.dry_run else ''}")
    try:
        run_pipeline(paths, gate, first, last, write=not args.dry_run)
    except PipelineBlocked as e:
        print(f"\n⛔ หยุด: {e}")
        _write_review(gate, paths)
        return 1
    print(f"\n=== สรุป: {gate.summary()} ===")
    _write_review(gate, paths)
    _print_pending(paths)
    return 0


def _print_pending(paths: Paths) -> None:
    """เปเปอร์ที่รอเปิด -- พิมพ์ทุกครั้งเพื่อไม่ให้เรื่องค้างหล่นหาย"""
    import csv
    f = paths.overrides / "pending_papers.csv"
    if not f.exists():
        return
    with open(f, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("doi", "").strip()]
    if not rows:
        return
    print(f"\nรอเปิดเปเปอร์ {len(rows)} ฉบับ (pipeline ทำต่อไม่ได้จนกว่าจะมีคนดู):")
    for r in rows:
        print(f"  · {r['doi']:32s} {r['เรื่องที่ค้าง']}")
        print(f"      {r['ต้องหาอะไรในเปเปอร์']}")


def _write_review(gate: GateReport, paths: Paths) -> None:
    if p := gate.write(paths.review):
        print(f"รายการที่ต้องมีคนดู: {p.relative_to(paths.root)}")


def cmd_verify(args) -> int:
    """แตกชุดข้อมูล baseline จาก git แล้วเทียบผลลัพธ์ทุกไบต์

    นี่คือหลักฐานว่า pipeline ให้ผลเท่ากับ notebook -- ไม่ใช่แค่ 'น่าจะเหมือน'
    """
    root = find_root()
    ref = args.ref
    with tempfile.TemporaryDirectory(prefix="pipeline-verify-") as tmp:
        tmp = Path(tmp)
        base, run = tmp / "baseline", tmp / "run"
        base.mkdir()
        run.mkdir()
        tar = subprocess.run(["git", "archive", ref, "Raw Data", "processed"],
                             cwd=root, capture_output=True)
        if tar.returncode:
            print(f"git archive {ref} ไม่สำเร็จ: {tar.stderr.decode()[:200]}")
            return 2
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar.stdout, check=True)
        shutil.copytree(base / "Raw Data", run / "Raw Data")

        n = len(list((run / "Raw Data").glob("*.xlsx")))
        print(f"ชุดอ้างอิง {ref}: {n} workbook -> รัน pipeline แล้วเทียบกับ processed/ ที่ commit ไว้\n")

        gate = GateReport(strict=False, quiet=True)
        paths = Paths(run)
        try:
            run_pipeline(paths, gate, 1, 3, write=True)
        except PipelineBlocked as e:
            print(f"⛔ pipeline หยุดกลางทาง: {e}")
            return 1

        declared = _load_deviations(Paths(root))
        same, diff, expected, missing = [], [], [], []
        for folder, names in VERIFY_TARGETS.items():
            for name in names:
                key = f"{folder}/{name}"
                a = run / "processed" / folder / f"{name}.csv"
                b = base / "processed" / folder / f"{name}.csv"
                if not b.exists():
                    missing.append(key)
                elif a.exists() and a.read_bytes() == b.read_bytes():
                    same.append(key)
                elif key in declared:
                    expected.append(key)
                else:
                    diff.append(key)

        for f in same:
            print(f"  ✓ {f}")
        for f in expected:
            print(f"  ≠ ต่างตามที่ประกาศไว้: {f}\n      {declared[f]}")
        for f in diff:
            print(f"  ✗ ต่างโดยไม่ได้ประกาศ: {f}")
        for f in missing:
            print(f"  – ไม่มีใน baseline: {f}")

        n_tot = len(same) + len(expected) + len(diff)
        print(f"\nตรงกับ notebook ทุกไบต์ {len(same)}/{n_tot} ไฟล์"
              f" | ต่างตามที่ตั้งใจ {len(expected)} ไฟล์")
        if diff:
            print("มีไฟล์ที่ต่างโดยไม่ได้ประกาศ -- ผลลัพธ์เปลี่ยนแบบไม่ได้ตั้งใจ ห้ามใช้จนกว่าจะรู้ว่าเพราะอะไร")
            print("ถ้าเป็นการแก้ที่ตั้งใจ ให้เพิ่มบรรทัดใน pipeline/overrides/known_deviations.csv")
            return 1
        if expected:
            print("ไม่มีไฟล์ไหนเปลี่ยนโดยไม่ได้ตั้งใจ ✓ (ส่วนที่ต่างประกาศเหตุผลไว้ครบ)")
        else:
            print("pipeline ให้ผลเท่ากับ notebook ทุกไบต์ ✓")
        return 0


def _load_deviations(paths: Paths) -> dict[str, str]:
    """ไฟล์ที่ผลลัพธ์ต่างจาก notebook เพราะเราตั้งใจแก้ -- ประกาศไว้พร้อมเหตุผล

    ทำให้ verify ยังจับ "ผลเปลี่ยนโดยไม่ได้ตั้งใจ" ได้อยู่ หลังจาก pipeline
    เริ่มแก้สิ่งที่ notebook ทำผิดหรือทำไม่ครบ
    """
    import csv
    f = paths.overrides / "known_deviations.csv"
    if not f.exists():
        return {}
    with open(f, encoding="utf-8") as fh:
        return {r["ไฟล์"].strip(): r["เหตุผล"].strip()
                for r in csv.DictReader(fh) if r.get("ไฟล์", "").strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="รัน pipeline (ค่าเริ่มต้น)")
    for p in (ap, r):
        p.add_argument("--stage", type=int, choices=[1, 2, 3], help="รันขั้นเดียว")
        p.add_argument("--from", dest="start", type=int, choices=[1, 2, 3],
                       help="เริ่มจากขั้นนี้ถึงจบ")
        p.add_argument("--strict", action="store_true",
                       help="ของใหม่ที่กติกาไม่รู้จัก = หยุด")
        p.add_argument("--dry-run", action="store_true", help="ไม่เขียนไฟล์")

    v = sub.add_parser("verify", help="พิสูจน์ว่าผลตรงกับ notebook ทุกไบต์")
    v.add_argument("--ref", default=VERIFY_REF, help=f"git ref ของชุดอ้างอิง (ค่าเริ่มต้น {VERIFY_REF})")

    args = ap.parse_args()
    if args.cmd == "verify":
        return cmd_verify(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
