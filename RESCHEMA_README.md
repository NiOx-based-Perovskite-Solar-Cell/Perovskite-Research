# Re-schema — Perovskite Solar Cell Process Data

เอกสารนี้อธิบายการจัด schema ใหม่ของข้อมูลใน [`Raw Data/`](Raw%20Data/) และ
**คำแนะนำว่าควรเก็บข้อมูลในรูปแบบใด** สำหรับการนำไปทำ ML/DL ต่อ

โปรเจกต์นี้เน้น **กระบวนการ/ขั้นตอนการทดลอง (process/fabrication)** ของ perovskite
solar cell — ต่างจากงานทั่วไปที่เน้นค้นหาวัสดุ/ธาตุใหม่ ดังนั้น schema จึงออกแบบให้
*ขั้นตอนการทำ* และ *การเชื่อมขั้นตอนกับชั้น (layer)* เป็นพลเมืองชั้นหนึ่ง

---

## 1. ข้อมูลดิบเป็นอย่างไร

- **1 ไฟล์ `.xlsx` = 1 device (sample)** ตั้งชื่อ `<doi>_Sample<N>.xlsx`
- ปัจจุบันมี **44 devices จาก 15 publications**
- แต่ละ workbook มี 6 sheets:

| Sheet | เนื้อหา |
|-------|---------|
| `Layer` | โครงสร้างชั้นของอุปกรณ์ (Anode → HTL → Perovskite → ETL → Cathode) |
| `Vender` | ผู้ผลิต/ความบริสุทธิ์ของวัสดุแต่ละตัว |
| `Materials` | องค์ประกอบวัสดุต่อชั้น (ตัวทำละลาย, ปริมาณ, หน่วย) |
| `Process` | ขั้นตอนการทำต่อชั้น (spin-coating, annealing, antisolvent, …) |
| `Properties` | สมบัติที่วัดได้ (Voc, Jsc, FF, PCE, band gap, trap density, …) |
| `Stability` | ผลทดสอบความเสถียร (อุณหภูมิ, ความชื้น, เวลา, %retention) |

### ปัญหา data quality ที่พบ (และจัดการแล้วใน ETL)
- ลำดับ sheet สลับกันบางไฟล์ (`Materials`/`Vender`) → อ้างด้วยชื่อ sheet ไม่ใช่ตำแหน่ง
- หัวตาราง `Stability` สะกด/พิมพ์เล็กใหญ่ต่างกัน 7 แบบ → map เป็นชื่อมาตรฐานเดียว
- หัวตาราง `Properties` มี/ไม่มีคอลัมน์ `Errors` → เติมให้ครบ
- ชื่อใน `Properties` เป็น free-text **200+ ชื่อ** (`Champion PCE`, `Avg PCE`, `PCE (RS)`, …)
- มีเซลล์ว่าง, ช่องว่าง/ขึ้นบรรทัดปนในข้อความ, แถวว่าง → clean ทั้งหมด

---

## 2. Schema ใหม่ (normalized) — source of truth

ทุกตารางเชื่อมด้วยคีย์เดียว **`device_id`** (= ชื่อไฟล์ เช่น `10_1126_science_ade9637_Sample2`)

```
publication (doi_raw)
   └─< devices (device_id, doi_raw, sample_no, source_file)      # 1 แถว / sample
         ├─< layers     (device_id, layer_id, layer_type, material, thickness_nm)
         ├─< materials  (device_id, layer_id, material, type, solvent, amount, unit)
         ├─< process    (device_id, layer_id, step_order, process_type, description, parameter, unit)
         ├─< vendors    (device_id, material, purity, vendor)
         ├─< properties (device_id, property, value, error, unit, remarks)   ← long / key–value (EAV)
         └─< stability  (device_id, environment, encapsulation, temperature_c,
                         humidity_pct, time_hr, retention_pct, t80_hr)
```

- `layers.layer_id` เป็น natural key ภายในแต่ละ device ที่ `materials` และ `process`
  อ้างถึง → ตอบได้ว่า "วัสดุ/ขั้นตอนไหนอยู่ชั้นไหน" (หัวใจของโปรเจกต์ที่เน้น process)
- `properties` เก็บเป็น **long format** (ไม่ใช่ wide) เพราะชื่อสมบัติหลากหลายเกินไป
  การเก็บแบบ wide จะได้ตารางที่ NaN เกือบทั้งหมด

ผลลัพธ์ตาราง normalized เขียนเป็น CSV ที่ [`processed/csv/`](processed/csv/)

---

## 3. ⭐ ตารางพร้อมใช้กับ ML/DL — `processed/ml_dataset.csv`

ตาราง normalized ด้านบนเหมาะเป็นแหล่งข้อมูลตั้งต้น แต่ **ML/DL ต้องการ feature matrix
แบบ 1 แถวต่อ 1 sample** ดังนั้น `build_ml_table()` จึง pivot/aggregate กลับมาเป็น
ตารางกว้าง **44 แถว × 17 คอลัมน์**:

| กลุ่ม | คอลัมน์ |
|------|---------|
| identifier | `device_id`, `doi_raw`, `sample_no` |
| structural | `n_layers`, `htl_material`, `etl_material`, `perovskite`, `anode`, `cathode` |
| process | `n_process_steps`, `n_spin_coating`, `has_antisolvent` |
| JV / target | `pce_max`, `voc_max`, `jsc_max`, `ff_max`, `bandgap_max` |

> JV metrics ได้จากการ pivot `properties` โดยรวมชื่อที่หมายถึงสิ่งเดียวกัน
> (Champion/Average/RS/FS) แล้วใช้ค่า **max (≈ champion)**. ขยาย/ปรับ feature ได้ที่
> `build_ml_table()` ใน [`scripts/reschema.py`](scripts/reschema.py)

โหลดเข้าโมเดลได้ทันที:
```python
import pandas as pd
df = pd.read_csv("processed/ml_dataset.csv")
y = df["pce_max"]                       # target เช่น ทำนาย PCE
X = df.drop(columns=["pce_max"])        # encode คอลัมน์ categorical ก่อนเทรน
```

---

## 4. ควรเก็บข้อมูลแบบไหน? — คำแนะนำ

> **สรุป: ใช้ CSV** — และที่สำคัญกว่านามสกุลไฟล์คือ *รูปทรงตาราง* ต้องเป็น
> **1 แถวต่อ 1 device แบบ wide** (`ml_dataset.csv`) ซึ่งคือสิ่งที่ ML/DL ใช้ได้ตรงๆ

| รูปแบบ | เหมาะกับงานนี้? | เหตุผล |
|--------|:--:|--------|
| **CSV (flat, 1 แถว/device)** | ✅ **แนะนำ** | 44 แถวเล็กมาก, `pd.read_csv()` → scikit-learn/PyTorch ได้ทันที, version control ได้, ไม่ต้องตั้ง server, ทุกเครื่องมืออ่านได้ |
| Relational DB (SQLite/Postgres) | ⚠️ เกินจำเป็น | เหมาะเป็น *แหล่ง query* แต่ ML ต้อง flatten เป็น matrix อยู่ดี เพิ่ม infra เปล่าๆ ที่ขนาดนี้ |
| NoSQL / JSON (nested) | ⚠️ ทางอ้อม | nested ดีต่อการ archive ราย device แต่ต้อง parse + pivot ก่อนเทรนทุกครั้ง |
| GraphQL | ❌ ไม่เกี่ยว | เป็น *API layer* สำหรับ serve ข้อมูล ไม่ใช่รูปแบบจัดเก็บ dataset วิจัย |

**ทำไม CSV ชนะที่สเกลนี้:**
1. ML/DL framework กินตาราง flat — CSV → DataFrame → array คือเส้นทางสั้นที่สุด
2. 44 แถว (โตเป็นหลักร้อย/พันก็ยังเล็ก) ไม่จำเป็นต้องมี DB/index
3. text-based → diff ได้ใน git, รีวิว/แชร์ง่าย, reproducible
4. ไม่มี dependency หรือ server ให้ดูแล

**เมื่อไรค่อยพิจารณาเปลี่ยน:**
- ถ้าโตถึงหลัก **10⁵–10⁶ แถว** หรือ join หนัก ๆ บ่อย → ย้าย *source of truth* ไป **SQLite/Parquet**
  (ฟังก์ชัน `write_sqlite()` มีให้แล้ว) แล้ว export `ml_dataset.csv`/Parquet เหมือนเดิม
- ถ้าจะทำ **sequence/graph model** บนลำดับขั้นตอน → ดึงจากตาราง `process` (long) โดยตรง
  แทนการ flatten
- ถ้าต้องเปิดให้ทีม/แอปอื่นดึงข้อมูล → ค่อยวาง **API (REST/GraphQL)** ทับ DB ภายหลัง

---

## 5. ไฟล์ในโปรเจกต์

| ไฟล์ | หน้าที่ |
|------|---------|
| [`notebooks/01_explore_and_reschema.ipynb`](notebooks/01_explore_and_reschema.ipynb) | สำรวจข้อมูล + แสดงขั้นตอน re-schema ทั้งหมด (รันแล้วมี output) |
| [`scripts/reschema.py`](scripts/reschema.py) | ETL ใช้ซ้ำได้: raw `.xlsx` → ตาราง normalized → `ml_dataset.csv` |
| `processed/ml_dataset.csv` | ⭐ ตารางพร้อมเทรน (1 แถว/device) |
| `processed/csv/*.csv` | ตาราง normalized (source of truth) |

### วิธีรัน
```bash
pip install pandas openpyxl          # dependency ขั้นต่ำ
python scripts/reschema.py           # regenerate processed/ ทั้งหมด
```
