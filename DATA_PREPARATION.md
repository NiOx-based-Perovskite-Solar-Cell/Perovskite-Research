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
| 4.0 | **PARAM_FIX 11 แถว** — parameter ตัวเลขติดกัน/comma หาย + ค่าผิด ทุกค่าของ `10.1002admi.202500231` ยืนยันจาก **Supporting Information** ของ paper แล้ว (ดูหัวข้อ Ground-truth validation) + คอลัมน์ `param_fixed` กำกับ / **ไม่เหลือแถวติดธง** |
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
| `target_table.csv` | 44×35 | **ตารางหลักสำหรับ modeling**: Y (jv_variant กำกับ) + mediator + stability + X knob + composition + etl_family |
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

### `target_table.csv` (44×35) — 1 แถว = 1 device ⭐ ตารางหลัก modeling
- **key**: `device_id` / paper แยกได้จาก `device_id.rsplit("_Sample", 1)[0]` (ใช้เป็น group ใน LOPO)
- **ธง layer** (10): `is_coated`, `n_coating_layers`, `coat_htl/psk/etl`, `anneal_htl/psk` + `htl_mod_kind`/`psk_mod_kind` (coating/additive — ดูหัวข้อชั้นเคลือบผิว) — ⚠️ `is_coated`/`coat_etl` True ทั้ง 44 (ทุก device มี L7) variance 0 ใช้เป็น feature ไม่ได้ ตัวที่มี variance คือ `coat_htl` (26) กับ `coat_psk` (22)
- **Y** (5): `pce voc jsc ff` (43 dev) + `jv_variant` บอกที่มา (champion 23 / plain 16 / average 4) — 4 ค่ามาจากชุดเดียวกันเสมอ
- **mediator** (4): `vtfl` (28) ตัวหลัก, `trap_density` (22), `rs` (22), `rrec` (15)
- **stability** (2): `retention_25c` (14), `degradation_rate` (14)
- **X knob** (10): `psk_spin_rpm` (43), `psk_anneal_C` (40), `psk_anneal_time_s` (41), `antisolvent_vol_uL` (35), `htl_anneal_C` (41), `psk_spin_last_s` (42) + จังหวะหยด antisolvent 4 คอลัมน์ (ดูด้านล่าง)

**จังหวะหยด antisolvent — ใช้ `antisolvent_drop_frac` เป็นตัวหลัก**

ค่าดิบมี 2 จุดอ้างอิงที่รวมสเกลตรง ๆ ไม่ได้ (`antisolvent_timing_s` + `antisolvent_timing_ref`): บวก = วินาทีหลังเริ่ม spin ขั้นสุดท้าย, ลบ = วินาทีก่อนจบ recheck §9 แปลงรวมด้วยความยาว spin ขั้นสุดท้าย (`psk_spin_last_s`):

| คอลัมน์ | ความหมาย | n |
|---|---|---|
| `antisolvent_timing_s` | ค่าดิบตามที่ paper เขียน (ยังมี 2 จุดอ้างอิง) | 32 |
| `antisolvent_timing_ref` | `after_start` 26 / `before_end` 6 | 32 |
| `psk_spin_last_s` | ความยาว spin ขั้นสุดท้ายของชั้น PSK | 42 |
| **`antisolvent_drop_s`** | **วินาทีนับจากเริ่ม spin ขั้นสุดท้าย — แกนเดียวกันหมด** | 32 |
| **`antisolvent_drop_frac`** | `drop_s / psk_spin_last_s` (0-1) เทียบข้ามสูตรที่ spin ไม่เท่ากันได้ | 30 |

แปลง 6 device ที่เป็น `before_end`: admi spin 35 s, timing −10 → หยดวินาทีที่ **25** · acsami.5c16972 spin 40 s, timing −5 → **35** มี gate assert ว่าจุดหยดต้องอยู่ใน `0 < t ≤ spin duration` ทุกตัว
`drop_frac` ว่าง 2 device (`solr_202400534`) เพราะไม่มี spin duration — ค่าบวกอยู่แล้วจึงใช้ `drop_s` ได้ปกติ

**ผลของการรวมสเกล** (naive spearman, ยังไม่คุม paper — ทิศทางเท่านั้น): `drop_frac ~ pce` **+0.52** (p=0.003) ดีกว่าค่าดิบ `timing_s ~ pce` +0.44 (p=0.012) สอดคล้องกับกลไก: จังหวะหยดที่สำคัญคือ*สัดส่วน*ของช่วง spin (หน้าต่างตกผลึก) ไม่ใช่วินาทีสัมบูรณ์
⚠️ อย่าใส่ทั้ง `timing_s` และ `drop_s`/`drop_frac` พร้อมกัน — เป็นข้อมูลเดียวกัน จะทำให้ SHAP กระจายเครดิตมั่ว
- **X ควบคุม** (4): `a_Cs a_FA x_Br` (44 — ไอออนไม่อยู่ในสูตร = 0 จริง) + `etl_family` (44, categorical: PCBM/C60/BCP — ชนิด ETL กระทบรอยต่อ/การเก็บอิเล็กตรอน ต้องคุมเหมือน composition; ⚠️ BCP มีแค่ 2 dev จาก paper เดียว ถ้า SHAP ชี้ BCP อย่าเชื่อ)
- ⚠️ `pce` ขาด 1 dev ที่ variant ไม่รายงาน ใช้ voc×jsc×ff/100 แทนได้

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

## Ground-truth validation — เทียบข้อมูลที่สกัดกับตัวเปเปอร์จริง (2026-08-04)

ทำกับ `10.1002admi.202500231` ([Tirado et al., *Adv. Mater. Interfaces* **12**, e00231, 2025](https://doi.org/10.1002/admi.202500231), open access CC-BY) เป็น paper ตัวอย่าง — เปิดไฟล์เต็มแล้วไล่เทียบทุกค่าที่สกัดได้

**ผลรวม: ตรงเป๊ะ 30/30 ค่า ไม่มีคลาดเคลื่อนสักตัว**

| สิ่งที่เทียบ | แหล่งในเปเปอร์ | ผล |
|---|---|---|
| Voc, Jsc, FF, PCE, Rs (4 device) | Table 1 | **20/20 ตรง** |
| TRPL τ_avg, ∂A/∂V FWHM (4 device) | Table 2 | **8/8 ตรง** |
| องค์ประกอบ PSK | ข้อความ §2 `(Cs.05FA.79MA.16)Pb(I.87Br.13)3` | ตรง — `a_Cs` 0.05 / `a_FA` 0.79 / `a_MA` 0.16 / `x_I` 0.87 / `x_Br` 0.13 |
| โครงสร้างชั้น | §2.4 `ITO/HTL/CsFAMA/C60/BCP/Ag` | ตรง — L1-L8 ครบ, L5 ว่างจริง (ไม่มี PSK treatment) |
| ความหนา PSK | §2.5 "≈400–450 nm of CsFAMA" | ตรง — `400-450` parse เป็น 425 (ค่ากลาง) |
| Stability | §2.4 "72 h ... ISOS-L2 ... unencapsulated" | ตรง — `time_hr` 72, `environment` ISOS-L2, `encapsulation` N |

นัยสำคัญ: **pipeline สกัด/คลีนไม่ได้ทำค่าเพี้ยน** ทั้งการแปลง FF ×100 (0.76 → 76), การ parse ช่วงความหนา, การแตกสูตร ABX₃, และการ map layer — ผ่านการตรวจกับต้นทางจริงหมด

**สิ่งที่พบเพิ่มระหว่างตรวจ:**

1. **ค่า JV ของ paper นี้เป็น "average" ไม่ใช่ champion** — Table 1 รายงาน mean ± SD แต่ช่อง `property` ในไฟล์เขียนแค่ `Voc`/`Jsc`/... ไม่มีคำนำหน้า ตัวจัด variant เลยให้ `jv_variant = "plain"` ⚠️ ตอนเทียบข้าม paper ให้จำว่า `plain` ของ paper นี้ = average ไม่ใช่ champion
2. **`VBM = −5.42` ซ้ำกันทั้ง Sample2/3/4** — ข้อความในเปเปอร์รายงาน UPS แค่ 2 ค่า (−5.7 สำหรับ ITO/NiOx, −5.4 สำหรับ ITO/NiOx-IL2%) ค่าของ IL1%/IL4% น่าจะถูกคัดลอกมาจาก IL2% ค่าจริงรายตัวอยู่ใน Table S5 (Supporting Information) — ควรเช็คก่อนใช้ VBM เป็น feature
   (คนละชุดกับ VBM จาก spectroelectrochemistry ในเปเปอร์ซึ่งได้ 5.6 / 5.56 / 5.5 / 5.6 eV แยกรายตัว)
3. **`PSK optical band gap` ของ Sample1 = 1.609 eV** — เปเปอร์เขียนขัดกันเอง: ประโยคหนึ่งบอก shift ไป 1.57 eV เมื่อ EMIMI เพิ่ม อีกประโยคบอกตัวที่ treated กลับมาที่ 1.62 eV ตามปกติ ข้อมูลเราตีความแบบหลัง (control เพี้ยน / treated = 1.62) ซึ่งสมเหตุผลทางฟิสิกส์กว่า แต่ตัวเลข 1.609 ไม่ตรงกับ 1.57 ที่เปเปอร์เขียน
4. **Experimental Section ไม่อยู่ในตัวเปเปอร์** — อยู่ใน Supporting Information (ตัวบทอ้าง "As detailed in the Experimental Section" แต่ไฟล์หลัก 8 หน้าไม่มีหัวข้อนี้) จึงยังไม่ได้ค่า annealing ที่ค้างอยู่

### เปิด Supporting Information แล้ว — ปิดทุกเคสค้างของ paper นี้

โหลด `admi70014-sup-0001-SuppMat.pdf` (SI, 27 หน้า) ได้ **Experimental Section เต็ม** ที่ไม่มีในไฟล์หลัก

**ยืนยันตรงเพิ่มอีก 12 ค่า** — SI §1.1-1.2 vs ข้อมูลเรา:

| SI เขียนว่า | ข้อมูลเรา | ผล |
|---|---|---|
| precursor: 324 mg PbI₂, 47 PbBr₂, 110 FAI, 11 CsI, 14 MABr | ตรงทุกตัว | ✅ |
| ตัวทำละลาย 0.512 ml DMF + 0.128 ml DMSO | `solvent_ratio` = 4:1 | ✅ |
| NiOx 22 mg/ml · EMIMI 25 mg/ml | ตรง | ✅ |
| ล้าง acetone 10 min + IPA 10 min | `Wash 1200 sec` (ผลรวม) | ✅ |
| ผสม precursor 60 °C, 1 h | `60, 3600` | ✅ |
| spin 1000 rpm/500 rpm·s⁻¹/10 s → 4000/2000/35 s | ตรงเป๊ะ | ✅ |
| **"At 10 s before the end ... 0.25 ml chlorobenzene"** | `0.25, -10` | ✅ **ยืนยัน convention ค่าลบ = ก่อนจบ spin** |
| C60 50 nm, BCP 10 nm, Ag 100 nm | ตรง | ✅ |
| อัตราระเหย 0.5-1 Å/s @ 1×10⁻⁶ Torr | ตรง | ✅ |
| NiOx synthesis: 6 M, NaOH 7 M pH 10, ล้าง 4 ครั้ง, อบ 80 °C 12 h, เผา 270 °C 3 h | ตรงทุกขั้น | ✅ |
| NiOx Eg 3.85 eV (Fig S10a) | ตรง | ✅ |

**และพบ error 3 จุดที่แก้แล้ว:**

| # | SI ระบุ | ค่าเดิม | แก้เป็น |
|---|---|---|---|
| 1 | *"put on a hot plate at **100 °C for 30 min**"* | S1 `100,1800` ✓ / S2 `100,108000` / S3 `100108000` / S4 `100,10800` | **`100, 1800` ทั้ง 4** — เคส `100108000` ปิดแล้ว |
| 2 | *"O₂-plasma at 155 W with 600 mtorr ... for **10 min**"* | S4 `155,600,3600` | **`155, 600, 600`** — PARAM_FIX เดิมของเราเดา 3600 ผิด |
| 3 | Fig S10b Tauc: control **1.609** / 1% **1.605** / 2% **1.615** / 4% **1.616** eV | S2/S3/S4 = 1.620 ทั้งหมด | ค่ารายตัวตาม SI (ค่าเดิมคือ PL peak จากตัวบท ไม่ใช่ Tauc Eg) |

ผลข้างเคียงที่สำคัญ: **outlier `psk_anneal_time_s` = 108000 s (30 ชม.) หายไป** ตอนนี้ median 1800 max 3600 ทั้ง dataset สมเหตุผลหมด และ `psk_anneal_time_s ~ voc` เปลี่ยนจาก +0.36 เป็น **+0.44** (p=0.004) เพราะ outlier เคยบิดอันดับ

**⚠️ เรื่องที่ยังต้องตัดสินใจ — `htl_anneal_C` ของ 4 device นี้**

SI §1.2 (Device fabrication) **ไม่มีขั้นอบฟิล์ม NiOx เลย** — มีแค่ spin coat 3000 rpm 30 s แล้วไปต่อชั้น perovskite ส่วน `270 °C 3 h` อยู่ใน §1.1 **Materials synthesis** = การ**เผาผง** NiOx ก่อนเอามาละลาย
แถว `Annealing 270, 10800` ที่ L2 ใน Process sheet จึง**ซ้ำกับ NiOx synthesis sheet** ไม่ใช่ขั้นตอนสร้างเซลล์ → `htl_anneal_C = 270` ของ 4 device นี้อาจไม่ควรมี
กระทบ feature ที่ correlation แรงสุดตอนนี้ (`htl_anneal_C ~ ff` = −0.74) — ยังไม่แก้ รอตัดสินใจ

**Table S5 ยืนยันข้อสงสัยเรื่อง VBM**: มีแถว EMIMI แถวเดียว (2%) → `VBM = −5.42` ของ Sample2 (1%) และ Sample4 (4%) **ถูกคัดลอกมาจากค่า 2%** ไม่ได้วัดแยก ห้ามใช้เป็น feature รายตัว

## ⛔ ข้อค้นพบสำคัญที่สุด — X knob เชิงตัวเลข **คงที่ภายใน paper ทุกตัว**

ตรวจ 2026-08-04 หลังเปิด SI: นับว่ามีกี่ paper ที่ค่าของ feature นั้น**ต่างกันระหว่าง device ของตัวเอง**

| feature | paper ที่แปรค่าภายในตัวเอง (จาก 15) |
|---|---|
| `htl_mod_kind` | **11** ✅ |
| `coat_htl` | **10** ✅ |
| `n_coating_layers` | **10** ✅ (ตัวเดียวกับ coat_htl) |
| `psk_spin_rpm` | 1 |
| `a_Cs` / `a_FA` / `x_Br` | 1 |
| `psk_anneal_C` · `psk_anneal_time_s` · `psk_spin_last_s` · `htl_anneal_C` | **0** ⛔ |
| `antisolvent_vol_uL` · `antisolvent_drop_s` · `antisolvent_drop_frac` | **0** ⛔ |
| `coat_psk` · `coat_etl` · `psk_mod_kind` · `etl_family` · `anneal_htl` · `anneal_psk` | **0** ⛔ |
| *(เทียบ)* `pce` · `voc` · `vtfl` | **15 / 15 / 11** — Y แปรครบทุก paper ตามที่ควรเป็น |

**แปลว่า**: ในแต่ละ paper นักวิจัยเปลี่ยน**สารที่ใช้ดัดแปลง HTL** (EMIMI 1/2/4%, SAM มี/ไม่มี ...) แต่ **ไม่เปลี่ยน process knob เชิงตัวเลขเลย** — spin RPM, อุณหภูมิ/เวลา anneal, ปริมาณและจังหวะ antisolvent ตั้งค่าเดียวแล้วใช้กับทุก device ในเปเปอร์นั้น

**ผลต่อ modeling**: knob ที่แปรค่า 0 paper คือ **confound 100% กับ paper identity** ใต้ LeaveOneGroupOut โมเดลแยกไม่ออกว่าเป็นผลของ knob หรือของ "ห้องแล็บไหนทำ" → correlation แรง ๆ ที่เห็นก่อนหน้า (`htl_anneal_C ~ ff` −0.74, `antisolvent_drop_frac ~ pce` +0.52) เป็น **between-paper ล้วน** = paper fingerprint ไม่ใช่ความสัมพันธ์เชิงกลไก

**สิ่งที่เหลือใช้ได้จริงใต้ LOPO**: `coat_htl` / `htl_mod_kind` (การดัดแปลงผิว HTL) — 10-11 paper มี contrast ภายในตัวเอง (control vs treated) เป็น within-paper comparison ที่แท้จริง

**สิ่งที่ต้องตัดสินใจ** (ยังไม่ทำ):
1. เปลี่ยนคำถามวิจัยเป็น "การดัดแปลงผิว HTL ส่งผลต่อ Y อย่างไร" ซึ่งตอบได้จริงด้วยข้อมูลชุดนี้ — หรือ
2. คงคำถามเดิม (process knob ขับ grain size) แล้วยอมรับว่าต้อง**เก็บเปเปอร์เพิ่มที่แปร knob ภายในตัวเอง** (เช่น paper ที่ศึกษา antisolvent timing โดยตรง) — หรือ
3. รายงานผลเป็น between-paper association พร้อมประกาศข้อจำกัดชัดเจนว่าไม่ใช่ causal

หมายเหตุ: ข้อนี้เป็นเหตุผลเดียวกับที่ตัดสินใจไม่ดึงพารามิเตอร์การเคลือบ L3/L5/L7 (หัวข้อชั้นเคลือบผิว) — ตอนนี้ยืนยันแล้วว่าปัญหานี้ครอบ **ทุก** knob เชิงตัวเลข ไม่ใช่แค่ของชั้นเคลือบ

## ค้าง / ระวัง

1. ~~**`100108000`**~~ — **ปิดแล้ว 2026-08-04** ด้วย Supporting Information (`100 °C for 30 min` = 1800 s) พร้อมแก้ Sample2/4 ที่ผิดด้วย ไม่เหลือแถวติดธง `param_note` ในทั้ง dataset
2. ~~`antisolvent_timing_s` มี 2 จุดอ้างอิง~~ — **แก้แล้ว** ดู `antisolvent_drop_s` / `antisolvent_drop_frac` ในหัวข้อ target_table
6. ~~`a_Cs = 0.333` ของ `10_1002_solr_202200422`~~ — **ปิดแล้ว 2026-08-04** เปิด SI ของเปเปอร์ ([Yang et al., Solar RRL 2022](https://onlinelibrary.wiley.com/doi/10.1002/solr.202200422)) พบว่า**เปเปอร์เขียน `0.7 M CsI` จริง** (Raw Excel เรากรอกตรงต้นฉบับ ไม่ใช่ความผิดการกรอก) แต่หลักฐานฟิสิกส์ 4 ชั้นชี้ว่า**เปเปอร์เองพิมพ์ผิดจาก 0.07**: (1) 0.07 ให้สูตรมาตรฐาน Cs05FA81MA14 + A/B=0.97 stoichiometric / 0.7 ให้ A เกิน B 39% (2) precursor ตัวอื่นตรง recipe Saliba คลาสสิก ×1.18 ซึ่ง CsI effective ~0.07-0.09 (3) PL peak ~770 nm ใน SI Fig S12 = bandgap มาตรฐาน ขัดกับ Cs 33% ที่ต้อง blue-shift (4) เปเปอร์เรียกเองว่า "CsFAMA triple cation" — user ยืนยันแก้เป็น 0.07 (recheck §8.9) → a_Cs=0.048, a_FA=0.81, a_MA=0.143 ตอนนี้ a_Cs สูงสุดทั้ง dataset = 0.15 (สมเหตุผล)
   - **บทเรียนสำคัญ**: raw Excel กรอกถูกทุกตัวในเคสนี้ — บางครั้งต้นฉบับ (เปเปอร์) คือตัวที่ผิด การ validate ต้องเช็คถึงระดับความสมเหตุผลทางฟิสิกส์ ไม่ใช่แค่เทียบกับต้นทาง
7. หมายเหตุเล็ก (ไม่ใช่บั๊ก แต่ควรรู้ตอนใช้):
   - `psk_anneal_time_s` ใช้ **ผลรวม** ทุกขั้น Annealing ที่ L4 — device ที่อบสองขั้น (เช่น `smll_202504237`: 30°C 40 นาที + 100°C 20 นาที) จะได้ 3600 ซึ่งรวมเวลาขั้นอุ่นต่ำเข้าไปด้วย ไม่ใช่เวลาที่อุณหภูมิหลักอย่างเดียว (`psk_anneal_C` ใช้ max = 100 ถูกต้อง)
   - `properties` มี 4 คู่ (device, property) ซ้ำของ `acsami_5c15868_Sample3` — เป็น Champion ค่าเดียวกันรายงานสองที่ (ตาราง vs forward-scan inset) ค่าต่างกัน <0.3% pivot ใช้ max เลือกค่าตารางให้อัตโนมัติ ไม่กระทบผล
   - `pce` สูงสุด 26.5 และ `retention` 100.85% ตรวจแล้ว**ถูกต้อง** — มาจากเปเปอร์ระดับ record ปี 2024-25 (Adv. Mater., Science) และ retention เกิน 100 จาก light-soaking (มี note กำกับในไฟล์แล้ว)
3. Thermal evaporation rate เป็นช่วง (`0.5-1 Å/s`) parse ไม่ได้ 12 แถว — ไม่ใช่ knob หลัก ปล่อยไว้
4. stability n=14 — exploratory เท่านั้น
5. **โมเดลถัดไป**: RandomForest + SHAP, LeaveOneGroupOut(paper) 15 fold, เทรน voc/jsc/ff แยกแล้วคูณเป็น pce, รายงาน mean±SD + bootstrap — ผลทั้งหมด = hypothesis สำหรับ wet-lab validation

**ปิดไปแล้ว** (เคยอยู่ในรายการนี้): `100108000` + outlier annealing (ปิดด้วย SI) · antisolvent 2 จุดอ้างอิง · 27 unit errors ปิดครบ (ดู `properties.csv`) · psk_composition export แล้ว · layers แถวพอง · Champion/Average ปน · `coat_*` นับ additive · NiOx synthesis รั่วเข้า feature

## การตรวจสอบย้อนกลับ (19 ส.ค. 2569) — 13 รายการที่แก้แล้ว

ตรวจซ้ำทั้งชุดโดยเทียบผลลัพธ์กับไฟล์ Excel ต้นทาง 44 ไฟล์ ตรวจความสมเหตุสมผลทางฟิสิกส์ และอ่านโค้ดในโน้ตบุ๊กทีละเซลล์
พบ 20 รายการ แก้แล้ว 13 · รายงานเต็ม: `reports/audit_report.pdf`

**แก้ที่ต้นทางแล้ว (รัน pipeline ใหม่ทั้งเส้น)**

| ข้อ | ปัญหา | แก้ที่ |
|---|---|---|
| A, B | ค่า 12 แถวถูกกรอกในช่อง `Descriptions` แทน `Parameter` ตัวสกัดจึงอ่านไม่เจอ — ทำให้ `psk_spin_rpm` ของ `solr_202400534_Sample2` ผิดเป็น 1000 (ที่ถูกคือ 5000) และค่าของ `acsami_5c15868_Sample1` หายทั้งชีต | §4.0b `DESC_RECOVER` + ด่านตรวจที่ fail ถ้ามีตัวเลขตกค้างในช่อง description |
| C, D | VBM ของ `ccschem_Sample1` เครื่องหมายลบหาย (+5.02 → −5.02) · ชื่อ `VBM` ถูกใช้กับ 2 ปริมาณ แยกเป็น `VBM offset from EF` 3 แถว | §8.8b + ด่านตรวจว่าระดับพลังงานอ้างอิงสุญญากาศต้องเป็นลบ |
| E | `Rs` ของ `smll` 2 แถวเป็นความต้านทานแผ่น (Ω/sq) ไม่ใช่ series resistance | ย้ายไป `sheet_resistance_htl` — `rs` เหลือ 20 device |
| H | `trap_density` เดิมเฉลี่ยฝั่งโฮลกับอิเล็กตรอนรวมกัน | ใช้ฝั่งโฮลอย่างเดียว + เพิ่ม `trap_density_electron` |
| J | `Bandgap` 3.80 eV ของ `solener` เป็นของ NiOx แต่ไม่ระบุชั้น | เติม `layer = HTL` ให้ค่าที่เกิน 2.5 eV |
| N | `psk_spin_rpm` ใช้ `max` แต่ `psk_spin_last_s` ใช้ขั้นสุดท้ายจริง — คนละกติกา | ใช้ `step_order` ทั้งคู่ |
| **O** | **ค่า JV ยังปนกันระหว่างทิศทางสแกน RS/FS ใน 5 device** — บั๊กชนิดเดียวกับ Champion/Average ที่เคยแก้ แต่ลึกลงอีกชั้น | คีย์ชุดค่าเป็น `(variant, scan)` + เพิ่มคอลัมน์ `jv_scan` + ลดเกณฑ์ด่านตรวจจาก 1.0 เป็น 0.2 |
| P, Q | ด่านตรวจ §8.7 ยกเว้นกรณี FF พอดีแล้วประกาศว่าตรวจครบ · §8.8 แก้ค่าหลังด่านตรวจผ่านไปแล้ว | ย้ายมารวมที่ §9.5 ก่อน export บังคับว่าทุกความต่างต้องมี `value_note` |
| R, S | `split_param` ทิ้งข้อมูลเงียบเมื่อไม่มีหน่วย และทิ้งทั้งแถวเมื่อจำนวนไม่ตรง | ทุกเส้นทางติดธงใน `param_note` + หน่วยซ้ำไม่เขียนทับ |
| U | ตัวตรวจหน่วยเดิมหาแค่ชื่อ `voc`/`jsc`/`mobility` | เปลี่ยนเป็นตรวจตามช่วงค่าทางฟิสิกส์ 7 ปริมาณ |

**ผลต่อข้อสรุป**: ไม่เปลี่ยน — ΔPCE +2.09% ดีขึ้น 11/11 (p=0.001) · VTFL ลด 8/8 · ที่เปลี่ยนคือ ΔFF +2.62→+2.65, ΔJsc +0.72→+0.66
และ **`psk_spin_rpm` เป็น 0/15 ไม่ใช่ 1/15** ทำให้ข้อสรุปเรื่องพารามิเตอร์เชิงตัวเลขไม่มีข้อยกเว้นเหลือเลย

**ยังค้าง 7 รายการ** ต้องเปิดเปเปอร์ต้นฉบับ 4 ฉบับก่อนตัดสินใจ (`solr_202200422`, `acsami_4c14300`, `acsami_5c15868`, `ccschem_025_202505977`) —
รวมถึงค่า `Defect density` ที่บ่งชี้ความหนาฟิล์ม 31 nm, `Rrec` ที่สูงกว่าเปเปอร์อื่น 1,000 เท่า,
การตีความจังหวะหยด antisolvent ค่าบวก, และตัวเลขของ `ccschem_Sample1` ที่เปเปอร์เองไม่สอดคล้องกัน 0.50

**ตัดสินใจว่าไม่ทำ** (อย่าเปิดใหม่โดยไม่มีเหตุผลใหม่): ดึงพารามิเตอร์การเคลือบ L3/L5/L7 เป็น feature — ไม่แปรผันภายใน paper จะกลายเป็น paper fingerprint (เหตุผลเต็มในหัวข้อชั้นเคลือบผิว)


---

# หลังการเตรียมข้อมูล — ข้อมูลตอบอะไรได้ ทางเลือก และแผนที่จะไปต่อ

> เขียน 2026-08-04 หลังการเตรียมข้อมูลเสร็จสมบูรณ์ (target_table 44×35 ผ่าน ground-truth validation กับเปเปอร์จริง 30/30 ค่า) — ส่วนนี้คือ "แล้วยังไงต่อ" สำหรับขั้น modeling และการคุยกับอาจารย์ที่ปรึกษา

## 1. สถานะข้อมูลโดยสรุป

- 44 devices / 15 papers ทุกตัวเป็น p-i-n NiOx-based perovskite solar cell
- ฝั่ง Y ครบ: `voc jsc ff` 43 dev (variant-consistent), mediator `vtfl` 28 dev, `retention_25c` 14 dev
- ฝั่ง X ครบ: process knob 10 คอลัมน์, composition 3, สถาปัตยกรรม (`etl_family`, ธง coat/mod) — ทุกค่าผ่านการ audit และ 1 เปเปอร์ผ่านการเทียบกับต้นฉบับแบบ 100%
- คุณภาพข้อมูลไม่ใช่ปัญหาอีกต่อไป — **ปัญหาเดียวที่เหลือคือโครงสร้างของข้อมูล** (ข้อ 2)

## 2. ข้อจำกัดเชิงโครงสร้าง — สิ่งที่ข้อมูลชุดนี้ตอบไม่ได้

หลักการพื้นฐาน: จะวัดผลของตัวแปร X ได้ ต้องมีตัวอย่างที่ **X ต่างกันแต่อย่างอื่นเหมือนกัน** ใน dataset นี้ "อย่างอื่นเหมือนกัน" มีทางเดียวคือ **อยู่ในเปเปอร์เดียวกัน** (แล็บเดียว คนเดียว เครื่องเดียว ล็อตสารเดียว) — การเทียบข้ามเปเปอร์จะปนกับความต่างของแล็บเสมอ (พิสูจน์ในข้อมูล: variance ของ PCE ระหว่างเปเปอร์ใหญ่กว่าภายในเปเปอร์ ~3 เท่า)

ผลนับจริง (จำนวนเปเปอร์ที่ค่าแปรผันภายในตัวเอง จาก 15):

| กลุ่มตัวแปร | แปรภายในเปเปอร์ | สถานะ |
|---|---|---|
| การดัดแปลงผิว HTL (`htl_mod_kind`, `coat_htl`) | **10-11** | ✅ ใช้เป็นตัวแปรหลักได้ |
| สูตรเคมี (`a_Cs a_FA x_Br`) | 1 | ใช้เป็นตัวควบคุม |
| ปุ่มตัวเลขทุกตัว (RPM, อุณหภูมิ, เวลา, antisolvent) | **0** | ⛔ confound 100% กับ paper |
| สถาปัตยกรรมอื่น (`etl_family`, `coat_psk`, ...) | 0 | ⛔ เช่นกัน |
| (เทียบ) Y: `pce voc vtfl` | 15 / 15 / 11 | แปรตามปกติ — ยืนยันว่าวิธีนับไม่เพี้ยน |

สาเหตุไม่ใช่ความบังเอิญ: นักวิจัยแต่ละกลุ่มตั้งสูตรการผลิตไว้คงที่ แล้วแปรเฉพาะสิ่งที่ตัวเองศึกษา — และสิ่งที่วงการ p-i-n ศึกษาช่วงนี้คือ buried interface / การดัดแปลงผิว HTL พอดี

## 3. ทางเลือก 3 ทาง และเหตุผล

**ทาง 1 — ปรับคำถามให้ตรงกับที่ข้อมูลตอบได้: "การดัดแปลงผิว HTL ช่วยจริงไหม ผ่านกลไกอะไร" (แนะนำ)**
- ทำได้ทันที ไม่ต้องเก็บข้อมูลเพิ่ม
- มี within-paper contrast 10-11 เปเปอร์ = การเทียบแบบ A/B ในแล็บเดียวกัน ซึ่งเป็นหลักฐานที่แข็งแรงที่สุดที่ observational data แบบนี้ให้ได้
- ยังคงโครงเดิมทุกอย่าง: input=process (ขั้นตอนดัดแปลงผิว), mediator=คุณภาพผลึก (VTFL), output=ประสิทธิภาพ+เสถียรภาพ
- ข้อแลก: ปุ่มตัวเลขเปลี่ยนบทบาทจาก "คำตอบ" เป็น "จุดตั้งต้นสูตรแล็บ"

**ทาง 2 — คงคำถามเดิม แล้วเก็บเปเปอร์เพิ่มชนิดที่แปรปุ่มภายในตัวเอง**
- เช่น เปเปอร์ที่ศึกษา antisolvent timing โดยตรง (ทดลอง 5/10/15 วิ ในแล็บเดียว), เปเปอร์ optimize อุณหภูมิ anneal
- ตอบคำถามเดิมได้จริง แต่ต้นทุนสูง: หา screen สกัด คลีน ต่อเปเปอร์ ~ชั่วโมงระดับสิบ และเปเปอร์แบบนี้กระจัดกระจายกว่า
- เหมาะเป็น**ส่วนขยาย**หลังทาง 1 มีผลแล้ว ไม่เหมาะเป็นทางหลักภายใต้เวลาฝึกงาน

**ทาง 3 — คงทุกอย่าง รายงานเป็น between-paper association**
- เร็วสุด แต่ข้อสรุปอ่อนสุด: ทุก correlation ของปุ่มตัวเลขแยกไม่ออกจากลายนิ้วมือแล็บ
- reviewer/กรรมการสอบจะโจมตีจุดนี้ได้ทันที และเราไม่มีคำตอบ
- ใช้ได้เฉพาะเป็น "ภาคผนวก exploratory" ประกอบทาง 1

**ทำไมทาง 1 ถึงไม่ใช่การล้มเลิกเป้าหมายเดิม** — เป้าหมายเดิมคือ "หา process ที่นำไปสู่ผลึก/ประสิทธิภาพ/เสถียรภาพที่ดีที่สุด แล้วเอากลับไปทำแล็บ (closed loop)" การดัดแปลงผิว HTL **คือ process ตัวหนึ่ง** และ closed loop ยังครบ — แค่แบ่งงานใหม่:

```
ML (วรรณกรรม 15 เปเปอร์)                     แล็บจริง (ของเรา)
────────────────────────────                ────────────────────────────
ตอบ: ขั้นตอนไหนสำคัญ + กลไกอะไร              ตอบ: ตั้งค่าตัวเลขเท่าไหร่
"HTL mod ช่วยจริง ผ่านการลด trap"            สูตรตั้งต้น = ค่ากลางวรรณกรรม
= เลือกคันโยกที่ควรลงทุน                      (4000 rpm, 100°C 30 min,
                                             antisolvent ~50-75% ของ spin)
                                             แล้วแปรทีละปุ่มในแล็บเดียวกัน
                                             = ไม่มี confound เพราะคุมเองหมด
```

**ML ส่วนนี้ไม่ได้หาค่า parameter** — มันหาว่า "ขั้นตอนไหนมี impact + ทำงานผ่านกลไกอะไร" ส่วนค่าตัวเลขที่ดีที่สุดเป็นหน้าที่ของแล็บ ซึ่งเป็นที่เดียวที่หาได้อย่างถูกต้องอยู่แล้ว เป้าหมายสุดท้ายอยู่ครบ แค่คำตอบมาจากสองแหล่งประกอบกัน

## 4. ผลเบื้องต้นที่ยืนยันว่าทาง 1 มีของจริง (คำนวณจากข้อมูลแล้ว 2026-08-04)

เทียบคู่ภายในเปเปอร์เดียวกัน (device ที่ดัดแปลงผิว HTL − control ของเปเปอร์นั้น) 11 เปเปอร์:

| Δ (treated − control) | median | ทิศทางบวกกี่เปเปอร์ |
|---|---|---|
| ΔPCE | **+2.09 %** | **11/11** |
| ΔFF | +2.62 | 11/11 |
| ΔJsc | +0.72 mA cm⁻² | 11/11 |
| ΔVoc | +0.045 V | 9/11 |
| **ΔVTFL** | **−0.145 V** | ลดลง **8/8** |
| Δretention@25C | +25 % | 3/3 (n เล็กมาก) |

- 11/11 ทิศเดียวกัน: ถ้าไม่มีผลจริง โอกาสเกิดสุ่ม ≈ 0.1% (sign test)
- **กลไกโผล่ในข้อมูลเอง**: VTFL ลดทุกเปเปอร์ที่วัด = trap ลดจริง → โซ่ `ดัดแปลงผิว → trap↓ → FF/Voc↑ → PCE↑` มีหลักฐานครบทุกข้อต่อ
- ขั้น modeling เต็ม (RF+SHAP+LOPO, คุม composition + etl_family, bootstrap CI) จะทำให้ตัวเลขพวกนี้แข็งแรงและแยกรายกลุ่มสารได้

⚠️ ข้อจำกัดที่ต้องประกาศเสมอ: **publication bias** — เปเปอร์มักตีพิมพ์เฉพาะการดัดแปลงที่สำเร็จ 11/11 จึงอาจสูงเกินความเป็นจริงของ "การดัดแปลงแบบสุ่มสักอย่าง" ผลที่รายงานจึงตีความว่า "การดัดแปลงที่ผ่านการคัดของแต่ละแล็บแล้ว ให้ผลขนาดนี้" ไม่ใช่ "ดัดแปลงอะไรก็ได้แล้วจะดีขึ้น"

## 5. งานนี้ต่างจากเปเปอร์ที่ตีพิมพ์แล้วอย่างไร — อะไรใหม่ อะไรไม่ใหม่

**สิ่งที่ไม่ใหม่ (ต้องยอมรับตรง ๆ):** ข้อความ "การดัดแปลงผิว HTL ช่วยเพิ่มประสิทธิภาพ" ไม่ใหม่ — เปเปอร์แต่ละฉบับใน dataset ก็อ้างแบบนี้กับสารของตัวเองอยู่แล้ว

**สิ่งที่ใหม่ (ไม่มีเปเปอร์เดี่ยวไหนทำได้):**

1. **การสังเคราะห์ข้ามแล็บอย่างเป็นระบบ (meta-analysis)** — เปเปอร์เดี่ยวพูดได้แค่ "สาร X ของฉัน ในแล็บฉัน ดีขึ้น Y%" ไม่มีใครตอบว่า "ปรากฏการณ์นี้ generalize ข้ามสาร (SAM / ionic liquid / Al₂O₃ / BDT-POZ) ข้ามแล็บ ข้ามสูตร perovskite ไหม ขนาดผลรวมเท่าไหร่" — เราตอบได้ เพราะรวม 11 การทดลอง A/B จากคนละแล็บมาไว้ในกรอบเดียว
2. **กลไกที่ยืนยันข้ามแล็บ** — เปเปอร์เดี่ยววัด trap ของตัวเอง เราแสดงว่า VTFL ลด **8/8 เปเปอร์อิสระ** = หลักฐานเชิงกลไกที่แข็งแรงกว่าเปเปอร์ไหน ๆ ใน dataset + วัดขนาด mediation (ผลผ่าน trap กี่ % ผ่านทางอื่นกี่ %) ซึ่งไม่มีเปเปอร์เดี่ยวทำ
3. **จัดอันดับกลุ่มสารบนสนามเดียวกัน** — SHAP แยกว่า SAM vs ionic liquid vs oxide ตัวไหนให้ผลผ่านกลไกไหน โดยคุมสูตรเคมี — การเทียบแบบนี้ทำไม่ได้ในเปเปอร์เดี่ยวเพราะแต่ละฉบับมีสารเดียว
4. **ระเบียบวิธีที่ตรงข้ามกับงาน ML-perovskite กระแสหลัก** — งาน ML ส่วนใหญ่ (เช่นที่ใช้ Perovskite Database หลักหมื่น device) เน้นหาวัสดุใหม่ เทรนข้าม lab โดย**ไม่คุม lab confound** และไม่มี mediator ของเราเล็กกว่ามากแต่: คลีนถึงระดับ audit รายค่า, LOPO กันลายนิ้วมือแล็บ, แยก within/between-paper อย่างชัดเจน, มี mediation — เป็นจุดขายเชิง methodology ที่เขียนเป็น contribution ได้
5. **Closed loop** — ผล ML ถูกออกแบบให้จบที่สูตรแล็บ + การทดลองยืนยันจริง ไม่ใช่จบที่ leaderboard

**ประโยคสรุป** งานนี้ไม่ได้ค้นพบว่า "การดัดแปลงผิว HTL ช่วย" — งานนี้เป็นงานแรก (ในขอบเขตที่สำรวจ) ที่**วัดว่าช่วยเท่าไหร่ ช่วยผ่านกลไกอะไร และ generalize แค่ไหน** ข้ามสาร-ข้ามแล็บ ด้วยข้อมูลที่ควบคุม confound ระดับเปเปอร์ แล้วปิดวงจรด้วยการทดลองจริง