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

## พื้นฐาน domain — อ่านก่อนถ้าไม่ได้มาสายวัสดุ/เคมี

### เซลล์แสงอาทิตย์ perovskite ประกอบจากชั้นอะไร

เซลล์เป็นแซนด์วิชของฟิล์มบาง ๆ ซ้อนกัน แสงวิ่งทะลุจากด้านบนลงไปถึงชั้นดูดแสงตรงกลาง:

| layer_id (ในข้อมูลเรา) | ชั้น | ทำหน้าที่ | วัสดุที่เจอบ่อยใน dataset |
|---|---|---|---|
| 1 | Anode | ขั้วไฟฟ้าโปร่งแสง (แสงเข้าทางนี้) | ITO, FTO |
| 2 | HTL (Hole Transport Layer) | รับ "โฮล" (ประจุบวก) จากชั้นดูดแสงส่งไปขั้ว | NiOx, PEDOT:PSS, SAM |
| **3** | HTL treatment | เคลือบผิว HTL ลด defect รอยต่อ | — |
| 4 | **PSK (Perovskite)** | **ชั้นดูดแสง — พระเอกของทั้งเซลล์** ดูดโฟตอนแล้วสร้างคู่อิเล็กตรอน-โฮล | สูตร ABX₃ (ดูหัวข้อถัดไป) |
| **5** | PSK treatment | เคลือบผิว perovskite (passivation) อุด defect ที่ผิว | — |
| 6 | ETL (Electron Transport Layer) | รับอิเล็กตรอน (ประจุลบ) ส่งไปอีกขั้ว | C60, PCBM, SnO2 |
| **7** | ETL treatment | เคลือบ/บัฟเฟอร์ก่อนขั้วโลหะ | BCP |
| 8 | Cathode | ขั้วโลหะ | Ag, Au, Cu |

หลักการ: แสง → PSK สร้างประจุ → HTL ดูดโฮลไปทางหนึ่ง, ETL ดูดอิเล็กตรอนไปอีกทาง → เกิดกระแสไฟ ถ้าประจุวิ่งไปไม่ถึงขั้ว (ไปตายที่ defect ระหว่างทาง) = พลังงานหายเป็นความร้อน — เกือบทุกอย่างที่เราวิเคราะห์วนอยู่กับ "ทำยังไงให้ defect น้อย"

### ชั้นเคลือบผิว (layer คี่ 3 / 5 / 7) — เคลือบชั้นไหน ทำหน้าที่อะไร

**Convention ที่ dataset ใช้ (ยืนยันจากวัสดุจริงครบทั้ง 3 ตำแหน่ง):**

เซลล์ถูกสร้างแบบ**วางทีละชั้นจากล่างขึ้นบน** ตามลำดับ layer_id ดังนั้น**ชั้นเคลือบหมายเลข N คือฟิล์มบาง ๆ ที่ deposit ทับ "ผิวบนของชั้นหลักก่อนหน้า (N−1)" เสมอ** — ทำชั้นหลักเสร็จ เคลือบผิวมัน แล้วค่อยวางชั้นหลักถัดไป (N+1) ทับลงไป ไม่มีกรณี "เคลือบข้ามชั้น" เพราะเป็นไปไม่ได้ทางกายภาพ

สองมุมมองที่ถูกทั้งคู่ (คนละมุมของสิ่งเดียวกัน):
- มุม **การผลิต**: L3 เคลือบ HTL, L5 เคลือบ PSK, L7 เคลือบ ETL (เคลือบชั้นล่างของตัวเอง)
- มุม **หน้าที่**: แต่ละตัวเป็น "interface layer" ของ**รอยต่อ**ระหว่างชั้นล่างกับชั้นบน — L3 ทำงานที่รอยต่อ HTL↔PSK, L5 ที่ PSK↔ETL, L7 ที่ ETL↔โลหะ

**ทำไมชั้นบาง ๆ พวกนี้สำคัญ**: defect ที่ฆ่าประสิทธิภาพไม่ได้กระจายทั่วเนื้อฟิล์ม แต่**กระจุกอยู่ที่ผิวและรอยต่อ** (พันธะขาด, ไอออนหลุด, อะตอมโลหะแพร่ลงมา) — recombination ที่รอยต่อคือตัวกิน Voc/FF หลัก งานเคลือบผิว (เรียกรวมว่า passivation / interface engineering) จึงเป็นสนามแข่งใหญ่ของวงการ และเป็น knob ที่ dataset นี้เก็บไว้เป็น `coat_*`

**รายละเอียดทีละตำแหน่ง (วัสดุจริงจาก dataset + กลไกจาก literature):**

| | L3 — HTL treatment | L5 — PSK treatment | L7 — ETL treatment/buffer |
|---|---|---|---|
| เคลือบผิวของ | L2 (NiOx) | L4 (perovskite) | L6 (C60/PCBM) |
| ทำงานที่รอยต่อ | HTL ↔ PSK ("buried interface") | PSK ↔ ETL | ETL ↔ ขั้วโลหะ |
| มีใน dataset | 26/44 dev (+3 additive) | 22/44 dev | **44/44 dev** |
| วัสดุที่เจอ | SAM (Me-2PACz, Me-4PACz), Al₂O₃, PTAA+Al₂O₃, ionic liquid (EMIMI), BDT-POZ | **PEAI 15/22**, PDI, CF3-PEAI/MAI, PC61BM | **BCP 37/44**, PEI, SnO2 |
| ความหนา | บางระดับโมเลกุล (SAM = ชั้นเดียว) | บางมาก | ~5-10 nm (median 9) |

- **L3 (บนผิว NiOx ก่อนวาง perovskite)** — 3 หน้าที่: (1) ปรับพลังงานผิว/ความเปียก ให้สารละลาย perovskite เกาะทั่วแผ่น ฟิล์มไม่เป็นรู (2) จัดระดับพลังงานให้ดึงโฮลเข้า HTL ลื่นขึ้น (3) กั้นปฏิกิริยาเคมีระหว่างผิว NiOx (Ni³⁺) กับ perovskite ซึ่งกิน Voc — จุดนี้เรียก "buried interface" เพราะพอวาง perovskite ทับแล้ววัดตรง ๆ ไม่ได้อีก เป็นเหตุที่ paper สาย SAM เน้นหนักช่วงหลัง
- **L5 (บนผิว perovskite ก่อนวาง ETL)** — ตัวหลักคือ **PEAI** (phenethylammonium iodide): โมเลกุล PEA⁺ ทำปฏิกิริยากับ PbI₂ ส่วนเกินและ defect ที่ผิว เกิดชั้น **2D perovskite (PEA)₂PbI₄** บาง ๆ ทับหน้า — อุด undercoordinated Pb / iodide vacancy (Voc ขึ้น) + ผิว 2D ไม่ชอบน้ำช่วยกันความชื้น ข้อควรรู้จาก literature: ชั้น 2D นี้**ไม่เสถียรนัก** อาจ decay เร็วช่วงแรกของการทดสอบ stability — เวลาตีความ retention ของ device กลุ่ม `coat_psk=True` ให้นึกถึงข้อนี้
- **L7 (บน ETL ก่อนระเหยขั้วโลหะ)** — ตัวหลักคือ **BCP** (bathocuproine) ทำ 2 อย่าง: (1) กันอะตอมโลหะ (Ag/Cu) แพร่ทะลุ ETL ลงไปช็อตกับ perovskite (2) เปลี่ยน contact ระหว่าง C60 กับโลหะจาก Schottky (มี barrier กักประจุ) เป็น **ohmic** (ไหลลื่น) ลด charge accumulation — FF ดีขึ้น จุดอ่อน: BCP **aggregate ที่ ~85°C** เป็นความเสี่ยง thermal stability ที่ paper หลายฉบับพยายามแก้ (เหตุที่เจอ PEI/SnO2 มาแทนในบาง device)

**ชั้นเคลือบมาคู่กับวิธีเคลือบเสมอ (ตรวจแล้ว 92/95 ครั้ง)**

`layers.csv` บอกว่า**เคลือบอะไร** (material) ส่วน `process.csv` บอกว่า**เคลือบยังไง** (process_type + parameter) — คนละตารางแต่ผูกกันด้วย `device_id + layer_id` เดียวกัน เช่น L5 ของ device หนึ่ง: layers บอก `PEAI`, process บอก `Spin coating, 5000 RPM, 30 sec`

ผลตรวจการจับคู่:

| | มี material | มี process จริง (process_type ไม่ว่าง) | ตรงกัน | ไม่ตรง |
|---|---|---|---|---|
| L3 (HTL treat) | 29 dev (material) | 26 | 26 | **3** (= additive) |
| L5 (PSK treat) | 22 dev | 22 | 22 | 0 |
| L7 (ETL treat) | 44 dev | 44 | 44 | 0 |

**3 เคสที่ไม่ตรง = ไม่ใช่ชั้นเคลือบจริง แต่เป็น additive** (ไม่ใช่ข้อมูลหาย): paper `10.1002admi.202500231` (Sample2-4) ใส่ material `EMIMI-1%/2%/4%` ไว้ที่ layer_id=3 แต่ **EMIMI ถูกผสมลงในน้ำหมึก NiOx แล้ว spin ครั้งเดียว** ไม่ได้เคลือบทับเป็นชั้นแยก หลักฐาน:
- process ของ device เหล่านี้ที่ L2 มีขั้น **`Mix` description = "EMIMI"** เป็น step_order 1 ก่อน Sonication → Spin coating → Annealing
- device ควบคุม (Sample1, ไม่มี EMIMI) มีลำดับเดียวกันเป๊ะ **ยกเว้นไม่มีขั้น Mix**
- ไม่มี spin coating ครั้งที่สองที่ไหนเลย — ถ้าเคลือบทับจริงต้องมี

จึงเป็น**ข้อจำกัดของ schema** (ช่อง L3/L5 ถูกใช้บันทึกทั้ง "เคลือบทับ" และ "ผสมในน้ำหมึก") ไม่ใช่ data entry ผิด → แก้ด้วยการเพิ่มคอลัมน์แยกชนิด (ดูด้านล่าง) ไม่ใช่ย้ายข้อมูล

**คอลัมน์แยกชนิด `htl_mod_kind` / `psk_mod_kind`** (recheck §2.1, อยู่ใน device_layer_flags + target_table): ค่า `"coating"` / `"additive"` / ว่าง — เกณฑ์ data-driven คือ *มี material ที่ L3/L5 แต่ไม่มี process ที่ layer เดียวกัน = additive* (ชั้นเคลือบจริงต้องมีขั้นตอน deposit เสมอ)

| | coating | additive | ไม่มีการดัดแปลง |
|---|---|---|---|
| `htl_mod_kind` (L3) | 26 | **3** (EMIMI) | 15 |
| `psk_mod_kind` (L5) | 22 | 0 | 22 |

⚠️ **ข้อควรระวังตอน join**: `process.csv` มีแถว placeholder ที่ `process_type` เป็น NaN 28 แถว (L1=5, L3=13, L5=10 — ไม่มี parameter/unit เลยสักแถว เป็นช่องว่างของ template) ถ้านับ device จาก `layer_id` อย่างเดียวจะได้ L3=38, L5=32 ซึ่งเกินจริง **ต้อง filter `process_type.notna()` ก่อนเสมอ** — ไม่ต้องลบแถวออก เพราะมันบอกว่า "ช่องนี้มีอยู่ใน template แต่ไม่มีข้อมูล" ซึ่งต่างจาก "ไม่มีแถวเลย"

### ตัดสินใจแล้ว: **ไม่ดึงพารามิเตอร์การเคลือบ (L3/L5/L7) เป็น feature**

ข้อมูลมีครบและ join ได้ (L3 26 dev, L5 22 dev, L7 44 dev มี `Spin coating__RPM`, `Annealing__C` ฯลฯ) แต่**ตั้งใจไม่ทำ** — ไม่ใช่ลืม

เหตุผลที่ตัดสินใจ (ตรวจข้อมูลจริงแล้ว 2026-08-04):

| knob | จำนวน paper | ค่าต่างกันทั้งหมด | **paper ที่แปรค่าภายในตัวเอง** |
|---|---|---|---|
| L3 `Spin coating__RPM` | 10 | 5 | 1 |
| L3 `Annealing__C` | 11 | 4 | 0 |
| L5 `Spin coating__RPM` | 8 | 4 | **0** |
| L5 `Annealing__C` | 4 | 2 | 0 |
| L7 `Spin coating__RPM` | 7 | 5 | 0 |

1. **คอลัมน์ขวาสุดคือตัวตัดสิน** — เกือบทุก paper ใช้ค่าเดียวกันทุก sample แปลว่าค่าเหล่านี้ไม่แปรผัน*ภายใน* paper ใต้ LOPO โมเดลแยกไม่ออกว่าเป็นผลของ knob หรือของ paper → กลายเป็น **paper fingerprint** แบบเดียวกับ NiOx synthesis
   (ต่างจาก `psk_spin_rpm` / `antisolvent_timing_s` ที่ผู้เขียนจงใจแปรระหว่าง sample เพราะเป็นตัวแปรที่เขาศึกษา — จึงดึงมาใช้)
2. **สิ่งที่แปรจริงถูกจับไว้แล้ว** — สิ่งที่ paper ตั้งใจแปรคือ*ชนิดสาร* (PEAI มี/ไม่มี, EMIMI 1%/2%/4%) ไม่ใช่พารามิเตอร์การเคลือบ → `coat_psk`, `coat_htl`, `htl_mod_kind`, `psk_mod_kind` ครอบไว้หมดแล้ว
3. **N=44 กับ 15 fold** — เพิ่ม feature ที่ไม่ให้ข้อมูลใหม่คือกิน degrees of freedom เปล่า และเพิ่มโอกาส false positive ตอนอ่าน SHAP

**เมื่อไหร่ถึงค่อยกลับมาพิจารณา**: หลังมี baseline model แล้วทำ error analysis — ถ้าพบว่า residual เกาะกลุ่มกับวิธีเคลือบ ค่อยเพิ่มแบบ **categorical** (`psk_coat_method` = spin / evaporation / none) ไม่ใช่ดึงตัวเลข RPM ตรง ๆ

**วิธี join ถ้าถึงเวลานั้นจริง:**
```python
proc = proc[(proc.source_sheet == "Process") & proc.process_type.notna()]
coat = layers[layers.layer_id.isin([3, 5, 7])].merge(
    proc[proc.layer_id.isin([3, 5, 7])], on=["device_id", "layer_id"], how="left")
```
ครอบคลุมทุกเคสที่เป็น coating จริง — 3 เคส additive ไม่มีวิธีเคลือบให้ดึงเพราะไม่ได้เคลือบ ให้ไปดูขั้น `Mix` ที่ L2 แทน

**การใช้ในข้อมูล/โมเดล:**
- `coat_htl`/`coat_psk`/`n_coating_layers` **นับเฉพาะเคลือบทับจริง** (additive ไม่นับ เพราะไม่ได้เพิ่มชั้น) → `coat_htl` = 26 ไม่ใช่ 29 / ถ้าอยากได้ "HTL ถูกดัดแปลงไหม" (รวม additive) ใช้ `htl_mod_kind.notna()`
- `layers.csv` คอลัมน์ `layer_kind` ก็แยกเช่นกัน: main 218 / coating 94 / **additive 3** / ว่าง 37
- ธง `coat_htl` (L3), `coat_psk` (L5), `coat_etl` (L7) + `is_coated`, `n_coating_layers` — อยู่ใน devices / device_layer_flags / target_table (สร้างใน recheck §2)
- ⚠️ **`coat_etl` และ `is_coated` = True ทั้ง 44 เครื่อง** (ทุก device มี L7 อย่างน้อย BCP) → variance 0 **ใช้เป็น feature ไม่ได้** เก็บไว้เป็น sanity check เท่านั้น
- ตัวที่มี variance จริง: `coat_htl` (29/44), `coat_psk` (22/44) — และเนื่องจาก L5 เกือบทั้งหมดคือ PEAI, `coat_psk` ตีความได้เกือบตรงตัวว่า "มี PEAI passivation หรือไม่"
- ตัวอย่าง stack เต็มอ่านจากล่างขึ้นบน: `ITO / NiOx / SAM / perovskite / PEAI / C60 / BCP / Ag` = layer 1-8 เรียงตรง

อ้างอิง: [PEAI surface passivation (2D layer)](https://www.researchgate.net/publication/332132994_Surface_passivation_of_perovskite_film_for_efficient_solar_cells) · [PEAI ใน p-i-n PSC](https://www.sciencedirect.com/science/article/abs/pii/S0925838824006479) · [BCP eliminating charge accumulation / ohmic contact](https://pubs.rsc.org/en/content/articlelanding/2017/ra/c7ra06365b) · [Design of BCP buffer layer](https://pubs.aip.org/aip/apm/article/7/3/031117/1024797/Design-of-BCP-buffer-layer-for-inverted-perovskite)

### ค่าที่วัดฝั่ง Y (ผลลัพธ์ของเซลล์)

- **Voc** (V, open-circuit voltage) — แรงดันสูงสุดตอนไม่จ่ายกระแส บอก "คุณภาพ" พลังงานของประจุ ยิ่ง defect/recombination เยอะ Voc ยิ่งตก
- **Jsc** (mA cm⁻², short-circuit current) — กระแสสูงสุดตอนลัดวงจร บอก "ปริมาณ" แสงที่เก็บเกี่ยวได้ ขึ้นกับ bandgap และความหนาชั้น PSK
- **FF** (%, fill factor) — ความ "เหลี่ยม" ของกราฟ J-V บอกการสูญเสียจากความต้านทานภายใน/กระแสรั่ว
- **PCE** (%) = Voc × Jsc × FF / 100 — ประสิทธิภาพรวม **เป็นแค่ผลคูณของสามตัวบน** (เราพิสูจน์ในข้อมูลแล้ว corr 0.996) จึงเทรนสามตัวแยกแล้วคูณ ไม่เทรน PCE ตรง
- **champion vs average** — paper มักรายงานทั้ง "เซลล์ตัวที่ดีที่สุด" (champion) และ "ค่าเฉลี่ยหลายเซลล์" (average) ปนกันไม่ได้ → ที่มาของ `jv_variant`

### Perovskite ABX₃ — วิธีอ่านสูตร

โครงผลึกทั่วไปคือ **ABX₃**: **A** = ไอออนบวกตัวใหญ่, **B** = โลหะ (ในข้อมูลเรา = ตะกั่ว Pb ทั้งหมด), **X** = halide สัดส่วนภายใน site เดียวกันรวมกันได้ 1 เสมอ

ตัวอย่างจริงจาก dataset: `(Cs0.05FA0.79MA0.16)Pb(I0.87Br0.13)3` อ่านว่า
- A-site: Cs 5% + FA 79% + MA 16% (สามตัวนี้คือ `a_Cs a_FA a_MA`)
- B-site: Pb ล้วน (`b_site`)
- X-site: I 87% + Br 13% (`x_I x_Br`) — เลข 3 ท้ายวงเล็บคือจำนวน X ต่อหน่วยสูตร เรา normalize เป็นสัดส่วนรวม 1 แล้ว

ตัวย่อไอออน: **Cs** = ซีเซียม (อนินทรีย์), **MA** = methylammonium CH₃NH₃⁺ (อินทรีย์), **FA** = formamidinium CH(NH₂)₂⁺ (อินทรีย์), **I/Br/Cl** = ไอโอไดด์/โบรไมด์/คลอไรด์

ทำไมต้องผสมหลายตัว — แต่ละไอออนให้คนละอย่าง:

| เพิ่มอะไร | ได้อะไร | แลกกับอะไร |
|---|---|---|
| `a_FA` ↑ | bandgap แคบ ดูดแสงได้กว้าง → Jsc ดี | FA ล้วนไม่เสถียรที่อุณหภูมิห้อง (เปลี่ยนจาก phase ดำที่ทำงาน → phase เหลืองที่ไม่ทำงาน) |
| `a_MA` ↑ | ตกผลึกง่าย ฟิล์มเรียบ | ทนร้อนแย่สุด — สลายตัว/ระเหยเมื่ออบนาน |
| `a_Cs` ↑ (นิยม 5-15%) | อะตอมเล็ก ช่วย "ล็อค" โครงผลึกให้อยู่ phase ดำ → stability ดีขึ้น | ใส่มากไป phase แยก |
| `x_Br` ↑ | bandgap กว้างขึ้น → **Voc สูงขึ้น** (เห็นจริงใน EDA §7) + ช่วยตกผลึก | Jsc ลด (ดูดแสงช่วงแคบลง) และเกินราว 20% เสี่ยง halide segregation ใต้แสง |
| Cl (เป็น MACl) | additive ช่วยเกรนโตตอน anneal | **ระเหยหายตอนอบ ไม่อยู่ในฟิล์มจริง** — เหตุที่โค้ดตัด MACl ทิ้ง (`VOLATILE`) |

**นี่คือเหตุที่ composition เป็น confounder ตัวใหญ่สุด**: สัดส่วนพวกนี้กระทบ Voc/Jsc/stability ตรง ๆ ทางฟิสิกส์ ถ้า paper A ใช้ Br เยอะ + spin เร็ว แล้วโมเดลไม่รู้เรื่อง Br มันจะสรุปผิดว่า "spin เร็วทำให้ Voc สูง" → จึงต้องใส่ `a_Cs a_FA x_Br` เข้าโมเดลเป็นตัวควบคุมเสมอ

### Precursor คืออะไร ทำไมใช้เช็คสูตรได้

ห้องแล็บไม่ได้ซื้อ perovskite สำเร็จ — เขาซื้อ**เกลือไอออน** (เรียก precursor เช่น FAI, MAI, MABr, CsI, PbI₂, PbBr₂) มาชั่ง ละลายในตัวทำละลาย (DMF/DMSO) แล้ว spin coat ให้ตกผลึกเป็น perovskite บนแผ่น

แต่ละเกลือสลายให้ไอออนตามสูตรเคมี: PbI₂ → Pb²⁺ + 2 I⁻, FAI → FA⁺ + I⁻ ฯลฯ ดังนั้นถ้ารู้ว่าใส่เกลืออะไรกี่โมล ก็คำนวณย้อนได้ว่าฟิล์มควรมีสัดส่วนไอออนเท่าไหร่:
- paper บอกมวล (mg) → หารด้วยมวลโมเลกุล (MW ในโค้ด) = โมล
- paper บอกความเข้มข้น (M/mmol) → ใช้ตรง
- รวมโมลของแต่ละไอออนแยกตาม site แล้ว normalize

ผลที่ได้เป็น**แหล่งข้อมูลอิสระแหล่งที่สอง** ไว้เทียบกับสูตรที่ paper เขียนในชื่อชั้น — ตรงกัน = มั่นใจได้ (`cross_check = "ตรง"`) ส่วน `precursor_status = "ตกหล่น: MABr"` แปลว่ารายการสารของ device นั้นไม่มี/อ่าน MABr ไม่ได้ เลยคำนวณฝั่ง precursor ไม่ครบ — ใช้ค่าจากสูตรนามได้ตามปกติ แค่ขาดตัวเช็คซ้ำ

### Mediator — ตัวแปรกายภาพคั่นกลางระหว่าง process กับผลลัพธ์

สมมุติฐานหลักของงานนี้: **process knob → คุณภาพผลึก → ประสิทธิภาพ/เสถียรภาพ** ตัวกลางที่วัดได้:

- **grain size** — ขนาดเกรนผลึกในฟิล์ม เกรนใหญ่ = พื้นที่ขอบเกรนน้อย = ที่อยู่ของ defect น้อย (แกนของ RQ แต่ paper รายงานตัวเลขแค่ 13/44 → ใช้ตัวแทน)
- **trap density / VTFL** — วัดด้วยเทคนิค SCLC: ค่อย ๆ เพิ่มแรงดันจนกระแสกระโดดขึ้น จุดกระโดดเรียก V_TFL (trap-filled limit = แรงดันที่ต้องใช้ "เติม trap ให้เต็ม") **VTFL สูง = trap เยอะ = ฟิล์มแย่** — สัมพันธ์กับ trap density ตรงทฤษฎี และเราเห็นจริงในข้อมูล (rho +0.76) เลยใช้ VTFL (28 dev) แทน grain size
- **Rs / Rrec** — ความต้านทานอนุกรม / ความต้านทาน recombination จากการวัดอิมพีแดนซ์ (EIS): Rs ต่ำดี Rrec สูงดี

### Process knob — ปุ่มที่ห้องแล็บหมุนได้ (X ของเรา)

- **spin coating (RPM)** — เหวี่ยงสารละลายให้เป็นฟิล์มบาง ยิ่งเร็วฟิล์มยิ่งบาง/แห้งเร็ว กระทบจังหวะเกิดผลึก (nucleation)
- **antisolvent** — ระหว่าง spin หยดตัวทำละลายที่ perovskite *ไม่ละลาย* (เช่น chlorobenzene) ลงไป บังคับให้ตกผลึกพร้อมกันทั้งแผ่น ฟิล์มเนียนขึ้น — **จังหวะหยดสำคัญมาก** หน้าต่างแค่ไม่กี่วินาที (ที่มาของ `antisolvent_timing_s` และเหตุที่ convention บันทึกเวลา 2 แบบ: "วินาทีหลังเริ่ม spin" กับ "วินาทีก่อนจบ")
- **annealing (°C, เวลา)** — อบฟิล์มเพื่อไล่ solvent ที่เหลือ + ให้เกรนโต **PSK อบ 100-150°C** (ร้อนกว่านี้สารอินทรีย์ MA/FA สลาย) ส่วน **NiOx/HTL อบ 270-450°C** (เป็นออกไซด์อนินทรีย์ ทนร้อน) — คนละกลไกโดยสิ้นเชิง เหตุที่แยกคอลัมน์ `psk_anneal_C` / `htl_anneal_C` และห้ามรวม
- **treatment (layer 3/5/7)** — เคลือบผิวบาง ๆ อุด defect ที่รอยต่อ (passivation)

### Stability — ฝั่งเสถียรภาพ

- **retention** (%) — เหลือ PCE กี่ % หลังทดสอบไป t ชั่วโมงภายใต้เงื่อนไขหนึ่ง (อุณหภูมิ/ความชื้น/แสง) — device ใน dataset ถูกทดสอบคนละเงื่อนไข (25-85°C, 1-5000 ชม.) เทียบข้ามเงื่อนไขตรง ๆ ไม่ได้ จึงใช้เฉพาะกลุ่ม ~25°C (14 dev)
- **T80** — เวลาที่ PCE ตกถึง 80% ของค่าเริ่ม (มาตรฐานวงการ แต่ข้อมูลเรามีน้อยจนตัดทิ้ง)
- การเสื่อมไม่เชิงเส้น: ช่วงแรกตกเร็ว (burn-in) แล้วช้าลง — เหตุที่ `degradation_rate` (% ต่อชั่วโมง) เทียบข้าม timescale ตรง ๆ ไม่ได้

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
| 8.7 | ปิดช่องโหว่ **`value_parsed` ไม่ตาม `value_corrected`** — `column_prepare` §3.2 คำนวณ `value_parsed = to_numeric(value)` จากค่าดิบ ไม่เคยอ่าน `value_corrected` การแก้ที่เปลี่ยน**ตัวเลข** จึงหายเงียบ (hole mobility m²→cm² ×1e4, 3 แถว) ตอนนี้ซิงก์แล้ว + gate กันซ้ำ |
| 8.6 | `material_family` NaN จัดการตามบทบาท: PSK→มี composition แทน, L1/L2/L8 variance=0 ไม่ใช้, materials→มี `type` ครอบ — ดึงเฉพาะ **`etl_family`** (L6: PCBM 24 / C60 18 / BCP 2, ข้าม 9/5 paper) เข้า target_table |
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
| `target_table.csv` | 44×32 | **ตารางหลักสำหรับ modeling**: Y (jv_variant กำกับ) + mediator + stability + X knob + composition + etl_family |
| `process.csv` | 702×71 | ทุก process ราย layer + 53 knob columns + param_fixed/param_note |
| `layers.csv` | 352×11 | + layer_kind |
| `devices.csv` | 44×11 | + ธงเคลือบ/anneal |
| `psk_composition.csv` | 44×11 | a/x site ครบ + comp_source + cross_check |
| `device_layer_flags.csv` | 44×10 | ธงแยกไฟล์ |
| `materials.csv` | 543×13 | + solvent_1/2/ratio/system |
| `properties.csv` | 563×16 | base_property + value_parsed |
| `stability.csv` | 62×20 | parsed + stress_group |
| `vendors.csv` | 644×8 | + purity_parsed |

## รายไฟล์ `prepared_v2/` — คอลัมน์ + วิธีใช้ + จุดระวัง

### ทำไมต้องมี 3 ไฟล์ derived (ไฟล์ที่ recheck สร้างใหม่ ไม่ได้ mirror จาก `prepared/`)

**`target_table.csv` — จุดตัดสินใจทั้งหมดถูกฝังไว้ที่เดียว**
ML ต้องการ 1 แถว = 1 device แต่ข้อมูลดิบกระจายอยู่ 5 ตาราง (properties หลายแถวต่อ device, process ราย step, stability รายเงื่อนไข) ถ้าให้แต่ละ notebook pivot เอง จะพลาดซ้ำแบบบั๊ก Champion/Average ที่เคยเกิด — ไฟล์นี้จึง encode การตัดสินใจทุกข้อไว้แล้ว: เลือก variant ชุดเดียวกัน (`jv_variant`), fill 0 ให้ไอออนที่ไม่อยู่ในสูตร, แยก sign convention ของ antisolvent, knob เฉพาะ layer ที่ถูกต้อง
**อิทธิพล**: เป็นไฟล์เดียวที่ขั้น modeling อ่าน — ถ้าไฟล์นี้ผิด ทุกโมเดลผิดตาม และถ้าแก้กติกา (เช่น เปลี่ยน variant rule) ต้องแก้ที่ recheck §7 แล้ว regenerate ไม่ใช่แก้ปลายทาง

**`psk_composition.csv` — หลักฐานที่มาของ X ควบคุม**
สูตรเคมี (a_Cs/a_FA/x_Br) เป็น confounder ตัวใหญ่สุดของ process knob: paper ที่เปลี่ยน knob มักเปลี่ยนสูตรด้วย ถ้าไม่ใส่ composition เข้าโมเดล SHAP จะโทษ knob ทั้งที่จริงเป็นผลของสูตร — จึงต้องมี composition ครบ 44/44 และต้อง**พิสูจน์ได้ว่าค่าเชื่อถือได้**: ไฟล์นี้เก็บ `comp_source` (มาจากสูตรนามหรือคำนวณจาก precursor), `cross_check` (2 แหล่งอิสระตรงกันไหม), `precursor_status` (ทำไมบาง device คำนวณไม่ได้) + ไอออนครบทุกตัว (a_MA, x_I ที่ target_table ไม่ได้ดึง)
**อิทธิพล**: target_table ดึงไปแค่ 3 คอลัมน์ — ไฟล์นี้คือ audit trail สำหรับเขียน methods ใน paper/thesis และเป็นที่กลับมาดูเมื่อ reviewer ถามที่มาของตัวเลข

**`device_layer_flags.csv` — intermediate เพื่อ join เบา ๆ**
ธงสถาปัตยกรรม (เคลือบ/anneal) ระดับ device ที่ notebook อื่น (เช่น `after_col_prepare`) join ได้โดยไม่ต้องลาก `devices.csv` ทั้งตาราง — ข้อมูล**ซ้ำโดยตั้งใจ**กับใน `devices.csv` และ `target_table.csv` (ไม่มีคอลัมน์ที่มีเฉพาะไฟล์นี้) ถ้าอยากเข้มงวดลบได้โดยไม่เสียข้อมูล แต่เก็บไว้เพราะต้นทุนศูนย์
**อิทธิพล**: ต่ำสุดใน 3 ตัว — สะดวก ไม่ใช่จำเป็น

### `target_table.csv` (44×32) — 1 แถว = 1 device ⭐ ตารางหลัก modeling
- **key**: `device_id` / paper แยกได้จาก `device_id.rsplit("_Sample", 1)[0]` (ใช้เป็น group ใน LOPO)
- **ธง layer** (10): `is_coated`, `n_coating_layers`, `coat_htl/psk/etl`, `anneal_htl/psk` + `htl_mod_kind`/`psk_mod_kind` (coating/additive — ดูหัวข้อชั้นเคลือบผิว) — ⚠️ `is_coated`/`coat_etl` True ทั้ง 44 (ทุก device มี L7) variance 0 ใช้เป็น feature ไม่ได้ ตัวที่มี variance คือ `coat_htl` (26) กับ `coat_psk` (22)
- **Y** (5): `pce voc jsc ff` (43 dev) + `jv_variant` บอกที่มา (champion 23 / plain 16 / average 4) — 4 ค่ามาจากชุดเดียวกันเสมอ
- **mediator** (4): `vtfl` (28) ตัวหลัก, `trap_density` (22), `rs` (22), `rrec` (15)
- **stability** (2): `retention_25c` (14), `degradation_rate` (14)
- **X knob** (7): `psk_spin_rpm` (43), `psk_anneal_C` (40), `psk_anneal_time_s` (42), `antisolvent_vol_uL` (35), `antisolvent_timing_s`+`timing_ref` (32), `htl_anneal_C` (41)
- **X ควบคุม** (4): `a_Cs a_FA x_Br` (44 — ไอออนไม่อยู่ในสูตร = 0 จริง) + `etl_family` (44, categorical: PCBM/C60/BCP — ชนิด ETL กระทบรอยต่อ/การเก็บอิเล็กตรอน ต้องคุมเหมือน composition; ⚠️ BCP มีแค่ 2 dev จาก paper เดียว ถ้า SHAP ชี้ BCP อย่าเชื่อ)
- ⚠️ `antisolvent_timing_s`: ลบ = ก่อนจบ spin, บวก = หลังเริ่ม — แยกกลุ่มตาม `timing_ref` ก่อนใช้ / `pce` ขาด 1 dev ที่ variant ไม่รายงาน ใช้ voc×jsc×ff/100 แทนได้

### `process.csv` (702×71) — 1 แถว = 1 ขั้นตอน process ต่อ layer
- **key**: `device_id + layer_id + step_order` / คอลัมน์ดิบ: `process_type description parameter unit note`
- **53 knob columns** ชื่อ `process_type__unit` (เช่น `Annealing__C`, `Spin coating__RPM`) — แถกจาก parameter/unit จับคู่ตามตำแหน่ง comma
- **derived**: `*__time_s` (รวม sec/min/hr), `AntiSolvent__vol_uL/timing_s/timing_ref`
- **audit**: `param_fixed` (8 แถวที่แก้จาก PARAM_FIX), `param_note` (1 แถวกำกวมที่เหลือ)
- ⚠️ `Annealing__C` ปนทุก layer — **ต้อง filter `layer_id` ก่อนใช้** (HTL 270-450°C, PSK 100-150°C)
- ⚠️ **`source_sheet` มี 2 ค่า**: `Process` 682 แถว (ขั้นตอนสร้างเซลล์จริง) กับ `NiOx synthesis` 20 แถว — อย่างหลังคือสูตร**สังเคราะห์ผง NiOx nanoparticle** ก่อนเอามาละลายทำน้ำหมึก (Dissolve Ni(NO₃)₂ → ปรับ pH ด้วย NaOH → ล้าง → อบแห้ง → เผา 270°C) ไม่ใช่ขั้นตอนสร้างเซลล์ อยู่ที่ `layer_id=2` ทั้งหมด มีแค่ DOI เดียว (`10.1002admi.202500231`, 4 device) → **confound 100% กับ paper นั้น ห้ามใช้เป็น feature** เก็บไว้เพื่อ provenance/ทำแล็บซ้ำเท่านั้น
  - ถ้าเห็นแถวเยอะผิดปกติที่ `layer_id=2` ของ paper นี้ — เป็น NiOx synthesis ไม่ใช่ขั้นตอนเคลือบผิวที่บันทึกผิดที่

**นโยบาย NiOx synthesis — บังคับใช้ 3 ชั้น (ไม่ใช่แค่เขียนเตือน)**

ข้อตกลงเดิม: เก็บไว้ใน `process.csv` เพื่อ provenance/ทำแล็บซ้ำ แต่**ห้ามเป็น ML feature** เพราะมาจาก DOI เดียว (4 device) — ใต้ LOPO มันคือลายนิ้วมือของ paper นั้น โมเดลจะจำ paper ไม่ใช่เรียนกลไก บังคับใช้จริงตามนี้:

| ชั้น | ที่ไหน | ทำอะไร |
|---|---|---|
| **1. กรองตอนดึง knob** | recheck §9 | `_cell = pr_out[pr_out["source_sheet"] == "Process"]` ก่อนคำนวณ knob ทุกตัว |
| **2. gate ตอน export** | recheck §10 | assert ว่า `htl_anneal_C` ใน target_table ตรงกับค่าที่คำนวณจาก `source_sheet=="Process"` เท่านั้น — ถ้าใครเผลอเอา filter ออก จะ fail ทันที ไม่ผ่านไปเงียบ ๆ |
| **3. ป้ายเตือนในผลลัพธ์** | recheck §4.1 | คอลัมน์ knob ที่มาจาก NiOx synthesis ล้วนติดป้าย `!!` ตอนพิมพ์รายการ — `Dissolve__M`, `pH adjustment__M`, `Wash__times`, `Dry__hr`, `Dry__time_s` (4/4 แถว) และ `Annealing__hr` (4/5) |

ตรวจแล้ว: filter ไม่ทำให้ค่าใน `target_table` เปลี่ยนสักช่อง (บังเอิญ anneal ทั้งสองชีต = 270°C เท่ากันพอดีทั้ง 4 device) — เป็นความบังเอิญ ไม่ใช่การออกแบบ จึงต้องมี gate ค้ำไว้

**ถ้าจะสร้าง feature ใหม่จาก `process.csv` เอง** ให้เริ่มด้วยบรรทัดนี้เสมอ:
```python
proc = pd.read_csv("processed/prepared_v2/process.csv")
proc = proc[(proc.source_sheet == "Process") & proc.process_type.notna()]   # ตัดชีตสังเคราะห์ + placeholder
```

### `properties.csv` (563×16) — 1 แถว = 1 ค่าที่ paper รายงาน
- **key**: `device_id` + `base_property` + `stat/scan/layer/carrier` (แตกมาจากชื่อ property เต็ม)
- ใช้ `value_parsed` เสมอ (ไม่ใช่ `value` ดิบ) / `unit_corrected/value_corrected/unit_fix_note` = ร่องรอยแก้ unit
- **สถานะ 27 unit errors จาก `audit/unit_value_errors.csv` — ปิดครบแล้ว**: Voc/Jsc ติดป้าย `%` 12 แถว (แก้ป้าย unit ตัวเลขไม่เปลี่ยน) · FF เศษส่วน 8 แถว (×100 ตั้งแต่ column_prepare ช่วง 3) · hole mobility m² 3 แถว (×1e4 — `value_corrected` ถูกมาตลอด แต่ `value_parsed` เพิ่งซิงก์ใน recheck §8.7) · 4 แถวที่เหลือเป็น false positive ของ regex เก่าที่แมตช์ `cm2 V-1 s-1` (หน่วยถูกอยู่แล้ว)
- ⚠️ ถ้าเพิ่มการแก้ที่**เปลี่ยนตัวเลข** ในอนาคต ต้องซิงก์เข้า `value_parsed` ด้วย — recheck §8.7 มี gate assert ไว้ให้ fail ทันทีถ้าลืม
- ⚠️ device เดียวมีหลายแถวต่อ property (Champion/Average/scan) — pivot เองต้องเลือก variant ให้ตรงกัน (target_table ทำให้แล้ว)

### `psk_composition.csv` (44×11) — 1 แถว = 1 device
> ศัพท์ A-site/X-site/precursor/cross-check อธิบายละเอียดในหัวข้อ "พื้นฐาน domain" ต้นเอกสาร
- `a_Cs a_FA a_MA` (A-site) + `x_I x_Br` (X-site) รวมฝั่งละ 1 / `b_site` = Pb ทั้งหมด
- `comp_source`: formula 42, precursor 2 / `cross_check`: เทียบ 2 แหล่งอิสระ / `precursor_status`: เหตุที่คำนวณจาก precursor ได้/ไม่ได้
- ⚠️ NaN ใน a_/x_ = ไอออนไม่อยู่ในสูตร (=0) — target_table fill 0 ให้แล้ว ไฟล์นี้คงค่าดิบ

**วิธีใช้**
- **Modeling**: ไม่ต้องแตะไฟล์นี้ — ใช้ `a_Cs a_FA x_Br` ที่ fill 0 แล้วใน `target_table.csv`
- **วิเคราะห์เคมีเต็ม** (เช่น halide ratio, MA content): ใช้ไฟล์นี้ — `x_I a_MA` ไม่ได้ถูกดึงเข้า target_table
- **อ่านความน่าเชื่อถือรายค่า**: `cross_check = "ตรง"` ดีสุด (2 แหล่งอิสระยืนยัน) / `NaN` = precursor คำนวณไม่ได้ (ดูเหตุใน `precursor_status` เช่น "ตกหล่น: MABr") ค่ายังใช้ได้จาก formula แค่ไม่มีแหล่งที่สอง / `"ต่าง: ..."` = 2 แหล่งขัดกัน ใช้ formula เป็นหลักแต่ควรตรวจ paper ก่อนอ้างใน thesis
- ⚠️ **อย่าสับสนกับ §E ใน column_prepare**: cell "Pivot Cleaning material" (`layers_material_parsed`/`mat_df`) เป็น prototype ที่ตายแล้ว — ไม่เคย merge/export ตัวจริงที่ผลิตไฟล์นี้คือตรรกะ §J (port อยู่ใน recheck §8.5)

### `layers.csv` (352×11) — 1 แถว = 1 วัสดุใน layer
- **key**: `device_id + layer_id` — **ไม่ unique** (layer 7 มีได้ 2 วัสดุ PCBM+BCP, `layer_type` = "ETL treatment (1)/(2)")
- `thickness_nm_parsed/min/max/note` (แปลง Å→nm แล้ว), `material_family`, `layer_kind` (main/coating/NaN ถ้าไม่มี material)
- ⚠️ `material_family` NaN 102/352 แถว — **ตั้งใจ ไม่ใช่ตกหล่น** คนละความหมายตามชั้น: L4 (PSK) = สูตรเคมี มี `psk_composition` แทน / L1/L2/L8 = TCO/NiOx/Metal ล้วน variance 0 / L6 มี variance ดึงเป็น `etl_family` ใน target_table แล้ว (recheck §8.6)

### `stability.csv` (62×20) — 1 แถว = 1 การทดสอบ (device มีได้หลายเงื่อนไข, 35 dev)
- ใช้ `*_parsed` + `stress_group` (6 กลุ่มจาก environment 22 แบบ) / `retention_pct_censor` = ค่า `<55` แบบ censored
- ⚠️ เทียบ retention ข้ามเงื่อนไขตรง ๆ ไม่ได้ (25-85°C, 1-5000 ชม.) — ใช้ retention@25C bin หรือ degradation_rate ใน target_table แทน

### `materials.csv` (543×13) — 1 แถว = 1 สารตั้งต้นต่อ layer
- `amount + unit` (โมลหรือมวล — ใช้คำนวณ composition), `solvent_1/2 solvent_ratio solvent_system solvent_note` (normalize 27→16 ระบบ)
- `material_family` NaN 262/543 ปกติ — สารในนี้เป็น precursor/solvent/additive (CsI, FAI, DMF, ...) ไม่มี "ตระกูล" ให้จัด บทบาทดูจากคอลัมน์ `type` แทน

### `vendors.csv` (644×8) — 1 แถว = 1 สารต่อ vendor
- `purity_pct purity_qualifier purity_kind` parse แล้ว — ฝั่ง provenance ไม่ใช่ feature

### `devices.csv` (44×13) / `device_layer_flags.csv` (44×10)
- devices = ทะเบียน device (`doi_raw sample_no source_file`) + ธง layer / flags = ธงชุดเดียวกันแยกไฟล์ (ซ้ำกับใน target_table — ใช้ตัวไหนก็ได้)

## ค้าง / ระวัง

1. **`100108000`** (admi S3, L4 Annealing, หน่วย `C, sec`) — **แถวเดียวที่ยังติดธง `param_note`**
   - อ่านตรงตัว = 100°C, 108000 sec (30 ชม.) — ไม่สมเหตุผลสำหรับ perovskite
   - sibling ในไฟล์เดียวกันขัดกันเอง: S1 = `100, 1800` (30 นาที), S4 = `100, 10800` (3 ชม.)
   - นับหลัก: `100108000` = 9 หลัก ถ้าเป็น `100,1800` จะได้ 7 หลัก ถ้าเป็น `100,10800` จะได้ 8 หลัก + เผลอพิมพ์ 0 เกิน 1 ตัว = 9 หลัก → **น่าจะเป็น `100, 10800` ตาม S4** แต่ยังเป็นการอนุมาน
   - พยายามเปิด paper แล้ว ([Tirado et al., Adv. Mater. Interfaces 2025](https://advanced.onlinelibrary.wiley.com/doi/10.1002/admi.202500231)) ติด paywall (HTTP 402) เข้าถึง experimental section ไม่ได้ → **คงติดธงไว้ ไม่เดา** ถ้ามี institutional access ให้เช็คค่า annealing ของ perovskite แล้วเติมใน `PARAM_FIX` (recheck §4.0)
2. `antisolvent_timing_s` มี 2 จุดอ้างอิง — ใช้ per-group (`timing_ref`) หรือทำ binary feature
3. Thermal evaporation rate เป็นช่วง (`0.5-1 Å/s`) parse ไม่ได้ 12 แถว — ไม่ใช่ knob หลัก ปล่อยไว้
4. stability n=14 — exploratory เท่านั้น
5. **โมเดลถัดไป**: RandomForest + SHAP, LeaveOneGroupOut(paper) 15 fold, เทรน voc/jsc/ff แยกแล้วคูณเป็น pce, รายงาน mean±SD + bootstrap — ผลทั้งหมด = hypothesis สำหรับ wet-lab validation

**ปิดไปแล้ว** (เคยอยู่ในรายการนี้): 27 unit errors ปิดครบ (ดู `properties.csv`) · psk_composition export แล้ว · layers แถวพอง · Champion/Average ปน · `coat_*` นับ additive · NiOx synthesis รั่วเข้า feature

**ตัดสินใจว่าไม่ทำ** (อย่าเปิดใหม่โดยไม่มีเหตุผลใหม่): ดึงพารามิเตอร์การเคลือบ L3/L5/L7 เป็น feature — ไม่แปรผันภายใน paper จะกลายเป็น paper fingerprint (เหตุผลเต็มในหัวข้อชั้นเคลือบผิว)
