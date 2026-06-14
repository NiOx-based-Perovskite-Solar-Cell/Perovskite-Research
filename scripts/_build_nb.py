import json, nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []
def md(s): cells.append(nbf.v4.new_markdown_cell(s.strip("\n")))
def code(s): cells.append(nbf.v4.new_code_cell(s.strip("\n")))

md(r"""
# Perovskite Solar Cell Data — Exploration & Re-schema

โปรเจกต์นี้เน้น **กระบวนการ/ขั้นตอนการทดลอง (process)** ในการทำ perovskite solar cell
(ต่างจากงานที่เน้นค้นหาวัสดุ/ธาตุใหม่). Raw data อยู่ในโฟลเดอร์ `Raw Data/` โดย
**1 ไฟล์ `.xlsx` = 1 device (sample)** ตั้งชื่อว่า `<doi>_Sample<N>.xlsx`.

แต่ละ workbook มี 6 sheets: `Layer`, `Vender`, `Materials`, `Process`, `Properties`, `Stability`.

Notebook นี้แบ่งเป็น 3 ส่วน:
1. **Explore** — สำรวจไฟล์, ตรวจความสม่ำเสมอของ schema, หาปัญหา data quality
2. **Re-schema** — ออกแบบ + รัน ETL เปลี่ยนเป็นตาราง normalized ที่เชื่อมด้วย `device_id`
3. **Outputs & Queries** — เขียนออกเป็น CSV / SQLite / JSON และตัวอย่าง query

> สรุปคำแนะนำเรื่องรูปแบบจัดเก็บ (relational / nosql / csv / json / graphql) อยู่ใน **`RESCHEMA_README.md`**.
""")

md("## 0. Setup")
code(r"""
import sys, os, glob, re
import pandas as pd

# ทำให้ import scripts/reschema.py ได้ ไม่ว่าจะรัน notebook จากที่ไหน
ROOT = os.path.abspath(os.path.join(os.getcwd(), ".." if os.path.basename(os.getcwd())=="notebooks" else "."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

RAW_DIR = "Raw Data"
files = sorted(glob.glob(os.path.join(RAW_DIR, "*.xlsx")))
print("repo root :", ROOT)
print("workbooks :", len(files))
""")

md("""
## 1. Inventory — ไฟล์ / publication / sample

ชื่อไฟล์เข้ารหัส DOI + หมายเลข sample. แยกออกมาเป็นคีย์ที่ใช้งานได้.
""")
code(r"""
from scripts.reschema import parse_device_id

inv = pd.DataFrame(
    [dict(zip(["device_id","doi_raw","sample_no"], parse_device_id(f))) | {"file": os.path.basename(f)}
     for f in files]
)
print(f"{len(inv)} devices จาก {inv['doi_raw'].nunique()} publications\n")
display(inv.head(10))
display(inv.groupby("doi_raw").size().rename("n_samples").sort_values(ascending=False))
""")

md("""
## 2. Schema audit — sheet และ header สม่ำเสมอแค่ไหน?

ตรวจว่าทุกไฟล์มี sheet/หัวคอลัมน์ตรงกันหรือไม่ (จุดที่ต้อง normalize).
""")
code(r"""
from collections import Counter

sheet_combos, headers = Counter(), {}
for f in files:
    xl = pd.ExcelFile(f)
    sheet_combos[tuple(xl.sheet_names)] += 1
    for sh in xl.sheet_names:
        h = tuple(str(x).strip() for x in pd.read_excel(f, sh, nrows=0).columns)
        headers.setdefault(sh, Counter())[h] += 1

print("=== ชุดชื่อ sheet ที่พบ ===")
for combo, n in sheet_combos.most_common():
    print(f"  {n:2d}x  {combo}")

print("\n=== จำนวน header แบบที่ต่างกัน ต่อ sheet ===")
for sh, c in headers.items():
    print(f"  {sh:12s}: {len(c)} แบบ")
""")
code(r"""
# sheet ที่ header ไม่นิ่ง = Stability (และ Properties) -> ต้อง map ให้เป็นมาตรฐานเดียว
for h, n in headers["Stability"].most_common():
    print(f"{n:2d}x {h}")
""")

md("""
## 3. ดูข้อมูลจริงของ 1 device (ครบ 6 sheets)
""")
code(r"""
sample_file = files[0]
print("FILE:", os.path.basename(sample_file), "\n")
for sh in ["Layer","Vender","Materials","Process","Properties","Stability"]:
    print(f"───── {sh} ─────")
    display(pd.read_excel(sample_file, sh))
""")

md("""
## 4. Data-quality scan

ประเด็นที่เจอ และต้องจัดการตอน re-schema:
- ชื่อ `Properties` เป็น free-text หลากหลายมาก (200+ ชื่อ เช่น `Champion PCE`, `Avg PCE`, `PCE (RS)`)
  → เก็บแบบ **long / key–value (EAV)** ไม่ใช่ wide columns
- header ของ `Stability` สะกด/ใช้ตัวพิมพ์ต่างกัน → map เป็นชื่อมาตรฐาน
- มีเซลล์ว่าง, ช่องว่าง/ขึ้นบรรทัดปนในข้อความ, แถวว่าง → ต้อง clean
""")
code(r"""
prop_names = Counter()
for f in files:
    s = pd.read_excel(f, "Properties")["Properties"].dropna().astype(str).str.strip()
    prop_names.update(s)
print(f"property names ที่ไม่ซ้ำกัน: {len(prop_names)} ชื่อ  (top 15)")
for name, n in prop_names.most_common(15):
    print(f"  {n:3d}  {name}")
""")

md(r"""
## 5. Re-schema — โมเดล normalized

ทุกตารางเชื่อมกันด้วยคีย์เดียว **`device_id`** (= ชื่อไฟล์ เช่น `10_1126_science_ade9637_Sample2`).

```
publication (doi_raw)
   └─< devices (device_id)                     # 1 แถว / sample
         ├─< layers        (device_id, layer_id, layer_type, material, thickness_nm)
         ├─< materials      (device_id, layer_id, material, type, solvent, amount, unit)
         ├─< process_steps  (device_id, layer_id, step_order, process_type, description, parameter, unit)
         ├─< vendors        (device_id, material, purity, vendor)
         ├─< properties     (device_id, property, value, error, unit, remarks)   # long/EAV
         └─< stability      (device_id, environment, encapsulation, temperature_c, humidity_pct, time_hr, retention_pct, t80_hr)
```

`layers.layer_id` เป็น natural key ภายใน device ที่ `materials` และ `process_steps` อ้างถึง
→ ทำให้ตอบได้ว่า "วัสดุ/ขั้นตอนไหนอยู่ layer ไหน" ซึ่งเป็นหัวใจของโปรเจกต์ที่เน้น *process*.

ETL logic อยู่ใน [`scripts/reschema.py`](../scripts/reschema.py) (ใช้ซ้ำได้นอก notebook).
""")
code(r"""
from importlib import reload
import scripts.reschema as rs
reload(rs)

tables = rs.build_tables()
for name, df in tables.items():
    print(f"{name:12s} {df.shape}")
""")
code(r"""
for name in ["devices","layers","materials","process","properties","stability"]:
    print(f"\n───── {name} ─────")
    display(tables[name].head(6))
""")

md(r"""
## 6. ⭐ ตารางพร้อมใช้กับ ML/DL — `ml_dataset.csv` (1 แถว / device)

โมเดล normalized ด้านบนเหมาะเป็น **source of truth** แต่ ML/DL ต้องการ **feature matrix
1 แถวต่อ 1 sample**. `build_ml_table()` จึง pivot/aggregate ตาราง long กลับมาเป็นตารางกว้างตารางเดียว:

- **identifier**: `device_id`, `doi_raw`, `sample_no`
- **structural** (จาก layer stack): `n_layers`, `htl_material`, `etl_material`, `perovskite`, `anode`, `cathode`
- **process** (หัวใจของโปรเจกต์): `n_process_steps`, `n_spin_coating`, `has_antisolvent`
- **JV / target** (pivot จาก Properties, ใช้ค่า max≈champion): `pce_max`, `voc_max`, `jsc_max`, `ff_max`, `bandgap_max`

นี่คือรูปแบบที่ **แนะนำให้ใช้** — โหลดด้วย `pd.read_csv()` แล้วเข้า scikit-learn / PyTorch ได้ทันที.
""")
code(r"""
ml = rs.build_ml_table(tables)
print("ML table:", ml.shape)
display(ml.head(8))
print("\nค่าที่ไม่ว่าง (non-null) ต่อคอลัมน์:")
print(ml.notna().sum())
""")

md("""
## 7. เขียน output (CSV เท่านั้น)

ตามที่สรุปไว้ — เก็บเป็น **CSV** อย่างเดียว: ไม่ต้องตั้ง server, version control ได้, โหลดเข้า ML ตรงๆ
- `processed/ml_dataset.csv` — ⭐ ตารางพร้อมเทรน (1 แถว/device)
- `processed/csv/*.csv` — ตาราง normalized (source of truth ไว้สร้าง feature เพิ่ม)

> ทางเลือกอื่น (SQLite / JSON) มีให้ใน `scripts/reschema.py` ผ่าน `write_sqlite()` / `write_json()`
> แต่**ไม่ใช่เส้นทางที่แนะนำ** สำหรับงาน ML/DL — ดูเหตุผลใน `RESCHEMA_README.md`.
""")
code(r"""
ml_path = rs.write_ml_table(tables)
rs.write_csv(tables)
print("wrote ->", ml_path)
print("wrote ->", os.path.join(rs.OUT_DIR, "csv", "*.csv"))
""")

md("""
## 8. ตัวอย่างการใช้งานต่อ (pandas)

ทุกอย่างทำบน DataFrame ตรงๆ ไม่ต้องใช้ SQL/DB.
""")
code(r"""
# Top devices ตาม PCE
display(ml.sort_values("pce_max", ascending=False)
          [["device_id","htl_material","perovskite","pce_max","voc_max","jsc_max","ff_max"]].head(10))
""")
code(r"""
# HTL material ที่ใช้บ่อย + PCE เฉลี่ย (เชื่อม process -> performance)
display(ml.groupby("htl_material")
          .agg(n=("device_id","size"), mean_pce=("pce_max","mean"))
          .sort_values("n", ascending=False))
""")
code(r"""
# โหลดเข้า ML ได้ทันที: แยก feature / target
import pandas as pd
df = pd.read_csv("processed/ml_dataset.csv")

target = "pce_max"
numeric_X = ["n_layers","n_process_steps","n_spin_coating","voc_max","jsc_max","ff_max"]
cat_X     = ["htl_material","etl_material"]

model_df = df.dropna(subset=[target])
print("ตัวอย่างtรียม X/y สำหรับ regression ทำนาย PCE:")
print("X numeric:", numeric_X)
print("X categorical (ต้อง encode):", cat_X)
print("y:", target, "| n_samples =", len(model_df))
display(model_df[[target] + numeric_X + cat_X].head())
""")

md("""
---
### สรุป
- raw `.xlsx` (1 ไฟล์/device) → 7 ตาราง normalized (source of truth) → **`ml_dataset.csv`** (1 แถว/device, พร้อม ML)
- รูปแบบที่แนะนำ = **CSV** (ดูเหตุผลเทียบกับ relational/nosql/json/graphql ใน `RESCHEMA_README.md`)
- regenerate ทั้งหมด: `python scripts/reschema.py`
- ขยาย feature: แก้ `build_ml_table()` ใน `scripts/reschema.py`
""")

nb["cells"] = cells
nb["metadata"] = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                  "language_info": {"name": "python"}}
with open("notebooks/01_explore_and_reschema.ipynb", "w") as f:
    nbf.write(nb, f)
print("written notebooks/01_explore_and_reschema.ipynb with", len(cells), "cells")
