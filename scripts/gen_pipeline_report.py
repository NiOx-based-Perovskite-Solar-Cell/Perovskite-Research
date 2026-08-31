# -*- coding: utf-8 -*-
"""สร้าง reports/data_pipeline_report.html -- เอกสารละเอียดตั้งแต่ Raw Data ถึง prepared_v2"""
import pandas as pd, numpy as np, glob, os, json, re, pathlib, collections, html

ROOT = pathlib.Path(".")
P = ROOT / "processed" / "prepared_v2"
_rep = pathlib.Path("reports/report.html").read_text(encoding="utf-8")
CSS = _rep[_rep.find("<style"):_rep.find("</style>") + 8]

T = {n: pd.read_csv(P / f"{n}.csv") for n in
     ["target_table", "layers", "materials", "process", "properties", "stability",
      "vendors", "devices", "device_layer_flags", "psk_composition"]}
tt = T["target_table"].copy(); tt["paper"] = tt.device_id.str.rsplit("_Sample", n=1).str[0]
RAW = sorted(glob.glob("Raw Data/*.xlsx"))
N_DEV, N_PAP = len(tt), tt.paper.nunique()

def esc(x): return html.escape(str(x))
def n(x): return f"{x:,}"

# ---------- ข้อเท็จจริงที่ดึงสด ----------
sheets = collections.Counter()
for f in RAW:
    try: sheets[tuple(pd.ExcelFile(f).sheet_names)] += 1
    except Exception: pass
stage_rows = {}
for st in ["normalize", "prepared", "prepared_v2"]:
    stage_rows[st] = {os.path.basename(p)[:-4]: pd.read_csv(p).shape
                      for p in sorted(glob.glob(f"processed/{st}/*.csv"))}
knob_cols = [c for c in T["process"].columns if "__" in c]
gates = []
for nb in ["notebooks/column_prepare.ipynb", "notebooks/recheck_comlumn_preapare.ipynb"]:
    d = json.load(open(nb, encoding="utf-8"))
    for c in d["cells"]:
        if c["cell_type"] != "code": continue
        for ln in "".join(c["source"]).split("\n"):
            if ln.strip().startswith("assert"):
                gates.append((os.path.basename(nb), ln.strip()))

def tbl(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'

# ---------- คำอธิบายคอลัมน์ ----------
GLOSS = {
 "device_id": "รหัสอุปกรณ์ = ชื่อไฟล์ต้นทาง (DOI + หมายเลขตัวอย่าง) · คีย์หลักของทุกตาราง",
 "doi_raw": "DOI ตามที่กรอกในไฟล์", "sample_no": "หมายเลขตัวอย่างภายในเปเปอร์",
 "source_file": "ชื่อไฟล์ Excel ต้นทาง", "source_sheet": "ชีตที่แถวนี้มาจาก",
 "layer_id": "เลขตำแหน่งชั้น 1-8 (คู่ = ชั้นหลัก · คี่กลาง = ชั้นเคลือบผิว)",
 "layer_type": "ชื่อชั้นตามที่กรอก", "layer_kind": "หลัก / เคลือบทับ / ผสมในน้ำหมึก",
 "material": "ชื่อวัสดุตามที่กรอก", "material_family": "ตระกูลวัสดุที่ยุบชื่อพ้องแล้ว",
 "thickness_nm": "ความหนาตามที่กรอก (ข้อความ)", "thickness_nm_parsed": "ความหนาเป็นตัวเลข (nm)",
 "thickness_nm_min": "ขอบล่างเมื่อกรอกเป็นช่วง", "thickness_nm_max": "ขอบบนเมื่อกรอกเป็นช่วง",
 "thickness_nm_note": "บันทึกว่าตีความค่านั้นยังไง",
 "step_order": "ลำดับขั้นตอนภายในชั้น", "process_type": "ชนิดขั้นตอน",
 "description": "คำอธิบายตามที่กรอก", "parameter": "ค่าพารามิเตอร์ (คั่นด้วย comma)",
 "unit": "หน่วย (คั่นด้วย comma จับคู่ตามตำแหน่งกับ parameter)",
 "param_fixed": "ธง: แถวนี้ถูกแก้ค่าที่พิมพ์ผิด", "param_recovered": "ธง: กู้ค่าจากช่องคำอธิบาย",
 "param_note": "เหตุผลที่แตกค่าไม่ได้ (ต้องว่างทุกแถว)",
 "property": "ชื่อค่าที่วัด ตามที่กรอก", "base_property": "ชื่อค่าที่วัดหลังตัดมิติอื่นออก",
 "stat": "champion / average", "scan": "ทิศทางการวัด RS / FS",
 "layer": "ชั้นที่ค่านั้นวัดจาก", "carrier": "ชนิดพาหะ (hole / electron)",
 "value": "ค่าตามที่กรอก", "value_parsed": "ค่าที่นำไปใช้จริง (ตัวเลข)",
 "value_after_unit_fix": "ค่าหลังแก้เฉพาะเรื่องหน่วย", "unit_corrected": "หน่วยหลังแก้",
 "unit_fix_note": "บันทึกการแก้หน่วย", "value_note": "บันทึกการแก้ที่เปลี่ยนตัวเลข",
 "error": "ค่าคลาดเคลื่อนตามที่กรอก", "remarks": "หมายเหตุจากผู้กรอก",
 "type": "บทบาทของสาร (สารตั้งต้น / สารเติม / ตัวทำละลาย …)",
 "solvent": "ตัวทำละลายตามที่กรอก", "solvent_1": "ตัวทำละลายตัวที่ 1 (ชื่อมาตรฐาน)",
 "solvent_2": "ตัวทำละลายตัวที่ 2", "solvent_ratio": "อัตราส่วนเป็นตัวเลข",
 "solvent_system": "ระบบตัวทำละลายมาตรฐานไว้จัดกลุ่ม", "solvent_note": "บันทึกกรณีตีความไม่ครบ",
 "amount": "ปริมาณตามที่กรอก", "amount_parsed": "ปริมาณเป็นตัวเลข",
 "amount_ratio": "กรณีที่กรอกเป็นอัตราส่วน", "amount_note": "บันทึกการตีความปริมาณ",
 "environment": "เงื่อนไขทดสอบตามที่กรอก", "stress_group": "กลุ่มชนิดความเครียดที่ยุบแล้ว",
 "encapsulation": "มีการห่อหุ้มหรือไม่",
 "temperature_c": "อุณหภูมิตามที่กรอก", "temperature_c_parsed": "อุณหภูมิเป็นตัวเลข",
 "humidity_pct": "ความชื้นตามที่กรอก", "humidity_pct_parsed": "ความชื้นเป็นตัวเลข",
 "time_hr": "ระยะเวลาทดสอบ (ชั่วโมง)",
 "retention_pct": "ประสิทธิภาพที่เหลือตามที่กรอก", "retention_pct_parsed": "ประสิทธิภาพที่เหลือเป็นตัวเลข",
 "retention_pct_censor": "ธงว่าค่าเป็นขอบเขต (< หรือ >)",
 "t80_hr": "เวลาที่ประสิทธิภาพเหลือ 80%", "t80_hr_parsed": "T80 เป็นตัวเลข",
 "initial_pce": "ประสิทธิภาพก่อนทดสอบ", "final_pce": "ประสิทธิภาพหลังทดสอบ",
 "purity": "ความบริสุทธิ์ตามที่กรอก", "purity_pct": "ความบริสุทธิ์เป็นตัวเลข",
 "purity_qualifier": "เครื่องหมายกำกับ (> / ~)", "purity_kind": "ชนิดของค่าที่กรอกมา",
 "vendor": "ผู้จำหน่าย",
 "comp_source": "ที่มาของสูตรเคมี (สูตรนาม / คำนวณจากสารตั้งต้น)",
 "cross_check": "ผลเทียบสองแหล่งอิสระ", "precursor_status": "เหตุที่คำนวณจากสารตั้งต้นได้/ไม่ได้",
 "b_site": "ไอออนกลาง (Pb ทั้งหมด)",
}
for i in ["Cs", "FA", "MA", "Rb", "K"]: GLOSS[f"a_{i}"] = f"สัดส่วน {i} ในตำแหน่ง A"
for i in ["I", "Br", "Cl"]: GLOSS[f"x_{i}"] = f"สัดส่วน {i} ในตำแหน่ง X"

TT_GLOSS = {
 "is_coated": "มีชั้นเคลือบผิวอย่างน้อยหนึ่งชั้น", "n_coating_layers": "จำนวนชั้นเคลือบผิว",
 "coat_htl": "เคลือบผิว HTL", "coat_psk": "เคลือบผิวชั้นดูดแสง", "coat_etl": "เคลือบผิว ETL",
 "anneal_htl": "มีขั้นอบ HTL", "anneal_psk": "มีขั้นอบชั้นดูดแสง",
 "htl_mod_kind": "ชนิดการดัดแปลง HTL: เคลือบทับ / ผสมในน้ำหมึก", "psk_mod_kind": "ชนิดการดัดแปลงชั้นดูดแสง",
 "jv_variant": "ชุดค่า J-V ที่เลือกใช้ (champion / average / ไม่ระบุ)",
 "jv_scan": "ทิศทางการวัดของชุดที่เลือก", "pce": "ประสิทธิภาพรวม (%)",
 "voc": "แรงดันเปิดวงจร (V)", "jsc": "กระแสลัดวงจร (mA cm⁻²)", "ff": "ฟิลล์แฟกเตอร์ (%)",
 "vtfl": "แรงดันเติมกับดักให้เต็ม (V) — ตัวแทนความหนาแน่นกับดัก · ตัวกลางของช่องทางอิเล็กทรอนิกส์ (เกาะกับ ΔFF)",
 "trap_density": "ความหนาแน่นกับดักฝั่งโฮล (cm⁻³)",
 "contact_angle": "มุมสัมผัสหยดน้ำบนผิว HTL (องศา) — ตัวแทนสมบัติการเปียกของผิวที่เพอรอฟสไกต์ไปตกผลึกทับ · "
                  "ตัวกลางของช่องทางสัณฐาน (เกาะกับ ΔJsc ไม่เกาะ ΔFF)",
 "grain_size": "ขนาดเกรนของฟิล์มเพอรอฟสไกต์ (nm) — มีค่าจริงน้อยกว่าจำนวนแถวมาก "
               "เพราะหลายเปเปอร์แสดงภาพ SEM แต่ไม่รายงานตัวเลข",
 "trap_density_electron": "ความหนาแน่นกับดักฝั่งอิเล็กตรอน",
 "defect_density_reported": "ค่าจุดบกพร่องที่เปเปอร์รายงานแต่ไม่รวมเข้า trap_density",
 "trap_implied_thickness_nm": "ความหนาฟิล์มที่คำนวณย้อนจากคู่ (VTFL, กับดัก)",
 "trap_scale_flag": "ธงว่าค่ากับดักเทียบข้ามเปเปอร์ได้ไหม",
 "rrec": "ความต้านทาน recombination", "rrec_scale_flag": "ธงว่าค่า Rrec หลุดสเกลไหม",
 "rs": "ความต้านทานอนุกรมของเซลล์", "sheet_resistance_htl": "ความต้านทานแผ่นของฟิล์ม HTL",
 "retention_25c": "ประสิทธิภาพที่เหลือหลังทดสอบที่ ~25 °C (%)",
 "degradation_rate": "อัตราการเสื่อมต่อชั่วโมง (%/hr)",
 "psk_spin_rpm": "ความเร็วปั่นขั้นสุดท้ายของชั้นดูดแสง",
 "psk_spin_last_s": "ความยาวขั้นปั่นสุดท้าย", "psk_spin_total_s": "ความยาวโปรแกรมปั่นทั้งหมด",
 "psk_anneal_C": "อุณหภูมิอบชั้นดูดแสง", "psk_anneal_time_s": "เวลาอบชั้นดูดแสง",
 "htl_anneal_C": "อุณหภูมิอบชั้น HTL",
 "antisolvent_vol_uL": "ปริมาณ antisolvent", "antisolvent_timing_s": "จังหวะหยดตามที่เปเปอร์เขียน",
 "antisolvent_timing_ref": "จุดอ้างอิงของจังหวะหยด", "antisolvent_drop_s": "จังหวะหยดบนแกนเดียวกัน (วินาที)",
 "antisolvent_drop_frac_of_total": "สัดส่วนจังหวะหยดต่อโปรแกรมปั่นทั้งหมด (ตัวหลัก)",
 "antisolvent_drop_frac_of_last": "สัดส่วนต่อขั้นปั่นสุดท้าย (ทางเลือก)",
 "etl_family": "ตระกูลวัสดุชั้น ETL",
}
GLOSS.update(TT_GLOSS)

# ---------- ประกอบ HTML ----------
h = []
A = h.append

A(f'''<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>รายงานเส้นทางข้อมูล — Raw Data ถึงชุดข้อมูลพร้อมใช้</title>{CSS}</head><body>

<section class="cover">
  <div class="kicker">เอกสารทางเทคนิค · กันยายน 2569</div>
  <h1>เส้นทางข้อมูลโดยละเอียด</h1>
  <div class="sub">จากไฟล์ Excel ต้นทาง ถึงชุดข้อมูลที่พร้อมสร้างแบบจำลอง<br>
  ทุกขั้นตอน ทุกการแก้ ทุกด่านตรวจ และความหมายของทุกคอลัมน์</div>
  <div class="chips">
    <span class="chip">{N_DEV} อุปกรณ์</span><span class="chip">{N_PAP} เปเปอร์</span>
    <span class="chip">{len(RAW)} ไฟล์ต้นทาง</span><span class="chip">10 ตารางผลลัพธ์</span>
    <span class="chip">{len(gates)} ด่านตรวจ</span>
  </div>
  <p class="lead" style="margin-top:2.2em">เอกสารนี้เขียนให้คนที่ต้อง<b>ตรวจสอบ ทำซ้ำ หรือรับช่วงต่อ</b>
  ทุกตัวเลขในเอกสารดึงจากไฟล์จริงตอนสร้างเอกสาร ไม่ได้พิมพ์ค้างไว้
  จึงใช้เป็นเอกสารอ้างอิงคู่กับชุดข้อมูลได้โดยตรง</p>
</section>

<h2>0 · วิธีอ่านเอกสารนี้</h2>
<p>เอกสารแบ่งตาม<b>ลำดับที่ข้อมูลไหลจริง</b> แต่ละขั้นตอบสามคำถามเดียวกันเสมอ:
<b>รับอะไรเข้า</b> · <b>ทำอะไรกับมัน และทำไม</b> · <b>ตรวจอะไรก่อนปล่อยผ่าน</b></p>
<div class="box key">
  <h4>หลักการที่ใช้ตลอดทั้งเส้น</h4>
  <ul>
    <li><b>ไม่แก้ไฟล์ต้นทาง</b> — ไฟล์ Excel ทุกไฟล์คงเดิม การแก้ทุกอย่างเกิดในโค้ดและบันทึกเหตุผลไว้</li>
    <li><b>ไม่ทับคอลัมน์ดิบ</b> — ค่าที่ตีความแล้วไปอยู่คอลัมน์ใหม่เสมอ (ลงท้าย <code>_parsed</code> / <code>_note</code>)
        ค่าที่ผู้กรอกใส่มาจึงยังตรวจย้อนได้ตลอด</li>
    <li><b>ทุกการตีความต้องมีร่องรอย</b> — ถ้าค่าไหนถูกแปลง ต้องมีคอลัมน์บันทึกว่าแปลงเพราะอะไร</li>
    <li><b>ด่านตรวจหยุดงานได้</b> — เงื่อนไขที่ต้องเป็นจริงเขียนเป็น <code>assert</code>
        ถ้าไม่ผ่าน กระบวนการหยุดทันที ไม่เขียนไฟล์ผลลัพธ์</li>
    <li><b>เขียนดิสก์จุดเดียวต่อขั้น</b> แล้วอ่านกลับมาเทียบกับตารางในหน่วยความจำทีละเซลล์</li>
  </ul>
</div>

<h2>1 · ข้อมูลตั้งต้น</h2>
<p>ไฟล์ Excel {len(RAW)} ไฟล์ · <b>1 ไฟล์ = 1 อุปกรณ์</b> · สกัดด้วยมือจากเปเปอร์ {N_PAP} ฉบับ
ชื่อไฟล์คือ <code>DOI_SampleN.xlsx</code> ซึ่งกลายเป็นรหัสอุปกรณ์และใช้แยกว่าอุปกรณ์ไหนมาจากเปเปอร์เดียวกัน
(ตัดที่ <code>_Sample</code> ตัวสุดท้าย) — จุดนี้สำคัญเพราะการวิเคราะห์ทั้งหมดจัดกลุ่มตามเปเปอร์</p>''')

A("<h3>1.1 ชุดชีตที่พบ</h3>")
A(tbl(["จำนวนไฟล์", "ชีตที่มี"],
      [[f'<span class="num">{v}</span>', " · ".join(f"<code>{esc(x)}</code>" for x in k)]
       for k, v in sheets.most_common()]))
A('''<p class="small muted">ลำดับชีตต่างกันบ้างระหว่างไฟล์ ไม่มีผลเพราะตัวแปลงอ่านตามชื่อชีต ไม่ใช่ตามตำแหน่ง ·
ชีต <code>NiOx synthesis</code> มีเฉพาะเปเปอร์ที่สังเคราะห์ผง NiOx เอง และ<b>ไม่ใช่ขั้นตอนสร้างเซลล์</b>
จึงถูกกันออกจากการดึงค่าพารามิเตอร์ทุกครั้ง (ดู §5.7)</p>''')

ex = RAW[0]
rows = []
for s_ in pd.ExcelFile(ex).sheet_names:
    cols = list(pd.read_excel(ex, s_, nrows=0).columns)
    rows.append([f"<code>{esc(s_)}</code>", " · ".join(f"<code>{esc(c)}</code>" for c in cols)])
A("<h3>1.2 คอลัมน์ในแต่ละชีต</h3>")
A(tbl(["ชีต", "คอลัมน์"], rows))
A('''<div class="box warn"><h4>ความไม่สม่ำเสมอที่พบในไฟล์ต้นทาง — และวิธีรับมือ</h4>
<p>ไฟล์ถูกกรอกโดยคนหลายคนต่างช่วงเวลา จึงมีรูปแบบต่างกันจริง ทั้งหมดนี้ถูกจัดการในขั้นถัดไป ไม่ใช่แก้ที่ไฟล์:</p>
<ul>
  <li><b>หัวคอลัมน์ชีตเสถียรภาพเขียนได้หลายแบบ</b> เช่น <code>temperature (celsius)</code> กับ <code>Temperature (°C)</code>
      และคอลัมน์ผลลัพธ์เป็นได้ทั้ง <code>Retention (%)</code> / <code>Normalize PCE (%)</code> / <code>Final normalize PCE (%)</code></li>
  <li><b>ชีตค่าที่วัดมีทั้งแบบมีและไม่มีคอลัมน์ค่าคลาดเคลื่อน</b></li>
  <li><b>ค่าเชิงปริมาณบางแถวถูกกรอกในช่องคำอธิบายแทนช่องค่า</b> — ตัวแปลงอ่านไม่เจอ ข้อมูลหายเงียบ (แก้ใน §5.2)</li>
  <li><b>เลขชั้นเป็นข้อความคนละแบบ</b> (<code>1</code> กับ <code>1.0</code>) ทำให้เชื่อมตารางได้ศูนย์แถวโดยไม่มีคำเตือน</li>
</ul></div>''')

# ---------- 2 เส้นทาง ----------
A("<h2>2 · เส้นทางข้อมูลทั้งเส้น</h2>")
A('''<div class="pipe">
<div class="pstep"><b>0 · Raw Data/*.xlsx</b><div class="d">ไฟล์ Excel ต้นทาง 1 ไฟล์ = 1 อุปกรณ์</div></div>
<div class="pstep"><b>1 · explore_reschema</b><div class="d">แตกชีตเป็นตารางยาว ผูกทุกแถวด้วยรหัสอุปกรณ์ วางระบบเลขชั้น</div></div>
<div class="pstep"><b>2 · column_prepare</b><div class="d">ทำความสะอาดข้อความ · แปลงค่าให้เป็นตัวเลข · แตกชื่อค่าที่วัดเป็น 5 มิติ</div></div>
<div class="pstep"><b>3 · recheck_comlumn_preapare</b><div class="d">ตรวจซ้ำ · แก้ตามหลักฐาน · แตกพารามิเตอร์ · สร้างตารางเป้าหมาย</div></div>
<div class="pstep"><b>4 · ชุดข้อมูลพร้อมใช้</b><div class="d">10 ตารางใน processed/prepared_v2/</div></div>
</div>''')

rows = []
allt = sorted(set(stage_rows["normalize"]) | set(stage_rows["prepared"]) | set(stage_rows["prepared_v2"]))
for k in allt:
    def cell(st):
        v = stage_rows[st].get(k)
        return f'{n(v[0])} × {v[1]}' if v else '<span class="muted">—</span>'
    rows.append([f"<code>{k}</code>", cell("normalize"), cell("prepared"), cell("prepared_v2")])
A("<h3>2.1 ขนาดตารางในแต่ละขั้น (แถว × คอลัมน์)</h3>")
A(tbl(["ตาราง", "หลังขั้น 1", "หลังขั้น 2", "หลังขั้น 3"], rows, "num"))
A('''<p class="small muted"><b>จำนวนแถวไม่เปลี่ยนตลอดเส้น</b> (ยกเว้น <code>properties</code> ที่ +1 แถวจากการเติมค่าที่เปเปอร์รายงานแต่ไฟล์ขาด)
คอลัมน์เพิ่มขึ้นเพราะทุกการตีความไปอยู่คอลัมน์ใหม่ ไม่ทับของเดิม · ตาราง 3 ตารางที่โผล่เฉพาะขั้น 3
เป็นตารางที่ขั้นนั้นสร้างขึ้นใหม่</p>''')

for _part in ["gen_part2.py", "gen_part3.py"]:
    exec(pathlib.Path("scripts").joinpath(_part).read_text(encoding="utf-8"))
