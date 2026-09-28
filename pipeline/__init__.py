"""pipeline — Raw Excel -> normalize -> prepared -> prepared_v2

พอร์ตตรงจาก notebook 3 ตัว (ลำดับเดิมตาม DATA_PREPARATION.md §Pipeline):
    ขั้น 1  explore_reschema.ipynb          -> stage1_normalize
    ขั้น 2  column_prepare.ipynb            -> stage2_prepare
    ขั้น 3  recheck_comlumn_preapare.ipynb  -> stage3_target

กติกาการพอร์ต: ทุกฟังก์ชัน/ค่าคงที่/ลำดับการทำงาน ต้องให้ผลเท่ากับ notebook ทุกไบต์
ตรวจด้วย `python run_pipeline.py verify` ซึ่งรันชุดข้อมูล baseline แล้วเทียบ CSV กับที่ commit ไว้
"""

__all__ = ["paths", "gates", "parsers", "overrides",
           "stage1_normalize", "stage2_prepare", "stage3_target"]
