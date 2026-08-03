# สรุปการเตรียมข้อมูล — จาก Raw Excel ถึง `prepared_v2`

> เป้าหมายงาน: หา **process ตัวไหนขับ grain size → stability/PCE** แล้วเอาสูตรกลับไปทำแล็บจริง (closed loop)
> ไม่ใช่หาวัสดุใหม่ — โฟกัสที่ knob การผลิต ไม่ใช่เคมีใหม่
> ข้อจำกัด: N=44 devices / 15 papers → กัน overfit, กัน confound ระดับ paper (LOPO), ห้าม DL

## Pipeline ทั้งเส้น

```
Raw Data/*.xlsx  (44 ไฟล์ = 44 devices จาก 15 papers, ไฟล์ละ 6 ชีต)
   │  explore_reschema.ipynb          — ETL: แตกชีตเป็นตาราง normalize, ตั้ง device_id
   ▼
processed/normalize/                  — ตารางยาว: devices, layers, materials, process, properties, stability, vendors
   │  column_prepare.ipynb (102 cells) — คลีนคอลัมน์ทีละเรื่อง A-L แบบ explore-แล้ว-fix
   ▼
processed/prepared/                   — ตารางคลีนแล้ว + audit trail ใน processed/audit/
   │  recheck_comlumn_preapare.ipynb  — ตรวจซ้ำ + แตก process knob + สร้าง target table
   ▼
processed/prepared_v2/                — ชุดสุดท้ายสำหรับ modeling (10 ไฟล์)
   │  explore_prepared_v2.ipynb       — EDA ชุดสุดท้าย (อ่านอย่างเดียว)
   ▼
   ต่อไป: RandomForest + SHAP, LeaveOneGroupOut(paper)
```

notebook เสริม: `after_col_prepare.ipynb` (ยุบเป็น device-level 44×39 เพื่อดูภาพรวม), `eda_prepare.ipynb` (EDA ชุด prepared), `column_audit.ipynb`

---

## ขั้น 1 — `explore_reschema`: Raw → normalize

- แตก 6 ชีตต่อไฟล์เป็นตาราง normalize ผูกทุกแถวด้วย `device_id` (= ชื่อไฟล์ DOI_SampleN)
- โครง layer เป็นระบบตำแหน่ง: **เลขคู่ = ชั้นหลัก** (1 Anode, 2 HTL, 4 PSK, 6 ETL, 8 Cathode), **เลขคี่กลาง = ชั้นเคลือบ** (3/5/7 = treatment)

## ขั้น 2 — `column_prepare`: คลีน A-L (explore → fix ทีละเรื่อง, gate ก่อน/หลังเขียนดิสก์)

| หมวด | ทำอะไร |
|---|---|
| A | Unicode ปน (μ กรีก vs µ micro), whitespace เกิน |
| B | typo ที่รู้ตัวแล้ว |
| C | สะกดต่างกัน (case/วรรคตอน) → canonical map |
| D-E | parse `thickness_nm` เป็นตัวเลข (รวมแปลงหน่วย Å→nm), แตก material ที่กรอกเป็น pivot |
| F | stability: temperature/humidity/retention เป็นตัวเลข |
| G | `vendors.purity` parse |
| H | จัดกลุ่ม `material_family` |
| I | `properties` unit ผิด — verify แล้ว 15 แถวที่สงสัยถูกจริง |
| J | **PSK composition** (a_Cs/a_FA/x_Br) จาก 2 แหล่งอิสระ: สูตรนาม + คำนวณจาก precursor (โมล/มวล÷MW) cross-check ตรง 13/15 paper — ครบ 44/44 devices |
| K | solvent normalize 27 แบบ → 16 ระบบ, แตก solvent_1/2 + ratio ตัวเลข |
| L | stability เพิ่ม: retention 24→28 devices (เจอหลักฐาน Excel percent format จริง ไม่ใช่เดา), environment 22 แบบ → 6 กลุ่ม |

**บั๊กที่เจอและแก้ระหว่างทาง**
- `layer_id` เป็น `'1.0'` vs `'1'` → join ได้ 0 แถวแบบเงียบ
- regex `contains("m2 V")` แมตช์ `cm2 V-1 s-1` ผิด (นับ 7 ที่ถูกคือ 3)
- `has_antisolvent` ใน eda_prepare ไม่เคย match (canonical เปลี่ยนชื่อเป็น `AntiSolvent`)

## ขั้น 2.5 — `after_col_prepare`: ข้อเท็จจริงที่กำหนดทิศ ML

- `PCE = Voc × Jsc × FF / 100` ยืนยันจากข้อมูลจริง (corr 0.996) → **เทรน PCE ตรงไม่มีข้อมูลใหม่** ให้เทรน component แยกแล้วคูณ
- `grain_size` ใช้ได้จริง 13/44 (paper ไม่รายงาน ไม่ใช่กรอกตก) → ใช้ **VTFL แทน** (28 devices; ยืนยันภายหลัง rho +0.76 กับ trap_density)

## ขั้น 3 — `recheck_comlumn_preapare`: prepared → prepared_v2

| § | ทำอะไร |
|---|---|
| 1-3 | `layer_kind` (main/coating), ธง device: `is_coated`, `coat_htl/psk/etl`, `anneal_htl/psk` |
| 4.0 | **PARAM_FIX 8 แถว** — parameter ตัวเลขติดกัน/comma หาย แก้ด้วยค่ายืนยันจาก sibling sample ใน Raw Excel (เช่น `27010800` → `270, 10800`) + คอลัมน์ `param_fixed` กำกับ / เหลือ 1 แถวกำกวม (`100108000`) ติดธงรอเปิด paper |
| 4 | แตก `parameter`+`unit` (คั่น comma จับคู่ตามตำแหน่ง) → **53 knob columns** ชื่อ `process_type__unit` |
| 6 | รวมหน่วยเวลา → `*__time_s` (sec+min×60+hr×3600) |
| 6.1 | AntiSolvent: รวม ml+uL → `vol_uL` / เวลาหยดมี **2 จุดอ้างอิง** (ลบ = ก่อนจบ spin, บวก = หลังเริ่ม) เก็บ `timing_ref` กำกับ |
| 7 | Y แบบ **variant-consistent**: เลือก Voc/Jsc/FF/PCE จากชุดเดียวกัน (champion 23 > plain 16 > average 4) แก้บั๊ก max ปน Champion/Average — `pce = voc×jsc×ff/100` เหลือ err ≤ 0.503 (rounding) จากเดิม 1.64 |
| 8 | stability index: `degradation_rate` (%/hr), `retention_25c` (Y เสถียร, 14 dev) |
| 8.5 | **export `psk_composition`** (เดิมคำนวณใน column_prepare แต่ไม่เคยเขียนดิสก์) — ไอออนที่ไม่อยู่ในสูตร = 0 จริง ไม่ใช่ missing |
| 9 | X ระดับ device: `psk_spin_rpm`, `psk_anneal_C`, `psk_anneal_time_s`, `antisolvent_vol_uL/timing_s`, `htl_anneal_C` → merge เข้า `target_table` |
| 10 | export + gate จากไฟล์บนดิสก์ (layers ไม่พอง, 44 unique, pce consistency) |

**บั๊กที่แก้รอบตรวจ**: layers merge บน (device_id, layer_id) ทำแถวพอง 352→356 (เคส layer 7 มี PCBM+BCP สองแถว) → เปลี่ยนเป็น map ตรงจาก layer_id

## ขั้น 4 — `explore_prepared_v2`: EDA ชุดสุดท้าย

- **confound ระดับ paper ใหญ่จริง**: variance PCE ระหว่าง paper ~3 เท่าของใน paper → LOPO จำเป็น
- coverage 3 ชั้น: JV+knob+composition (40-44) / mediator+antisolvent (20-35) / stability (13-14)
- missing เป็นระดับ paper (**MNAR**) — ห้าม impute ข้าม paper
- คู่ knob-Y เด่น (naive spearman ยังไม่คุม paper — เป็น candidate เข้า LOPO เท่านั้น):
  `htl_anneal_C~ff` −0.74, `htl_anneal_C~voc` −0.64, `psk_spin_rpm~vtfl` +0.57, `antisolvent_timing~voc` +0.52, `psk_anneal_C~vtfl` −0.38
- sanity ผ่าน: x_Br↑ → Voc↑ (ตาม bandgap physics)

---

## สถานะไฟล์ `processed/prepared_v2/`

| ไฟล์ | ขนาด | หมายเหตุ |
|---|---|---|
| `target_table.csv` | 44×29 | **ตารางหลักสำหรับ modeling**: Y (jv_variant กำกับ) + mediator + stability + X knob + composition |
| `process.csv` | 702×71 | ทุก process ราย layer + 53 knob columns + param_fixed/param_note |
| `layers.csv` | 352×11 | + layer_kind |
| `devices.csv` | 44×11 | + ธงเคลือบ/anneal |
| `psk_composition.csv` | 44×11 | a/x site ครบ + comp_source + cross_check |
| `device_layer_flags.csv` | 44×8 | ธงแยกไฟล์ |
| `materials.csv` | 543×13 | + solvent_1/2/ratio/system |
| `properties.csv` | 563×16 | base_property + value_parsed |
| `stability.csv` | 62×20 | parsed + stress_group |
| `vendors.csv` | 644×8 | + purity_parsed |

## รายไฟล์ `prepared_v2/` — คอลัมน์ + วิธีใช้ + จุดระวัง

### `target_table.csv` (44×29) — 1 แถว = 1 device ⭐ ตารางหลัก modeling
- **key**: `device_id` / paper แยกได้จาก `device_id.rsplit("_Sample", 1)[0]` (ใช้เป็น group ใน LOPO)
- **ธง layer** (8): `is_coated`, `n_coating_layers`, `coat_htl/psk/etl`, `anneal_htl/psk`
- **Y** (5): `pce voc jsc ff` (43 dev) + `jv_variant` บอกที่มา (champion 23 / plain 16 / average 4) — 4 ค่ามาจากชุดเดียวกันเสมอ
- **mediator** (4): `vtfl` (28) ตัวหลัก, `trap_density` (22), `rs` (22), `rrec` (15)
- **stability** (2): `retention_25c` (14), `degradation_rate` (14)
- **X knob** (7): `psk_spin_rpm` (43), `psk_anneal_C` (40), `psk_anneal_time_s` (42), `antisolvent_vol_uL` (35), `antisolvent_timing_s`+`timing_ref` (32), `htl_anneal_C` (41)
- **X ควบคุม** (3): `a_Cs a_FA x_Br` (44 — ไอออนไม่อยู่ในสูตร = 0 จริง)
- ⚠️ `antisolvent_timing_s`: ลบ = ก่อนจบ spin, บวก = หลังเริ่ม — แยกกลุ่มตาม `timing_ref` ก่อนใช้ / `pce` ขาด 1 dev ที่ variant ไม่รายงาน ใช้ voc×jsc×ff/100 แทนได้

### `process.csv` (702×71) — 1 แถว = 1 ขั้นตอน process ต่อ layer
- **key**: `device_id + layer_id + step_order` / คอลัมน์ดิบ: `process_type description parameter unit note`
- **53 knob columns** ชื่อ `process_type__unit` (เช่น `Annealing__C`, `Spin coating__RPM`) — แถกจาก parameter/unit จับคู่ตามตำแหน่ง comma
- **derived**: `*__time_s` (รวม sec/min/hr), `AntiSolvent__vol_uL/timing_s/timing_ref`
- **audit**: `param_fixed` (8 แถวที่แก้จาก PARAM_FIX), `param_note` (1 แถวกำกวมที่เหลือ)
- ⚠️ `Annealing__C` ปนทุก layer — **ต้อง filter `layer_id` ก่อนใช้** (HTL 270-450°C, PSK 100-150°C) / `source_sheet == "NiOx synthesis"` ห้ามใช้เป็น feature (confound กับ DOI เดียว)

### `properties.csv` (563×16) — 1 แถว = 1 ค่าที่ paper รายงาน
- **key**: `device_id` + `base_property` + `stat/scan/layer/carrier` (แตกมาจากชื่อ property เต็ม)
- ใช้ `value_parsed` เสมอ (ไม่ใช่ `value` ดิบ) / `unit_corrected/value_corrected/unit_fix_note` = ร่องรอยแก้ unit
- ⚠️ device เดียวมีหลายแถวต่อ property (Champion/Average/scan) — pivot เองต้องเลือก variant ให้ตรงกัน (target_table ทำให้แล้ว)

### `psk_composition.csv` (44×11) — 1 แถว = 1 device
- `a_Cs a_FA a_MA` (A-site) + `x_I x_Br` (X-site) รวมฝั่งละ 1 / `b_site` = Pb ทั้งหมด
- `comp_source`: formula 42, precursor 2 / `cross_check`: เทียบ 2 แหล่งอิสระ / `precursor_status`: เหตุที่คำนวณจาก precursor ได้/ไม่ได้
- ⚠️ NaN ใน a_/x_ = ไอออนไม่อยู่ในสูตร (=0) — target_table fill 0 ให้แล้ว ไฟล์นี้คงค่าดิบ

### `layers.csv` (352×11) — 1 แถว = 1 วัสดุใน layer
- **key**: `device_id + layer_id` — **ไม่ unique** (layer 7 มีได้ 2 วัสดุ PCBM+BCP, `layer_type` = "ETL treatment (1)/(2)")
- `thickness_nm_parsed/min/max/note` (แปลง Å→nm แล้ว), `material_family`, `layer_kind` (main/coating/NaN ถ้าไม่มี material)

### `stability.csv` (62×20) — 1 แถว = 1 การทดสอบ (device มีได้หลายเงื่อนไข, 35 dev)
- ใช้ `*_parsed` + `stress_group` (6 กลุ่มจาก environment 22 แบบ) / `retention_pct_censor` = ค่า `<55` แบบ censored
- ⚠️ เทียบ retention ข้ามเงื่อนไขตรง ๆ ไม่ได้ (25-85°C, 1-5000 ชม.) — ใช้ retention@25C bin หรือ degradation_rate ใน target_table แทน

### `materials.csv` (543×13) — 1 แถว = 1 สารตั้งต้นต่อ layer
- `amount + unit` (โมลหรือมวล — ใช้คำนวณ composition), `solvent_1/2 solvent_ratio solvent_system solvent_note` (normalize 27→16 ระบบ)

### `vendors.csv` (644×8) — 1 แถว = 1 สารต่อ vendor
- `purity_pct purity_qualifier purity_kind` parse แล้ว — ฝั่ง provenance ไม่ใช่ feature

### `devices.csv` (44×11) / `device_layer_flags.csv` (44×8)
- devices = ทะเบียน device (`doi_raw sample_no source_file`) + ธง layer / flags = ธงชุดเดียวกันแยกไฟล์ (ซ้ำกับใน target_table — ใช้ตัวไหนก็ได้)

## ค้าง / ระวัง

1. **`100108000`** (admi S3, L4 Annealing) — S1=`100, 1800`, S4=`100, 10800` ขัดกัน ต้องเปิด paper ยืนยันก่อนแก้ (แถวเดียวที่ยังติดธง `param_note`)
2. `antisolvent_timing_s` มี 2 จุดอ้างอิง — ใช้ per-group (`timing_ref`) หรือทำ binary feature
3. Thermal evaporation rate เป็นช่วง (`0.5-1 Å/s`) parse ไม่ได้ 12 แถว — ไม่ใช่ knob หลัก ปล่อยไว้
4. stability n=14 — exploratory เท่านั้น
5. **โมเดลถัดไป**: RandomForest + SHAP, LeaveOneGroupOut(paper) 15 fold, เทรน voc/jsc/ff แยกแล้วคูณเป็น pce, รายงาน mean±SD + bootstrap — ผลทั้งหมด = hypothesis สำหรับ wet-lab validation
