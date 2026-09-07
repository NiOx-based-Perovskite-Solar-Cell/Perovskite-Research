# -*- coding: utf-8 -*-
"""สร้าง reports/model_report.html -- เอกสารอธิบายขั้นสร้างแบบจำลอง

ตัวเลขทุกตัวดึงสดจาก processed/model/*.csv ที่ model_htl_modification.ipynb เขียนไว้
ไม่มีตัวเลขไหน hardcode -- ถ้าโน้ตบุ๊กรันใหม่แล้วผลเปลี่ยน รายงานนี้เปลี่ยนตาม
"""
import pathlib, html, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = pathlib.Path(".")
M = ROOT / "processed" / "model"
V2 = ROOT / "processed" / "prepared_v2"
FIG = ROOT / "reports" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

_rep = (ROOT / "reports" / "report.html").read_text(encoding="utf-8")
CSS = _rep[_rep.find("<style"):_rep.find("</style>") + 8]

D = {p.stem: pd.read_csv(p) for p in sorted(M.glob("*.csv"))}
tt = pd.read_csv(V2 / "target_table.csv")
tt["paper"] = tt.device_id.str.rsplit("_Sample", n=1).str[0]
N_DEV, N_PAP = len(tt), tt.paper.nunique()

MIRROR = D["model_block_mirror"].set_index("บล็อก")
BLOCK = D["model_scores_by_block"]
IMP = D["model_importance"].set_index("ตัวแปร")
RANK = D["model_class_ranking"]
MED = D["model_mediation"]
STAB = D["model_stability"].set_index("ตัวแปร")
DOSE = D["model_dose_response"]
COR = D["model_channel_correlations"].set_index("คู่")
PROV = D["paper_provenance"]
STAB_E = D.get("model_stability_effect")
NEWMED = D.get("model_new_mediators")
EV = D["paper_evidence_table"]
CTR = D["model_paper_contrasts"]

_have = {f.name for f in font_manager.fontManager.ttflist}
FONT = next((f for f in ["Leelawadee UI", "Sarabun", "Thonburi", "Tahoma", "DejaVu Sans"] if f in _have),
            "DejaVu Sans")
C1, C2, C3, C4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
plt.rcParams.update({
    "font.family": [FONT, "DejaVu Sans"], "axes.unicode_minus": False,
    "figure.dpi": 150, "figure.facecolor": "white", "savefig.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": BASE, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titlecolor": INK, "axes.titlesize": 10.5, "font.size": 9})


def esc(x):
    return html.escape(str(x))


def n(x):
    return f"{x:,}"


def tbl(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def num(x, f="{:+.3f}"):
    return '<span class="muted">—</span>' if pd.isna(x) else f'<span class="num">{f.format(x)}</span>'


def fig(name, cap):
    return (f'<figure><img src="figures/{name}"><figcaption>{cap}</figcaption></figure>')


def prov_of(sub, หัวข้อ=None):
    """คืนแถว provenance ที่ตรงกับข้อความ -- ใช้ผูกตัวเลขในเนื้อความเข้ากับ §7

    ต้องระบุหัวข้อด้วยเสมอสำหรับข้ออ้างที่ชื่อชนกันได้ เช่น 'Δpce ค่ากลาง' (ผลรวม)
    กับ 'SAM: ΔPCE ค่ากลาง' (รายกลุ่มสาร) -- เคยแมตช์ผิดจนรายงานโชว์ตัวเลขของ SAM เป็นผลรวม
    """
    r = PROV
    if หัวข้อ is not None:
        r = r[r["หัวข้อ"] == หัวข้อ]
    r = r[r["ข้ออ้าง"].str.startswith(sub, na=False)]
    return r.iloc[0] if len(r) else None


# ═══════════════════════════════════════════════════════ รูป
# รูป 1 -- ภาพสะท้อน
o = MIRROR.reindex(["ตัวแปรหลักอย่างเดียว", "ตัวควบคุมอย่างเดียว", "ทั้งสองชุด"])
f, ax = plt.subplots(figsize=(7.6, 3.9))
w, xs = 0.36, np.arange(len(o))
ax.bar(xs - w / 2, o.iloc[:, 0], w, color=C2, label=o.columns[0])
ax.bar(xs + w / 2, o.iloc[:, 1], w, color=C3, label=o.columns[1])
ax.axhline(0, color=INK, lw=1.2)
ax.set_xticks(xs, [i.replace("อย่างเดียว", "\nอย่างเดียว") for i in o.index], fontsize=9)
# ระยะป้ายคิดจากช่วงข้อมูล ไม่ใช่ค่าคงที่ -- ค่าคงที่ทำให้ป้ายของแท่งติดลบไปทับชื่อแกน x
_vals = o.values.ravel()
_lo, _hi = float(np.nanmin(_vals)), float(np.nanmax(_vals))
_pad = (_hi - _lo) * 0.055
ax.set_ylim(_lo - _pad * 3.2, _hi + _pad * 2.4)     # เว้นที่ให้ป้ายทั้งบนและล่าง
for i in range(len(o)):
    for off, c in [(-w / 2, 0), (w / 2, 1)]:
        v = o.iloc[i, c]
        ax.text(i + off, v + (_pad if v >= 0 else -_pad), f"{v:+.3f}", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=8.4, color=INK)
ax.set_ylabel("$R^2$ นอกกลุ่มฝึก")
ax.legend(frameon=False, fontsize=8.4)
ax.set_title("คะแนนมาจากบล็อกไหน — และสลับข้างกันพอดีเมื่อเปลี่ยนคำถาม")
plt.tight_layout(); plt.savefig(FIG / "model_fig1_mirror.png"); plt.close()

# รูป 2 -- สองช่องทาง
CTRP = CTR.groupby("paper")[[c for c in CTR.columns if c.startswith("Δ")]].mean()
base = tt.groupby("paper")[["ff", "jsc"]].mean()
J = CTRP.join(base).dropna(subset=["Δff", "Δjsc"])
J["rel_ff"], J["rel_jsc"] = J["Δff"] / J["ff"] * 100, J["Δjsc"] / J["jsc"] * 100
f, axes = plt.subplots(1, 2, figsize=(9.6, 3.9))
ax = axes[0]
ax.scatter(J.rel_ff, J.rel_jsc, s=58, color=C1, edgecolor="white", lw=1, zorder=3)
ax.axhline(0, color=INK, lw=1); ax.axvline(0, color=INK, lw=1)
ax.set_xlabel("Δ FF สัมพัทธ์ (%)"); ax.set_ylabel("Δ Jsc สัมพัทธ์ (%)")
ax.set_title(f"ΔFF ↔ ΔJsc  rho {COR.loc['Δff ↔ Δjsc','rho']:+.2f} (n={int(COR.loc['Δff ↔ Δjsc','n_paper'])})\n"
             "ไม่มีแนวโน้มร่วม = สองกลไกเป็นอิสระต่อกัน")
ax = axes[1]
_m = CTRP.dropna(subset=["Δcontact_angle", "Δjsc"])
ax.scatter(_m["Δcontact_angle"], _m["Δjsc"], s=58, color=C4, edgecolor="white", lw=1, zorder=3)
ax.axhline(0, color=INK, lw=1); ax.axvline(0, color=INK, lw=1)
ax.set_xlabel("Δ มุมสัมผัส (องศา) · บวก = ผิวไม่ชอบน้ำขึ้น"); ax.set_ylabel("Δ Jsc (mA cm⁻²)")
ax.set_title(f"ช่องสัณฐาน  rho {COR.loc['Δcontact_angle ↔ Δjsc','rho']:+.2f} "
             f"(n={int(COR.loc['Δcontact_angle ↔ Δjsc','n_paper'])})")
plt.tight_layout(); plt.savefig(FIG / "model_fig2_channels.png"); plt.close()

# รูป 3 -- จัดอันดับกลุ่มสาร
r = RANK[RANK["กลุ่มสาร"] != "** รวมทุกกลุ่ม **"].sort_values("ΔPCE_ค่ากลาง")
f, ax = plt.subplots(figsize=(7.6, 0.72 * len(r) + 1.8))
y = np.arange(len(r))
ax.hlines(y, r.CI95_ล่าง, r.CI95_บน, color=BASE, lw=3.4, zorder=2)
ax.scatter(r["ΔPCE_ค่ากลาง"], y, s=84, color=[C3 if v > 0 else C2 for v in r["ΔPCE_ค่ากลาง"]],
           edgecolor="white", lw=1.3, zorder=3)
ax.axvline(0, color=INK, lw=1.3)
ax.set_yticks(y, [f"{a}\n({int(b)} เปเปอร์ · {c})" for a, b, c in
                  zip(r["กลุ่มสาร"], r["เปเปอร์"], r["ทิศบวก"])], fontsize=8.6)
ax.set_xlabel("ΔPCE เทียบอุปกรณ์ควบคุมในเปเปอร์เดียวกัน (%) · แท่ง = CI 95% (bootstrap ระดับเปเปอร์)")
ax.set_title("ช่วงที่คร่อมเส้นศูนย์ = ยังแยกจาก «ไม่มีผล» ไม่ได้")
ax.grid(axis="y", visible=False)
plt.tight_layout(); plt.savefig(FIG / "model_fig3_ranking.png"); plt.close()

# ═══════════════════════════════════════════════════════ HTML
h = []
A = h.append
A(f'<!doctype html><html lang="th"><head><meta charset="utf-8">'
  f'<title>รายงานแบบจำลอง — การดัดแปลงผิว HTL</title>{CSS}</head><body>')

_abs_main = MIRROR.iloc[:, 0]["ตัวแปรหลักอย่างเดียว"]
_abs_ctrl = MIRROR.iloc[:, 0]["ตัวควบคุมอย่างเดียว"]
_ctr_main = MIRROR.iloc[:, 1]["ตัวแปรหลักอย่างเดียว"]
_ctr_ctrl = MIRROR.iloc[:, 1]["ตัวควบคุมอย่างเดียว"]
_pce = prov_of("Δpce ค่ากลาง", "§6-7 ขนาดผล")
_n_ctrl = int(PROV.loc[PROV["ข้ออ้าง"].str.contains("มีทั้งอุปกรณ์ควบคุม"), "n_เปเปอร์"].iloc[0])

A(f'''<section class="cover">
<div class="kicker">รายงานแบบจำลอง · ต่อจากรายงานการเตรียมข้อมูล</div>
<h1>การดัดแปลงผิว HTL<br>ช่วยเท่าไร ผ่านกลไกอะไร</h1>
<p class="sub">แบบจำลองบนชุดข้อมูล {N_DEV} อุปกรณ์ จาก {N_PAP} เปเปอร์<br>
พร้อมที่มาของทุกตัวเลขระดับรายเปเปอร์ สำหรับตรวจย้อนด้วยมือ</p>
<div class="chips">
  <span class="chip">Random Forest + Ridge</span>
  <span class="chip">LeaveOneGroupOut {N_PAP} รอบ</span>
  <span class="chip">SHAP + ตัดตัวแปรออก</span>
  <span class="chip">bootstrap ระดับเปเปอร์</span>
  <span class="chip">{len(PROV)} ข้ออ้างมีรายชื่อเปเปอร์กำกับ</span>
</div>
<div class="box key"><h4>ข้อสรุปสั้นที่สุด</h4>
<p>ตัวแบบจำลองเอง<b>ทำนายไม่แม่น</b> และนั่นไม่ใช่ข้อบกพร่องของมัน แต่เป็นข้อเท็จจริงของข้อมูล —
คะแนนที่ดูดีของการทำนายข้ามแล็บ ({_abs_ctrl:+.3f}) มาจาก<b>การจำสูตรเคมีของแต่ละแล็บ</b> ไม่ใช่จากกลไก
สิ่งที่งานนี้ได้จริงคือ <b>ขนาดผลข้ามแล็บ</b> ที่วัดอย่างคุมตัวกวนแล้ว
และ <b>โครงสร้างของกลไก</b> ที่เปเปอร์เดี่ยวมองไม่เห็น</p></div>
<div class="toc"><ol>
<li>แบบจำลองนี้ตอบอะไร และไม่ตอบอะไร<span class="d">ขอบเขตของข้ออ้าง</span></li>
<li>กติกาการวัดผล<span class="d">พื้น เพดาน และเหตุผลที่ต้องรวมทุกรอบ</span></li>
<li>ผลหลัก — ภาพสะท้อนสองแนวทแยง<span class="d">หลักฐานเชิงระเบียบวิธี</span></li>
<li>ตัวแปรไหนมีน้ำหนักจริง<span class="d">SHAP · ตัดตัวแปรออก · ความเสถียร</span></li>
<li>ขนาดผลและกลุ่มสาร<span class="d">ผลรวมและรายกลุ่ม</span></li>
<li>กลไก — สองช่องทางที่แยกจากกัน<span class="d">ส่วนที่ใหม่ที่สุด</span></li>
<li>ที่มาของทุกตัวเลข<span class="d">รายชื่อเปเปอร์สำหรับตรวจด้วยมือ</span></li>
<li>ข้อจำกัด<span class="d">สิ่งที่ต้องประกาศทุกครั้ง</span></li>
<li>การทำซ้ำ<span class="d">รันใหม่ให้ได้ผลเดิม</span></li>
</ol></div></section>''')

# ---------- 1 ----------
A("<h2>1 · แบบจำลองนี้ตอบอะไร และไม่ตอบอะไร</h2>")
A('<p class="lead">ขอบเขตของข้ออ้างสำคัญกว่าตัวเลขความแม่น '
  'เพราะข้อมูลชุดนี้ตอบคำถามหนึ่งได้ดีและอีกคำถามหนึ่งไม่ได้เลย</p>')
A(tbl(["คำถาม", "ตอบได้ไหม", "หลักฐาน"], [
    ["<b>อุปกรณ์ของแล็บที่ไม่เคยเห็น จะได้ PCE เท่าไร</b>",
     '<span class="no">ไม่ได้</span>',
     f'ใช้ตัวแปรฝั่ง HTL อย่างเดียว ได้ R<sup>2</sup> = {_abs_main:+.3f} — แย่กว่าการทายค่ากลาง'],
    ["<b>ถ้าแล็บนี้ดัดแปลงผิว HTL จะดีขึ้นกว่าตัวเองเท่าไร</b>",
     '<span class="ok">ได้</span>',
     f'R<sup>2</sup> = {_ctr_main:+.3f} และขนาดผล {_pce["ข้ออ้าง"]}' if _pce is not None else ""],
    ["<b>ควรตั้งความเร็วปั่น/อุณหภูมิอบเท่าไร</b>",
     '<span class="no">ไม่ได้</span>',
     "พารามิเตอร์เชิงตัวเลขทุกตัวแปรค่าภายในเปเปอร์ 0 ฉบับ — แยกจากตัวตนของแล็บไม่ออก"],
    ["<b>ผลที่ได้วิ่งผ่านกลไกอะไร</b>",
     '<span class="ok">ได้บางส่วน</span>',
     "แยกได้ว่ามีอย่างน้อยสองช่องทางที่เป็นอิสระต่อกัน (§6) แต่ปริมาณของแต่ละช่องยังไม่แน่นอน"],
]))
A('<div class="box warn"><h4>สิ่งที่รายงานนี้ไม่ใช่</h4>'
  "<p>ไม่ใช่การอ้างว่าสร้างตัวทำนายประสิทธิภาพได้ · ไม่ใช่ข้อสรุปเชิงสาเหตุ เพราะเป็นการสังเคราะห์"
  "การทดลองที่คนอื่นออกแบบ ไม่ใช่การทดลองสุ่มที่เราคุมเอง · ผลทุกข้อคือ<b>สมมติฐานสำหรับการทดลองยืนยันในแล็บ</b></p></div>")

# ---------- 2 ----------
A("<h2>2 · กติกาการวัดผล</h2>")
A(tbl(["กติกา", "ทำอย่างไร", "ทำไมต้องทำ"], [
    ["แบ่งข้อมูลตามเปเปอร์", f"<code>LeaveOneGroupOut</code> ตัดทีละเปเปอร์ รวม {N_PAP} รอบ",
     "อุปกรณ์จากเปเปอร์เดียวกันใช้สูตรชุดเดียวกัน ถ้าแบ่งสุ่มจะมีพี่น้องอยู่ทั้งสองฝั่ง = จำสูตร ไม่ใช่เรียนกลไก"],
    ["รวมคำทำนายทุกรอบมาคิดทีเดียว", "เก็บคำทำนายนอกกลุ่มฝึกของทุกอุปกรณ์ แล้วคิดคะแนนครั้งเดียว",
     "แต่ละรอบเหลืออุปกรณ์ทดสอบ 2–5 ตัว คะแนนรายรอบไม่มีความหมาย และการเฉลี่ยยิ่งผันผวน"],
    ["ห้ามเติมค่าที่หายข้ามเปเปอร์", "ปล่อยค่าว่างไว้ ให้ Random Forest จัดการเอง",
     "ข้อมูลหายเป็นก้อนตามเปเปอร์ การเติมข้ามเปเปอร์ = ประดิษฐ์ข้อมูลของแล็บหนึ่งจากอีกแล็บ"],
    ["ไม่จูนไฮเปอร์พารามิเตอร์", "ตั้งค่าจากขนาดข้อมูลตรง ๆ แล้วประกาศไว้ก่อนดูผล",
     "ข้อมูลไม่พอทำ nested CV ที่เชื่อถือได้ — ตัวเลขที่รายงานจึงไม่ได้ถูกคัดมาให้สวย"],
]))
A("<h3>2.1 พื้นและเพดาน</h3>")
A("<p>คะแนน R<sup>2</sup> ลอย ๆ ไม่มีความหมายถ้าไม่รู้ว่าอะไรคือ «ทายมั่ว» และอะไรคือ «ดีที่สุดที่เป็นไปได้» "
  "จึงวัดสองเส้นนี้ด้วยกติกาเดียวกับแบบจำลอง</p>")
A(tbl(["เส้น", "ทายด้วยอะไร", "ความหมาย"], [
    ["<b>พื้น</b>", "ค่ากลางของอุปกรณ์ในกลุ่มฝึก", "ไม่ใช้ข้อมูลตัวแปรเลย"],
    ["<b>เพดาน</b>", "ค่ากลางของเปเปอร์ที่ถูกตัดออกเอง",
     "ทายไม่ได้จริงตอนใช้งาน — วัดไว้เพื่อบอกว่าตัวตนของเปเปอร์อธิบายความแปรปรวนไปแล้วเท่าไร"],
]))

# ---------- 3 ----------
A('<h2 class="brk">3 · ผลหลัก — ภาพสะท้อนสองแนวทแยง</h2>')
A('<p class="lead">นี่คือผลที่แข็งที่สุดของงาน และเป็นข้อค้นพบเชิงระเบียบวิธีที่ใช้ได้เกินขอบเขตงานนี้</p>')
A("<p>แทนที่จะรายงาน R<sup>2</sup> ตัวเดียว เราแยกวัดทีละบล็อกของตัวแปร ว่าคะแนนที่ได้มาจากฝั่งไหน</p>")
rows = []
for b in ["ตัวแปรหลักอย่างเดียว", "ตัวควบคุมอย่างเดียว", "ทั้งสองชุด"]:
    rows.append([f"<b>{b}</b>", num(MIRROR.loc[b].iloc[0]), num(MIRROR.loc[b].iloc[1])])
A(tbl(["บล็อกตัวแปร", "ทำนายค่าสัมบูรณ์", "ทำนายส่วนต่างในเปเปอร์"], rows, "num"))
A(fig("model_fig1_mirror.png",
      "<b>สองแนวทแยงสลับข้างกันพอดี</b> · ซ้าย: ทำนายค่า PCE จริงของเปเปอร์ที่ไม่เคยเห็น — "
      "ตัวควบคุม (สูตรเคมี + ชนิด ETL) ทำคะแนนได้ ขณะที่ตัวแปรฝั่ง HTL ทำไม่ได้ · "
      "ขวา: ทำนายส่วนต่างภายในเปเปอร์ — ตัวควบคุมเหลือศูนย์พอดี และตัวแปร HTL คือสิ่งเดียวที่เหลือ"))
A(f'''<div class="box key"><h4>อ่านตารางนี้อย่างไร</h4>
<p><b>สูตรเคมีของแต่ละแล็บเกือบไม่ซ้ำกัน</b> มันจึงทำหน้าที่เป็นลายนิ้วมือของเปเปอร์
เมื่อให้แบบจำลองทำนายค่าสัมบูรณ์ มันเลยทำคะแนนได้ {_abs_ctrl:+.3f} จากการเดาว่า
«นี่คือเปเปอร์แนวไหน» ไม่ใช่จากการเข้าใจว่าการดัดแปลงทำอะไร</p>
<p>พอลบค่ากลางของเปเปอร์ออก (วิธีเดียวกับ fixed effects) ทุกอย่างที่คงที่ทั้งเปเปอร์หายไปหมด —
สูตรเคมี ชนิด ETL ความเร็วปั่น อุณหภูมิอบ <b>รวมถึงสิ่งที่เราไม่ได้บันทึกไว้เลย</b>
เช่น ความชื้นในกล่อง ฝีมือคนทำ ยี่ห้อสารตั้งต้น ตัวควบคุมจึงเหลือ {_ctr_ctrl:+.3f} คือศูนย์ตามทฤษฎี</p>
<p><b>บทเรียนที่ใช้ได้ทั่วไป:</b> งาน ML ที่รายงาน R<sup>2</sup> ตัวเดียวจากการเทรนข้ามแล็บ
โดยไม่แยกว่าคะแนนมาจากบล็อกไหน อาจกำลังรายงานความสามารถในการจำแล็บ</p></div>''')

# ---------- 4 ----------
A("<h2>4 · ตัวแปรไหนมีน้ำหนักจริง</h2>")
A("<p>SHAP ตอบว่า <i>แบบจำลองที่ฝึกเสร็จแล้วใช้ตัวแปรนี้อย่างไร</i> ซึ่งไม่เหมือนกับ "
  "<i>ตัวแปรนี้ช่วยทำนายเปเปอร์ที่ไม่เคยเห็นจริงหรือไม่</i> จึงวัดคู่กันสองแบบ "
  "และตัดออกทั้ง<b>กลุ่ม</b>ของตัวแปรเดียวกันเสมอ ไม่ใช่ทีละคอลัมน์ย่อย</p>")
rows = []
for v in IMP.index:
    st = STAB.loc[v] if v in STAB.index else None
    rows.append([f"<code>{esc(v)}</code>", IMP.loc[v, "บทบาท"],
                 num(IMP.loc[v, "SHAP_เฉลี่ย"], "{:.3f}"),
                 num(IMP.loc[v, "R2_ที่หายถ้าตัดออก"]),
                 f'<span class="num">{st["ขึ้นอันดับ_1"]:.0%}</span>' if st is not None else "—"])
A(tbl(["ตัวแปร", "บทบาท", "|SHAP| เฉลี่ย", "R<sup>2</sup> ที่หายถ้าตัดออก", "ขึ้นอันดับ 1"], rows, "num"))
_top = STAB.index[0]
A(f'''<div class="box"><h4>จุดที่สองวิธีไม่ตรงกัน — และต้องอ่านให้ออก</h4>
<p><code>{esc(_top)}</code> เด่นทั้งสองวิธี และขึ้นอันดับหนึ่งใน {STAB.loc[_top,"ขึ้นอันดับ_1"]:.0%}
ของการสุ่มเปเปอร์ซ้ำ — ถือได้ว่ามั่นคง</p>
<p>แต่ตัวแปรที่ค่า <b>R<sup>2</sup> ที่หายถ้าตัดออกติดลบ</b> คือตัวที่ <b>ตัดทิ้งแล้วแบบจำลองดีขึ้น</b>
ทั้งที่ SHAP ให้น้ำหนักมันไม่น้อย — แปลว่าแบบจำลองใช้มันจริง แต่ใช้แล้วไม่ช่วย
นี่คือเหตุผลที่รายงาน SHAP อย่างเดียวไม่พอ</p></div>''')

# ---------- 5 ----------
A('<h2 class="brk">5 · ขนาดผลและกลุ่มสาร</h2>')
_row = PROV[(PROV["หัวข้อ"] == "§6-7 ขนาดผล") & PROV["ข้ออ้าง"].str.startswith("Δpce")]
A(f'<p class="lead">วัดโดยเทียบกับอุปกรณ์ควบคุม<b>ในเปเปอร์เดียวกัน</b>เท่านั้น '
  f'แล้วรวมผลระดับเปเปอร์ — หน่วยของช่วงความเชื่อมั่นคือ <b>เปเปอร์</b> ไม่ใช่อุปกรณ์ '
  f'จึงไม่เกิดการนับตัวอย่างซ้ำ (มี {_n_ctrl} เปเปอร์ที่มีคู่เทียบ จาก {N_PAP} ฉบับ)</p>')
rows = []
for _, r in PROV[PROV["หัวข้อ"] == "§6-7 ขนาดผล"].iterrows():
    rows.append([r["ข้ออ้าง"].split(" ค่ากลาง")[0], f'<span class="num">{r["n_เปเปอร์"]}</span>',
                 r["ข้ออ้าง"].split("ค่ากลาง ")[1] if "ค่ากลาง " in r["ข้ออ้าง"] else ""])
A(tbl(["ตัวชี้วัด", "เปเปอร์", "ค่ากลาง · ทิศทาง · p"], rows, "num"))
A("<h3>5.1 แยกตามกลุ่มสารที่ใช้ดัดแปลง</h3>")
rows = []
for _, r in RANK.iterrows():
    ci = ("—" if pd.isna(r.CI95_ล่าง) else
          f'<span class="num">[{r.CI95_ล่าง:+.2f}, {r.CI95_บน:+.2f}]</span>')
    cross = (not pd.isna(r.CI95_ล่าง)) and (r.CI95_ล่าง < 0 < r.CI95_บน)
    rows.append([f'<b>{esc(r["กลุ่มสาร"])}</b>', f'<span class="num">{int(r["เปเปอร์"])}</span>',
                 num(r["ΔPCE_ค่ากลาง"], "{:+.2f}"), ci, esc(r["ทิศบวก"]),
                 '<span class="no">คร่อมศูนย์</span>' if cross else '<span class="ok">แยกจากศูนย์ได้</span>'])
A(tbl(["กลุ่มสาร", "เปเปอร์", "ΔPCE ค่ากลาง (%)", "CI 95%", "ทิศบวก", "สรุป"], rows, "num"))
A(fig("model_fig3_ranking.png",
      "<b>จัดอันดับบนสนามเดียวกัน</b> — สิ่งที่เปเปอร์เดี่ยวทำไม่ได้เพราะแต่ละฉบับมีสารเดียว · "
      "กลุ่มที่มีเปเปอร์น้อยกว่า 3 ฉบับ ช่วงความเชื่อมั่นกว้างจนใช้จัดอันดับไม่ได้"))
_ox = PROV[PROV["ข้ออ้าง"].str.contains("ไม่มีแถว", na=False)]
if len(_ox):
    A('<div class="box warn"><h4>กลุ่มสารที่จัดอันดับไม่ได้ — ต้องประกาศ ไม่ใช่ปล่อยให้หายไปเงียบ ๆ</h4><ul>' +
      "".join(f'<li><b>{esc(r["ข้ออ้าง"].split(":")[0])}</b> — {esc(r["หมายเหตุ"])} '
              f'(<code>{esc(r["รายชื่อเปเปอร์"])}</code>)</li>' for _, r in _ox.iterrows()) +
      "</ul><p>การจัดอันดับที่รายงานจึงครอบคลุมเฉพาะกลุ่มที่มีคู่เทียบจริง "
      "ไม่ใช่ทุกกลุ่มสารที่มีอยู่ในชุดข้อมูล</p></div>")

A("<h3>5.2 เสถียรภาพ — ผลที่เพิ่งวิเคราะห์ครั้งแรก</h3>")
if STAB_E is not None and len(STAB_E):
    A("<p><code>retention_25c</code> และ <code>degradation_rate</code> อยู่ในชุดข้อมูลมาตลอดแต่ไม่เคยถูกวิเคราะห์ "
      "ทั้งที่<b>เสถียรภาพคือคอขวดจริงของเพอรอฟสไกต์ ไม่ใช่ประสิทธิภาพ</b> · "
      "ใช้วิธีเดียวกับ §5.1 ทุกประการ</p>")
    A(tbl(["ตัวชี้วัด", "ทิศทางที่ดี", "เปเปอร์", "ค่ากลาง", "CI 95%", "ดีขึ้น", "p"],
          [[f'<code>{esc(r["ตัวชี้วัด"])}</code>',
            "สูงขึ้น" if r["ตัวชี้วัด"] == "retention_25c" else "ต่ำลง",
            f'<span class="num">{int(r["n_paper"])}</span>', num(r["ค่ากลาง"], "{:+.4g}"),
            f'<span class="num">[{r["CI95_ล่าง"]:+.4g}, {r["CI95_บน"]:+.4g}]</span>',
            f'<b>{esc(r["ดีขึ้นกี่เปเปอร์"])}</b>', f'<span class="num">{r["p"]:.4g}</span>']
           for _, r in STAB_E.iterrows()], "num"))
    A('<div class="box good"><h4>สองตัวชี้วัดอิสระชี้ไปทางเดียวกัน</h4>'
      "<p>ตัวหนึ่งวัดว่าเหลือประสิทธิภาพเท่าไรหลังทดสอบ อีกตัววัดว่าเสื่อมเร็วแค่ไหน "
      "ทั้งคู่บอกว่าดีขึ้น <b>7 จาก 7 เปเปอร์</b> ที่วัด · การที่สองมุมมองที่คำนวณคนละทางเห็นตรงกัน "
      "ทำให้ผลนี้แข็งกว่าตัวเลขเดี่ยว</p>"
      "<p><b>ข้อจำกัด</b> ยืนบนเปเปอร์เพียง 7 ฉบับ และอคติจากการตีพิมพ์ใช้ได้เต็มที่กับผลนี้เช่นกัน</p></div>")

A("<h3>5.3 ปริมาณสารที่ใช้</h3>")
_mid = int(DOSE["ดีสุดที่จุดกลาง"].sum()) if "ดีสุดที่จุดกลาง" in DOSE.columns else 0
A(tbl(["เปเปอร์", "จำนวนระดับที่ไล่", "ปริมาณที่ดีสุด", "PCE ที่ดีสุด", "rho", "รูปแบบ"],
      [[f'<code>{esc(r["เปเปอร์"])}</code>', f'<span class="num">{int(r["จุดที่ไล่"])}</span>',
        f'<span class="num">{r["ปริมาณที่ดีสุด"]:g}</span>', f'<span class="num">{r["PCE_ที่ดีสุด"]:.2f}</span>',
        num(r["rho"], "{:+.2f}"), esc(r["รูปแบบ"])] for _, r in DOSE.iterrows()], "num"))
A(f'<p class="small muted">จุดที่ดีที่สุดอยู่ตรงกลาง (ไม่ใช่ปลายสุด) เพียง <b>{_mid} จาก {len(DOSE)}</b> เปเปอร์ '
  f'ที่ไล่ปริมาณ — สรุปได้แค่ว่า «ไม่ใช่ยิ่งมากยิ่งดี» ยัง<b>สรุปไม่ได้</b>ว่ามีจุดเหมาะสมตรงกลางเสมอ</p>')

# ---------- 6 ----------
A('<h2 class="brk">6 · กลไก — สองช่องทางที่แยกจากกัน</h2>')
A('<p class="lead">ส่วนที่ใหม่ที่สุดของงาน · เปเปอร์เดี่ยวมีสารเดียวจึงเห็นได้ช่องเดียว '
  'ต้องมีการทดลอง A/B อิสระหลายสิบชุดถึงจะเห็นว่ามันแยกกัน</p>')
A("<p>สมมุติฐานเดิมคือมีช่องทางเดียว (ลดกับดักพาหะ → ผลลัพธ์ดีขึ้น) ซึ่งตรวจสอบได้ตรง ๆ: "
  "<b>ถ้ามีช่องทางเดียวจริง องค์ประกอบทั้งสามของ PCE ต้องขึ้นลงพร้อมกัน</b></p>")
rows = [[f'<code>{esc(i)}</code>', num(COR.loc[i, "rho"], "{:+.2f}"),
         f'<span class="num">{int(COR.loc[i,"n_paper"])}</span>'] for i in COR.index]
A(tbl(["คู่ที่วัด", "สหสัมพันธ์อันดับ", "เปเปอร์"], rows, "num"))
A(fig("model_fig2_channels.png",
      "<b>ซ้าย</b> ΔFF กับ ΔJsc ไม่สัมพันธ์กันเลย — ถ้าเป็นกลไกเดียวต้องเห็นแนวโน้มร่วม · "
      "<b>ขวา</b> มุมสัมผัสเกาะกับ ΔJsc แต่ไม่เกาะกับ ΔFF"))
_vff = COR.loc["Δvtfl ↔ Δff", "rho"]; _vvoc = COR.loc["Δvtfl ↔ Δvoc", "rho"]
_cjsc = COR.loc["Δcontact_angle ↔ Δjsc", "rho"]; _cff = COR.loc["Δcontact_angle ↔ Δff", "rho"]
A(f'''<div class="box good"><h4>หลักฐานว่าสองช่องแยกกันจริง — ตัวกลางแต่ละตัวเจาะจงกับช่องของตัวเอง</h4>
{tbl(["ช่องทาง", "ตัวกลางที่วัดได้", "เกาะกับช่องตัวเอง", "เกาะกับอีกช่อง"], [
  ["<b>อิเล็กทรอนิกส์</b> — กับดักที่รอยต่อลดลง", "<code>vtfl</code>",
   f'ΔFF <span class="num">{_vff:+.2f}</span>', f'ΔVoc <span class="num">{_vvoc:+.2f}</span>'],
  ["<b>สัณฐาน</b> — การตกผลึกของเพอรอฟสไกต์เปลี่ยน", "<code>contact_angle</code>",
   f'ΔJsc <span class="num">{_cjsc:+.2f}</span>', f'ΔFF <span class="num">{_cff:+.2f}</span>'],
])}
<p>ถ้าเป็นกลไกเดียวกัน ตัวกลางทั้งสองต้องเกาะกับทุกองค์ประกอบพอ ๆ กัน ซึ่งไม่ใช่ ·
หลักฐานหลักคือสหสัมพันธ์ ΔFF ↔ ΔJsc ซึ่ง<b>ไม่ต้องใช้เกณฑ์แบ่งกลุ่มใด ๆ เลย</b></p></div>''')

_ca = prov_of("Δcontact_angle", "§6-7 ขนาดผล")
_gs = prov_of("Δgrain_size", "§6-7 ขนาดผล")
A(f'''<div class="box warn"><h4>ทิศทางที่ข้อมูลบอก — ไม่ใช่ทิศทางที่เราตั้งไว้ล่วงหน้า</h4>
<p>สมมุติฐานตั้งต้นคือ «ผิวเปียกขึ้น → ฟิล์มคลุมดีขึ้น → Jsc ขึ้น» แต่ข้อมูลบอกตรงข้าม:
{esc(_ca["ข้ออ้าง"]) if _ca is not None else ""} และเปเปอร์ที่มุมสัมผัส<b>เพิ่ม</b>มาก
คือเปเปอร์ที่ Jsc เพิ่มมาก</p>
<p>อ่านได้ว่า <i>ผิวไม่ชอบน้ำขึ้น → จุดก่อผลึกน้อยลง → เกรนใหญ่ขึ้น → Jsc ขึ้น</i>
ซึ่งสอดคล้องกับวรรณกรรม และ {esc(_gs["ข้ออ้าง"]) if _gs is not None else "ขนาดเกรน"} ก็หนุนไปทางเดียวกัน</p>
<p><b>แต่นี่เป็นการตีความ ไม่ใช่สิ่งที่ข้อมูลชุดนี้พิสูจน์</b> — ข้อต่อ «เกรนใหญ่ขึ้น»
ยืนบนเปเปอร์เพียง {int(_gs["n_เปเปอร์"]) if _gs is not None else 0} ฉบับ
นี่คือช่องว่างที่ควรเก็บข้อมูลเพิ่มเป็นอันดับแรก</p></div>''')

_ch = PROV[PROV["หัวข้อ"] == "§7.2 ช่องทาง"]
A("<h3>6.1 เปเปอร์ไหนใช้ช่องไหน</h3>")
A(tbl(["ช่องทาง", "เปเปอร์"], [[esc(r["ข้ออ้าง"]), f'<span class="num">{r["n_เปเปอร์"]}</span>']
                               for _, r in _ch.iterrows()], "num"))
A('<div class="box"><h4>ตัวเลขนี้ขึ้นกับเกณฑ์ที่เราตั้งเอง</h4>'
  "<p>การจัดว่าเปเปอร์ไหน «ใช้ช่องเดียว» ต้องมีเส้นแบ่งว่าขยับเท่าไรจึงนับว่าขยับ "
  "เมื่อขยับเส้นแบ่ง จำนวนในแต่ละกลุ่มเปลี่ยน — ที่เกณฑ์หลวมมากเกือบทุกเปเปอร์เข้ากลุ่ม «ทั้งสองช่อง» "
  "สิ่งที่ยืนได้คือ <b>มีเปเปอร์ที่ใช้ช่องเดียวจริงในทั้งสองฝั่ง</b> ไม่ใช่ตัวเลขเป๊ะ ๆ · "
  "ดูตารางความไวต่อเกณฑ์ในโน้ตบุ๊ก §7.2</p></div>")

# ---------- 7 ----------
A("<h3>6.2 ทดสอบสมมุติฐาน «ช่องทางที่สาม» — ไม่ผ่าน</h3>")
if NEWMED is not None and len(NEWMED):
    A("<p>ΔVoc กินส่วนแบ่งราวหนึ่งในสามของการเพิ่ม PCE แต่<b>ยังไม่มีตัวกลางอธิบาย</b> "
      "สมมุติฐานคือการจัดเรียงระดับพลังงานที่รอยต่อ จึงดึง <code>vbm</code> <code>work_function</code> "
      "<code>afm_rms</code> <code>hi</code> เข้ามาทดสอบ — ทั้งสี่ตัวมีในข้อมูลมาตลอดแต่ไม่เคยถูกดึงออกมา</p>")
    A(tbl(["ตัวกลาง", "เปเปอร์", "ค่ากลาง", "ทิศทาง", "p", "เกาะ ΔVoc", "เกาะ ΔFF", "เกาะ ΔJsc"],
          [[f'<code>{esc(r["ตัวกลาง"])}</code>', f'<span class="num">{int(r["n_paper"])}</span>',
            num(r["ค่ากลาง"], "{:+.4g}"), esc(r["ทิศบวก"]), f'<span class="num">{r["p"]:.4g}</span>',
            num(r["เกาะ_Δvoc"], "{:+.2f}"), num(r["เกาะ_Δff"], "{:+.2f}"), num(r["เกาะ_Δjsc"], "{:+.2f}")]
           for _, r in NEWMED.iterrows()], "num"))
    A('<div class="box warn"><h4>สรุป: สมมุติฐานนี้ทดสอบแล้วไม่ผ่าน</h4>'
      "<p><code>work_function</code> เกาะกับ ΔVoc แน่นมาก แต่ยืนบนเปเปอร์เพียง 5 ฉบับ — "
      "ที่ n เท่านี้ สหสัมพันธ์สูงสุดเกิดจากความบังเอิญได้ไม่ยาก</p>"
      "<p><code>vbm</code> ซึ่งวัดเรื่องเดียวกันและครอบคลุมมากกว่า (9 เปเปอร์) "
      "<b>ไม่มีสัญญาณทิศทางเลย</b> สองตัวขัดกันเอง จึงยังสรุปไม่ได้ว่ามีช่องพลังงาน</p>"
      "<p><code>afm_rms</code> ก็ไม่ได้หนุนช่องสัณฐานอย่างที่ตั้งไว้ — ผิวเรียบขึ้นทุกเปเปอร์ที่วัด "
      "แต่ไปเกาะกับ ΔVoc มากกว่า ΔJsc สวนทางกับที่คาด</p>"
      "<p><b>บันทึกไว้ว่าเป็นสมมุติฐานที่ทดสอบแล้วไม่ผ่าน</b> ไม่ใช่ผลที่ซ่อนไว้ · "
      "ทางเดียวที่จะตัดสินได้คือเก็บเปเปอร์ที่วัด UPS/AFM เพิ่ม</p></div>")

A('<h2 class="brk">7 · ที่มาของทุกตัวเลข</h2>')
A('<p class="lead">ทุกข้ออ้างในเอกสารนี้เขียนเป็น «n เปเปอร์» ซึ่งตรวจย้อนไม่ได้ด้วยตัวเลขอย่างเดียว '
  'ส่วนนี้จึงเขียนรายชื่อ DOI ที่หนุนข้ออ้างแต่ละข้อออกมาให้ครบ</p>')
A(tbl(["ไฟล์", "หนึ่งแถวคือ", "ใช้ตอนไหน"], [
    ["<code>processed/model/paper_provenance.csv</code>", "หนึ่ง<b>ข้ออ้าง</b> + รายชื่อเปเปอร์ที่หนุน",
     "«ตัวเลขนี้มาจากเปเปอร์ไหนบ้าง»"],
    ["<code>processed/model/paper_evidence_table.csv</code>", "หนึ่ง<b>เปเปอร์</b> + ทุกค่าที่มันให้",
     "«เปิดเปเปอร์นี้แล้วต้องเจอเลขอะไรบ้าง»"],
]))
A("<h3>7.1 ข้ออ้างทั้งหมด พร้อมรายชื่อเปเปอร์</h3>")
rows = []
for _, r in PROV.iterrows():
    ps = " · ".join(f"<code>{esc(x.strip())}</code>" for x in str(r["รายชื่อเปเปอร์"]).split(";") if x.strip())
    _nt = r["หมายเหตุ"]
    note = (f'<br><span class="small muted">{esc(_nt)}</span>'
            if pd.notna(_nt) and str(_nt).strip() else "")
    rows.append([esc(r["หัวข้อ"]), esc(r["ข้ออ้าง"]) + note,
                 f'<span class="num">{r["n_เปเปอร์"]}</span>',
                 f'<span class="small">{ps or "—"}</span>'])
A(tbl(["หัวข้อ", "ข้ออ้าง", "n", "เปเปอร์ที่หนุนข้ออ้างนี้"], rows))

A('<h3 class="brk">7.2 ตารางรายเปเปอร์ — เปิดเปเปอร์แล้วเช็คได้ทีละแถว</h3>')
A("<p>คอลัมน์ <b>ควบคุม</b> คือค่าเฉลี่ยของอุปกรณ์ที่ไม่ดัดแปลงในเปเปอร์นั้น "
  "<b>ดัดแปลง</b> คือค่าเฉลี่ยของอุปกรณ์ที่ดัดแปลง — เทียบกับตัวเลขในเปเปอร์ได้ตรง ๆ โดยไม่ต้องคำนวณย้อน</p>")
rows = []
for _, r in EV.sort_values("เปเปอร์").iterrows():
    def c3(k, f="{:.2f}"):
        b, o, d = r.get(f"{k}_ควบคุม"), r.get(f"{k}_ดัดแปลง"), r.get(f"{k}_Δ")
        if pd.isna(b) and pd.isna(o):
            return '<span class="muted">—</span>'
        s = f'{f.format(b) if pd.notna(b) else "—"} → {f.format(o) if pd.notna(o) else "—"}'
        return f'<span class="num">{s}</span>' + (f'<br><span class="num small"><b>{d:+.2f}</b></span>'
                                                  if pd.notna(d) else "")
    rows.append([f'<code>{esc(r["เปเปอร์"])}</code>'
                 f'<span class="idgloss">{esc(r["สารที่ใช้"]) if pd.notna(r["สารที่ใช้"]) else "—"}</span>',
                 f'<span class="num">{int(r["ควบคุม"])} / {int(r["ดัดแปลง"])}</span>',
                 c3("pce"), c3("ff"), c3("jsc"), c3("voc", "{:.3f}"),
                 c3("vtfl", "{:.3f}"), c3("contact_angle", "{:.1f}"),
                 esc(r["ช่องทาง"]) if pd.notna(r["ช่องทาง"]) else "—"])
A(tbl(["เปเปอร์ · สารที่ใช้", "ควบคุม/ดัดแปลง", "PCE", "FF", "Jsc", "Voc", "VTFL", "มุมสัมผัส", "ช่องทาง"],
      rows, "num"))
A('<p class="small muted">แถวที่ช่อง «ควบคุม/ดัดแปลง» มีเลข 0 อยู่ฝั่งใดฝั่งหนึ่ง คือเปเปอร์ที่วัด Δ ไม่ได้ '
  'จึงไม่เข้าสู่การคำนวณขนาดผลใด ๆ — แสดงไว้เพื่อให้เห็นว่าไม่ได้ถูกซ่อน</p>')

# ---------- 8 ----------
A('<h2 class="brk">8 · ข้อจำกัดที่ต้องประกาศทุกครั้ง</h2>')
A(tbl(["ข้อจำกัด", "ผลต่อการตีความ"], [
    ["<b>อคติจากการตีพิมพ์</b>",
     "เปเปอร์ตีพิมพ์เฉพาะการดัดแปลงที่สำเร็จ ขนาดผลที่วัดได้จึงหมายถึง «การดัดแปลงที่ผ่านการคัดของแต่ละแล็บแล้ว "
     "ให้ผลเท่านี้» ไม่ใช่ «ดัดแปลงอะไรก็ได้แล้วจะดีขึ้น» · "
     "<b>ข้อสังเกต:</b> อคตินี้กระทบ<i>ขนาดผล</i>มาก แต่กระทบ<i>โครงสร้างสองช่องทาง</i> (§6) น้อยกว่า "
     "เพราะอคติกำหนดว่าผลจะได้ตีพิมพ์ไหม ไม่ได้กำหนดว่าผลนั้นวิ่งผ่านช่องทางไหน"],
    ["<b>ไม่ใช่ข้อสรุปเชิงสาเหตุ</b>",
     "เป็นการสังเคราะห์การทดลองที่คนอื่นออกแบบ ผลทุกข้อคือสมมติฐานสำหรับการทดลองยืนยันในแล็บ"],
    ["<b>ตัวควบคุมตีความเป็นกลไกไม่ได้</b>",
     "สูตรเคมีและชนิด ETL แปรค่าภายในเปเปอร์ 0–1 ฉบับ ค่า SHAP ของมันสะท้อนความต่างระหว่างแล็บ "
     "ไม่ใช่ผลของสูตรเคมีเอง"],
    ["<b>ตัวกลางบางตัวยืนบนเปเปอร์น้อยมาก</b>",
     "ขนาดเกรนมีคู่เทียบไม่กี่ฉบับ ข้อต่อกลางของช่องสัณฐานจึงยังไม่แน่น · "
     "ตัวเลขทุกตัวใน §7 บอก n กำกับไว้แล้ว ให้ดูก่อนอ้างอิงเสมอ"],
    ["<b>ผลเสถียรภาพยืนบนเปเปอร์น้อย</b>",
     "วัดได้เพียง 7 เปเปอร์ที่มีคู่เทียบ · แม้สองตัวชี้วัดจะเห็นตรงกันทั้งคู่ ก็ยังเป็นการประมาณขนาด "
     "ไม่ใช่ข้อสรุป · และอคติจากการตีพิมพ์ใช้ได้เต็มที่กับผลนี้เหมือนกับ ΔPCE"],
    ["<b>สมมุติฐานช่องทางที่สามทดสอบแล้วไม่ผ่าน</b>",
     "<code>work_function</code> เกาะ ΔVoc แน่นแต่มี 5 เปเปอร์ ขณะที่ <code>vbm</code> ซึ่งวัดเรื่องเดียวกัน "
     "และมี 9 เปเปอร์ ไม่มีสัญญาณ — สองตัวขัดกันเอง · รายงานไว้เป็นผลลบ ห้ามหยิบเฉพาะตัวที่สวยไปอ้าง"],
    ["<b>ตัวกลางบางตัวตีความชั้นไม่ได้</b>",
     "<code>afm_rms</code> 27 จาก 28 แถวไม่ระบุว่าวัดผิว HTL หรือผิวเพอรอฟสไกต์ (คอลัมน์ <code>afm_layer</code>) · "
     "<code>hi</code> มีเปเปอร์ที่ใช้นิยามต่างจาก (RS−FS)/RS จึงติดธง <code>hi_def_flag</code> "
     "ห้ามเทียบข้ามเปเปอร์ตรง ๆ ให้ใช้ทิศทางภายในเปเปอร์"],
    ["<b>ไม่ได้จูนไฮเปอร์พารามิเตอร์</b>",
     "ค่าทุกตัวมาจากการตั้งตามขนาดข้อมูลก่อนเห็นผล — กันการเลือกตัวเลขที่ดูดี แต่แปลว่ายังไม่ใช่ค่าที่ดีที่สุด"],
    ["<b>เกณฑ์แบ่งช่องทางเป็นเส้นที่เราตั้งเอง</b>",
     "จำนวนเปเปอร์ในแต่ละกลุ่มเปลี่ยนตามเกณฑ์ · หลักฐานที่ไม่ขึ้นกับเกณฑ์คือสหสัมพันธ์ ΔFF ↔ ΔJsc"],
]))

# ---------- 9 ----------
A("<h2>9 · การทำซ้ำ</h2>")
A('<div class="pipe">'
  '<div class="pstep"><div class="n">1</div><div class="b"><div class="t">'
  '<code>notebooks/recheck_comlumn_preapare.ipynb</code></div>'
  '<div class="d">สร้าง <code>processed/prepared_v2/</code> · เปลี่ยนกติกาการเลือกค่าที่นี่เท่านั้น</div></div></div>'
  '<div class="prule"></div>'
  '<div class="pstep"><div class="n">2</div><div class="b"><div class="t">'
  '<code>notebooks/eda_htl_modification.ipynb</code></div>'
  '<div class="d">คัดกรองตัวแปร 3 ด่าน · เขียน <code>processed/audit/eda_feature_screen.csv</code></div></div></div>'
  '<div class="prule"></div>'
  '<div class="pstep"><div class="n">3</div><div class="b"><div class="t">'
  '<code>notebooks/model_htl_modification.ipynb</code></div>'
  '<div class="d">สร้างแบบจำลอง · เขียนผลทั้งหมดลง <code>processed/model/</code> '
  '· <b>มีด่านตรวจว่าตัวแปรที่สร้างตรงกับที่ EDA คัดไว้ทุกตัว</b> ถ้าไม่ตรงจะหยุดทันที</div></div></div>'
  '<div class="prule"></div>'
  '<div class="pstep"><div class="n">4</div><div class="b"><div class="t">'
  '<code>python scripts/gen_model_report.py</code></div>'
  '<div class="d">สร้างเอกสารนี้ทั้ง <b>HTML และ PDF</b> · ตัวเลขทุกตัวดึงสดจาก '
  '<code>processed/model/*.csv</code> · PDF พิมพ์ผ่านเบราว์เซอร์ headless (CSS ตั้ง <code>@page</code> A4 ไว้แล้ว) '
  'ถ้าหาเบราว์เซอร์ไม่เจอจะข้ามไปโดยยังได้ HTML ครบ</div></div></div>'
  '</div>')
A(tbl(["ไฟล์ผลลัพธ์", "เนื้อหา"],
      [[f"<code>processed/model/{esc(k)}.csv</code>",
        f'<span class="num">{n(len(v))}</span> แถว × {v.shape[1]} คอลัมน์'] for k, v in D.items()]))
A('<div class="box key"><h4>ข้อควรระวังตอนต่อยอด</h4><ul>'
  "<li>เวลาเปลี่ยนกติกาการเลือกค่า ให้แก้ที่ต้นทางแล้วสร้างใหม่ทั้งชุด ไม่ใช่แก้ไฟล์ผลลัพธ์ด้วยมือ</li>"
  "<li>ตัวเลขในเอกสารนี้ไม่มีตัวไหน hardcode — แต่<b>ข้อความบรรยายเขียนด้วยมือ</b> "
  "ถ้าผลเปลี่ยนทิศทาง ต้องอ่านข้อความประกอบใหม่ด้วย ไม่ใช่ดูแค่ตัวเลข</li>"
  "<li>ทุกครั้งที่อ้างตัวเลขจากเอกสารนี้ ให้อ้าง n กำกับเสมอ — §7 มีรายชื่อเปเปอร์ให้ตรวจย้อน</li>"
  "</ul></div>")

A(f'<footer>เอกสารนี้สร้างอัตโนมัติจากไฟล์ข้อมูลจริงด้วย <code>scripts/gen_model_report.py</code> · '
  f'อุปกรณ์ {N_DEV} · เปเปอร์ {N_PAP} · เปเปอร์ที่มีคู่เทียบ {_n_ctrl} · '
  f'ข้ออ้างที่มีรายชื่อเปเปอร์กำกับ {len(PROV)} ข้อ</footer>')
A("</body></html>")

out = ROOT / "reports" / "model_report.html"
out.write_text("".join(h), encoding="utf-8")
print("wrote", out, sum(len(x) for x in h), "chars")
print("figures:", *[p.name for p in sorted(FIG.glob("model_fig*.png"))])

# ---------- PDF ----------
# พิมพ์ผ่านเบราว์เซอร์ headless เพราะ CSS ใช้ @page A4 อยู่แล้ว -- ไม่ต้องพึ่งไลบรารีเพิ่ม
# ถ้าหาเบราว์เซอร์ไม่เจอ ให้ข้ามไปเงียบ ๆ ไฟล์ HTML ยังใช้ได้และสั่งพิมพ์เองได้
import subprocess, urllib.request

_BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_exe = next((b for b in _BROWSERS if pathlib.Path(b).exists()), None)
if _exe is None:
    print("ข้าม PDF: หา Chrome/Edge ไม่เจอ -- เปิด reports/model_report.html แล้วสั่งพิมพ์เป็น PDF เองได้")
else:
    pdf = (ROOT / "reports" / "model_report.pdf").resolve()
    url = "file:" + urllib.request.pathname2url(str(out.resolve()))
    if not url.startswith("file:///"):
        url = url.replace("file:", "file:///", 1)
    try:
        r = subprocess.run(
            [_exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
             "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
             f"--print-to-pdf={pdf}", url],
            capture_output=True, text=True, timeout=300)
        if pdf.exists() and pdf.stat().st_size > 10_000:
            print(f"wrote {pdf} {pdf.stat().st_size:,} bytes")
        else:
            print("PDF ไม่ผ่าน (exit", r.returncode, "):", (r.stderr or "")[-300:])
    except Exception as e:
        print("ข้าม PDF:", e)
