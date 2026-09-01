# -*- coding: utf-8 -*-
"""สร้าง reports/model_flow.html (+ .pdf) -- อธิบายขั้นสร้างแบบจำลองด้วยแผนผัง

เอกสารนี้เป็น "ฉบับอธิบาย" ของ model_report.html -- เล่าตั้งแต่ศูนย์ว่าทำไมต้องทำแบบนี้
ตัวเลขทุกตัวดึงสดจาก processed/model/*.csv และ prepared_v2/ เหมือนกัน ไม่ hardcode
แผนผังวาดเป็น inline SVG จึงคมทั้งบนจอและใน PDF และไม่ต้องพึ่งไฟล์รูปเพิ่ม
"""
import pathlib, html, subprocess, urllib.request
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
tt["mod"] = tt.htl_mod_kind.notna()
N_DEV, N_PAP = len(tt), tt.paper.nunique()

MIRROR = D["model_block_mirror"].set_index("บล็อก")
COR = D["model_channel_correlations"].set_index("คู่")
PROV = D["paper_provenance"]
EV = D["paper_evidence_table"]
RANK = D["model_class_ranking"]

N_CTRL_PAP = int(PROV.loc[PROV["ข้ออ้าง"].str.contains("มีทั้งอุปกรณ์ควบคุม"), "n_เปเปอร์"].iloc[0])
_pce_claim = PROV[PROV["ข้ออ้าง"].str.startswith("ΔPCE")].iloc[0]["ข้ออ้าง"]

# ตัวอย่างที่ใช้เดินเรื่องทั้งเอกสาร -- เปเปอร์ที่ขยับทั้งสองช่องทาง เห็นภาพครบ
EXP = EV[EV["เปเปอร์"] == "10.1016j.jechem.2021.11.029"].iloc[0]

_sz = tt.groupby("paper").size()
_s = tt.dropna(subset=["pce"])
SD_TOT = _s.pce.std()
SD_WITHIN = _s.groupby("paper").pce.transform(lambda x: x - x.mean()).std()
SD_BETWEEN = _s.groupby("paper").pce.mean().std()

_have = {f.name for f in font_manager.fontManager.ttflist}
FONT = next((f for f in ["Leelawadee UI", "Sarabun", "Thonburi", "Tahoma", "DejaVu Sans"] if f in _have),
            "DejaVu Sans")
C1, C2, C3, C4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
TINT = "#f6f5ef"
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


def tbl(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


# ═════════════════════════════════ ตัวช่วยวาด SVG ═════════════════════════════════
SVG_FONT = f'{FONT}, "Leelawadee UI", sans-serif'


def svg(w, hgt, body, cap=""):
    """ห่อ SVG ให้ย่อตามความกว้างหน้ากระดาษ + คำบรรยายใต้ภาพ"""
    s = (f'<svg viewBox="0 0 {w} {hgt}" width="100%" style="max-width:{w}px;display:block;margin:0 auto" '
         f'xmlns="http://www.w3.org/2000/svg" font-family=\'{SVG_FONT}\'>'
         f'<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK2}"/></marker>'
         f'<marker id="arL" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{BASE}"/></marker></defs>'
         f'{body}</svg>')
    c = f'<figcaption>{cap}</figcaption>' if cap else ""
    return f'<figure class="avoid">{s}{c}</figure>'


def box(x, y, w, hgt, title, sub="", fill="#ffffff", stroke=BASE, tcol=INK, r=7, fs=12.5, sfs=10.5):
    o = (f'<rect x="{x}" y="{y}" width="{w}" height="{hgt}" rx="{r}" fill="{fill}" '
         f'stroke="{stroke}" stroke-width="1.3"/>')
    if sub:
        o += (f'<text x="{x+w/2}" y="{y+hgt/2-4}" text-anchor="middle" font-size="{fs}" '
              f'font-weight="700" fill="{tcol}">{title}</text>'
              f'<text x="{x+w/2}" y="{y+hgt/2+13}" text-anchor="middle" font-size="{sfs}" '
              f'fill="{MUTED}">{sub}</text>')
    else:
        o += (f'<text x="{x+w/2}" y="{y+hgt/2+4.5}" text-anchor="middle" font-size="{fs}" '
              f'font-weight="700" fill="{tcol}">{title}</text>')
    return o


def arrow(x1, y1, x2, y2, col=INK2, dash="", light=False):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="1.6"{d} '
            f'marker-end="url(#{"arL" if light else "ar"})"/>')


def txt(x, y, s, fs=11, col=INK2, anchor="middle", weight="400", style=""):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{fs}" fill="{col}" '
            f'font-weight="{weight}" font-style="{style or "normal"}">{s}</text>')


# ═════════════════════════════════ รูป matplotlib ═════════════════════════════════
# รูป: ความแปรปรวนอยู่ระหว่างเปเปอร์ ไม่ใช่ภายในเปเปอร์
order = _s.groupby("paper").pce.mean().sort_values().index
f, ax = plt.subplots(figsize=(9.4, 4.2))
for i, p in enumerate(order):
    g = _s[_s.paper == p]
    ax.plot([i] * len(g), g.pce, "o", ms=5, color=C1, alpha=.75, markeredgecolor="white", mew=.8)
    ax.plot([i - .32, i + .32], [g.pce.mean()] * 2, "-", color=INK, lw=1.6)
ax.axhline(_s.pce.mean(), color=C2, lw=1.4, ls="--")
ax.text(len(order) - .5, _s.pce.mean() + .35, "ค่ากลางรวมทุกเปเปอร์", fontsize=8.6, color=C2, ha="right")
ax.set_xticks([]); ax.set_xlabel(f"เปเปอร์ {N_PAP} ฉบับ (เรียงตามค่ากลางของตัวเอง)")
ax.set_ylabel("PCE (%)")
ax.set_title("จุด = อุปกรณ์ · ขีดดำ = ค่ากลางของเปเปอร์นั้น\n"
             "ระยะห่างระหว่างขีดดำ (ระหว่างแล็บ) กว้างกว่าการกระจายของจุดรอบขีดตัวเอง (ภายในแล็บ) มาก")
plt.tight_layout(); plt.savefig(FIG / "flow_fig1_variance.png"); plt.close()

# รูป: ตัวอย่างเปเปอร์เดียว -- ควบคุม vs ดัดแปลง 4 ตัวชี้วัด
f, axes = plt.subplots(1, 4, figsize=(9.6, 3.1))
# VTFL ยิ่งต่ำยิ่งดี (กับดักน้อยลง) -- ทิศทางที่ "ดี" จึงไม่เหมือนอีกสามตัว ต้องระบุแยก
pairs = [("PCE (%)", "pce", +1), ("FF (%)", "ff", +1),
         ("Jsc (mA cm⁻²)", "jsc", +1), ("VTFL (V) · ยิ่งต่ำยิ่งดี", "vtfl", -1)]
for ax, (lab, k, good_dir) in zip(axes, pairs):
    b, o = EXP[f"{k}_ควบคุม"], EXP[f"{k}_ดัดแปลง"]
    better = (o - b) * good_dir > 0
    ax.bar([0, 1], [b, o], color=[BASE, C3 if better else C2], width=.58)
    for i, v in enumerate([b, o]):
        ax.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=9, color=INK)
    ax.set_xticks([0, 1], ["ควบคุม", "ดัดแปลง"], fontsize=9)
    ax.set_title(lab, fontsize=9.5)
    ax.set_ylim(0, max(b, o) * 1.28)
    ax.grid(axis="x", visible=False)
f.suptitle(f"ตัวอย่างจริงหนึ่งเปเปอร์ — {EXP['เปเปอร์']} (สาร {EXP['สารที่ใช้']})", fontsize=10.5)
plt.tight_layout(rect=(0, 0, 1, 0.94))       # เว้นที่ให้ suptitle ไม่ให้ทับแกน
plt.savefig(FIG / "flow_fig2_example.png"); plt.close()

# ═════════════════════════════════ HTML ═════════════════════════════════
h = []
A = h.append
A(f'<!doctype html><html lang="th"><head><meta charset="utf-8">'
  f'<title>แผนผังขั้นสร้างแบบจำลอง</title>{CSS}'
  f'<style>figure svg{{border:1px solid var(--line);border-radius:6px;background:#fff;padding:.4em}}</style>'
  f'</head><body>')

A(f'''<section class="cover">
<div class="kicker">ฉบับอธิบาย · อ่านคู่กับ model_report</div>
<h1>ขั้นสร้างแบบจำลอง<br>อธิบายด้วยแผนผัง</h1>
<p class="sub">เอกสารนี้ตอบว่า <b>ทำไม</b>ถึงทำแบบนี้ ไม่ใช่แค่ทำอะไรไปบ้าง<br>
เริ่มจากศูนย์ ไม่ต้องอ่าน <a href="model_report.html">model_report</a> มาก่อน</p>
<div class="box key"><h4>เรื่องทั้งหมดย่อเหลือสามประโยค</h4>
<p><b>หนึ่ง</b> ข้อมูลไม่ได้เป็นแถวอิสระ {N_DEV} แถว แต่เป็น <b>{N_PAP} ก้อน</b> แต่ละก้อนคือหนึ่งเปเปอร์
ที่ทำการทดลองเทียบ «ก่อน–หลังดัดแปลง» ของตัวเอง</p>
<p><b>สอง</b> ความต่างระหว่างก้อนใหญ่กว่าความต่างภายในก้อนมาก
ถ้าถามว่า «อุปกรณ์นี้ได้ PCE เท่าไร» คำตอบเกือบทั้งหมดคือ «แล้วแต่ว่าแล็บไหนทำ» ซึ่งไม่ใช่ความรู้ที่เราต้องการ</p>
<p><b>สาม</b> จึงเปลี่ยนคำถามเป็น «<b>ภายในแล็บเดียวกัน</b> การดัดแปลงทำให้ดีขึ้นเท่าไร»
คำถามนี้ตอบได้ และตรงกับสิ่งที่แล็บอยากรู้จริง</p></div>
<div class="toc"><ol>
<li>ข้อมูลหน้าตาเป็นอย่างไร<span class="d">ทำไมไม่ใช่ตาราง 95 แถวธรรมดา</span></li>
<li>ปัญหาที่บังคับทุกอย่าง<span class="d">ความแปรปรวนอยู่ผิดที่</span></li>
<li>แบ่งข้อมูลอย่างไร<span class="d">ตัดทีละเปเปอร์ ไม่ใช่สุ่ม</span></li>
<li>สองคำถาม สองแบบจำลอง<span class="d">ค่าสัมบูรณ์ กับ ส่วนต่าง</span></li>
<li>ผลที่ได้ และวิธีอ่าน<span class="d">ตารางสองคูณสอง</span></li>
<li>กลไก<span class="d">ผลวิ่งผ่านทางไหน</span></li>
<li>แผนผังทั้งเส้น<span class="d">ไฟล์ไหนสร้างอะไร</span></li>
</ol></div></section>''')

# ───────── 1 ─────────
A("<h2>1 · ข้อมูลหน้าตาเป็นอย่างไร</h2>")
A('<p class="lead">จุดตั้งต้นของทุกอย่าง: ข้อมูลนี้ <b>ไม่ใช่</b> ตาราง 95 แถวที่แต่ละแถวเป็นอิสระต่อกัน</p>')

b = ""
b += txt(430, 24, f"อุปกรณ์ {N_DEV} ตัว ไม่ได้กระจายอิสระ แต่ซ้อนอยู่ในเปเปอร์ {N_PAP} ฉบับ", 13, INK, weight="700")
xs0 = 30
for i, (nm, dev) in enumerate([("เปเปอร์ A", [("ควบคุม", 0), ("ดัดแปลง", 1)]),
                               ("เปเปอร์ B", [("ควบคุม", 0), ("ดัดแปลง", 1), ("ดัดแปลง", 1)]),
                               ("เปเปอร์ C", [("ควบคุม", 0), ("ดัดแปลง", 1), ("ดัดแปลง", 1), ("ดัดแปลง", 1)]),
                               ("เปเปอร์ D", [("ดัดแปลง", 1), ("ดัดแปลง", 1)])]):
    x = xs0 + i * 212
    bw = 190
    bh = 46 + len(dev) * 40
    b += f'<rect x="{x}" y="46" width="{bw}" height="{bh}" rx="9" fill="{TINT}" stroke="{BASE}" stroke-width="1.3"/>'
    b += txt(x + bw / 2, 70, nm, 12.5, INK, weight="700")
    for j, (lab, kind) in enumerate(dev):
        y = 86 + j * 40
        col = C3 if kind else "#ffffff"
        b += box(x + 20, y, bw - 40, 30, lab, fill=col if kind else "#ffffff",
                 stroke=C3 if kind else BASE, tcol="#ffffff" if kind else INK2, r=6, fs=11)
b += txt(30, 300, "เปเปอร์ A–C มีทั้งอุปกรณ์ควบคุมและอุปกรณ์ที่ดัดแปลง จึงวัดผลต่างในตัวเองได้",
         11.5, INK2, anchor="start")
b += txt(30, 320, f"เปเปอร์ D ไม่มีอุปกรณ์ควบคุม จึงเทียบอะไรไม่ได้เลย — แบบนี้มี {N_PAP - N_CTRL_PAP} ฉบับ",
         11.5, C2, anchor="start")
A(svg(880, 334, b, "หนึ่งเปเปอร์ = หนึ่งการทดลอง A/B ที่แล็บนั้นทำเอง · "
                   f"เปเปอร์มีอุปกรณ์ {int(_sz.min())}–{int(_sz.max())} ตัว (ค่ากลาง {int(_sz.median())}) · "
                   f"<b>{N_CTRL_PAP} จาก {N_PAP} ฉบับ</b>มีคู่เทียบครบ"))

A(f'''<div class="box"><h4>ตัวอย่างจริงหนึ่งฉบับ ใช้เดินเรื่องทั้งเอกสาร</h4>
<p><code>{esc(EXP["เปเปอร์"])}</code> เคลือบผิว HTL ด้วย <b>{esc(EXP["สารที่ใช้"])}</b>
ทำอุปกรณ์ควบคุม {int(EXP["ควบคุม"])} ตัว และอุปกรณ์ที่ดัดแปลง {int(EXP["ดัดแปลง"])} ตัว
<b>ในแล็บเดียวกัน วันเดียวกัน สูตรเดียวกัน</b> ต่างกันแค่มีสารเคลือบหรือไม่มี</p></div>''')
A('<figure><img src="figures/flow_fig2_example.png"><figcaption>'
  f'ทุกตัวชี้วัดดีขึ้นพร้อมกัน · PCE {EXP["pce_ควบคุม"]:.2f} → {EXP["pce_ดัดแปลง"]:.2f} '
  f'(<b>{EXP["pce_Δ"]:+.2f}</b>) · VTFL ลดลงแปลว่ากับดักพาหะน้อยลง (ยิ่งต่ำยิ่งดี)'
  '</figcaption></figure>')
A('<p>ผลต่างในกรอบแบบนี้<b>เชื่อได้</b> เพราะทุกอย่างนอกจากสารเคลือบถูกตรึงไว้หมดแล้วโดยตัวแล็บเอง '
  'งานของเราคือรวมการทดลองแบบนี้จากหลายแล็บเข้าด้วยกันอย่างถูกวิธี</p>')

# ───────── 2 ─────────
A('<h2 class="brk">2 · ปัญหาที่บังคับทุกอย่าง</h2>')
A('<p class="lead">ความแปรปรวนของ PCE ส่วนใหญ่ไม่ได้มาจากการดัดแปลง แต่มาจาก «แล็บไหนเป็นคนทำ»</p>')
A('<figure><img src="figures/flow_fig1_variance.png"><figcaption>'
  f'ส่วนเบี่ยงเบนมาตรฐานของ PCE <b>ระหว่างเปเปอร์ {SD_BETWEEN:.2f}</b> เทียบกับ '
  f'<b>ภายในเปเปอร์ {SD_WITHIN:.2f}</b> — ต่างกันราว {SD_BETWEEN/SD_WITHIN:.1f} เท่า'
  '</figcaption></figure>')

b = ""
b += txt(400, 22, "ถ้าเอา 95 อุปกรณ์มากองรวมกันแล้วหาความสัมพันธ์ จะได้อะไร", 13, INK, weight="700")
b += box(40, 46, 200, 62, "สิ่งที่เราอยากได้", "ผลของการดัดแปลง", fill="#eefaf5", stroke="#bfe8d8")
b += box(300, 46, 200, 62, "สิ่งที่ปนมาด้วย", "แล็บไหนเก่งกว่ากัน", fill="#fdf3ee", stroke="#f2cdb9")
b += box(560, 46, 200, 62, "และปนมาอีก", "สูตรเคมีของแล็บนั้น", fill="#fdf3ee", stroke="#f2cdb9")
b += arrow(140, 112, 400, 146)
b += arrow(400, 112, 400, 146)
b += arrow(660, 112, 400, 146)
b += box(250, 150, 300, 50, "ตัวเลขเดียวที่แยกกันไม่ออก", fill=TINT)
b += txt(400, 224, "ยิ่งกล่องสีส้มใหญ่กว่ากล่องสีเขียวมากเท่าไร ตัวเลขที่ได้ยิ่งเป็นเรื่องของแล็บ ไม่ใช่ของกลไก",
         11.5, INK2)
A(svg(800, 240, b, "นี่คือ <b>ตัวกวนระดับเปเปอร์</b> (confounding) — และรูปด้านบนบอกว่ากล่องสีส้มใหญ่กว่าจริง"))

# ───────── 3 ─────────
A("<h2>3 · แบ่งข้อมูลอย่างไร</h2>")
A('<p class="lead">การแบ่งข้อมูลแบบสุ่มทั่วไปใช้ไม่ได้ที่นี่ และเหตุผลตรงไปตรงมามาก</p>')

b = ""
b += txt(230, 22, "แบ่งสุ่ม — ผิด", 13, C2, weight="700")
b += txt(690, 22, "ตัดทีละเปเปอร์ (LeaveOneGroupOut) — ถูก", 13, C3, weight="700")
for k, (ox, ok) in enumerate([(20, False), (480, True)]):
    for i in range(3):
        x = ox + i * 145
        b += f'<rect x="{x}" y="40" width="130" height="112" rx="8" fill="{TINT}" stroke="{BASE}"/>'
        b += txt(x + 65, 60, f"เปเปอร์ {chr(65+i)}", 11, INK, weight="700")
        for j in range(3):
            y = 70 + j * 26
            if ok:
                train = (i != 1)
            else:
                train = not (i == 1 and j == 1) and not (i == 0 and j == 2)
            col = "#dfeafb" if train else "#fbe3d6"
            b += (f'<rect x="{x+14}" y="{y}" width="102" height="20" rx="4" fill="{col}" '
                  f'stroke="{BASE}" stroke-width=".8"/>')
            b += txt(x + 65, y + 14, "ฝึก" if train else "ทดสอบ", 9.5, INK2)
b += txt(230, 176, "อุปกรณ์พี่น้องจากเปเปอร์เดียวกันอยู่ทั้งสองฝั่ง", 11, C2)
b += txt(230, 194, "โมเดลจำสูตรของเปเปอร์นั้นได้ คะแนนสูงแบบหลอก ๆ", 11, C2)
b += txt(690, 176, "ทั้งเปเปอร์ถูกกันออกไปพร้อมกัน", 11, C3)
b += txt(690, 194, "ต้องทำนายแล็บที่ไม่เคยเห็นจริง ๆ", 11, C3)
b += f'<line x1="455" y1="30" x2="455" y2="205" stroke="{BASE}" stroke-dasharray="4 4"/>'
A(svg(920, 214, b, f"ทำซ้ำ {N_PAP} รอบ กันออกทีละเปเปอร์จนครบทุกฉบับ"))

A('<div class="box warn"><h4>ผลข้างเคียงที่ต้องรับมือ</h4>'
  f'<p>แต่ละรอบเหลืออุปกรณ์ทดสอบแค่ {int(_sz.min())}–{int(_sz.max())} ตัว '
  f'({int((_sz==2).sum())} รอบมีแค่ 2 ตัว) การให้คะแนนความแม่นของรอบที่มี 2 ตัวแล้วเอามาเฉลี่ย '
  'จะได้ตัวเลขที่แกว่งจนไม่มีความหมาย</p>'
  '<p><b>วิธีแก้:</b> เก็บคำทำนายของทุกอุปกรณ์จากทุกรอบไว้ก่อน แล้วค่อยคิดคะแนน<b>ครั้งเดียว</b>บนกองรวม</p></div>')

# ───────── 4 ─────────
A('<h2 class="brk">4 · สองคำถาม สองแบบจำลอง</h2>')
A('<p class="lead">โจทย์เดียวกัน ข้อมูลชุดเดียวกัน แต่ถามคนละแบบ แล้วได้ผลคนละเรื่อง</p>')

b = ""
b += box(40, 20, 340, 54, "คำถามที่ 1", "อุปกรณ์นี้ได้ PCE กี่เปอร์เซ็นต์", fill="#fdf3ee", stroke="#f2cdb9")
b += box(460, 20, 340, 54, "คำถามที่ 2", "ดัดแปลงแล้วดีขึ้นกว่าตัวเองเท่าไร", fill="#eefaf5", stroke="#bfe8d8")
b += arrow(210, 78, 210, 108)
b += arrow(630, 78, 630, 108)
b += box(60, 112, 300, 46, "ทำนายค่าดิบ", "PCE = 20.16", fill="#ffffff")
b += box(480, 112, 300, 46, "ลบค่ากลางของเปเปอร์ตัวเองออกก่อน", "PCE − ค่ากลางเปเปอร์", fill="#ffffff")
b += arrow(210, 162, 210, 192)
b += arrow(630, 162, 630, 192)
b += box(40, 196, 340, 74, "ต้องรู้ว่าแล็บนี้ตั้งต้นที่ไหน", "ซึ่งไม่มีตัวแปรไหนบอกได้", fill=TINT)
b += box(460, 196, 340, 74, "ทุกอย่างที่คงที่ทั้งเปเปอร์หายไปหมด",
         "สูตรเคมี · ETL · ความชื้น · ฝีมือคนทำ", fill=TINT)
b += txt(210, 292, "ตอบไม่ได้ด้วยข้อมูลชุดนี้", 12.5, C2, weight="700")
b += txt(630, 292, "ตอบได้ และเป็นสิ่งที่แล็บอยากรู้", 12.5, C3, weight="700")
A(svg(840, 306, b, "การลบค่ากลางของเปเปอร์ออก คือหัวใจของทั้งงาน — "
                   "มันหักล้าง<b>ทุกอย่าง</b>ที่คงที่ภายในเปเปอร์ รวมถึงสิ่งที่เราไม่เคยบันทึกไว้เลย"))

A('<div class="box key"><h4>ทำไมข้อแลกเปลี่ยนนี้ยอมรับได้</h4>'
  '<p>การลบค่ากลางต้องใช้ค่ากลางของเปเปอร์นั้น — ฟังดูเหมือนโกง แต่ในการใช้งานจริง '
  '<b>แล็บมีอุปกรณ์ควบคุมของตัวเองอยู่แล้วเสมอ</b> เขาไม่เคยถามว่า «อุปกรณ์ฉันจะได้กี่เปอร์เซ็นต์» '
  'เขาถามว่า «ถ้าเติมสารนี้ ฉันจะดีขึ้นกว่าเดิมเท่าไร» ซึ่งคือคำถามที่ 2 พอดี</p></div>')

# ───────── 5 ─────────
A("<h2>5 · ผลที่ได้ และวิธีอ่าน</h2>")
A('<p>เอาตัวแปรมาแบ่งเป็นสองกลุ่ม แล้ววัดแยกกันว่าคะแนนมาจากกลุ่มไหน</p>')
A(tbl(["กลุ่มตัวแปร", "มีอะไรบ้าง", "ถ้ากลุ่มนี้ทำนายได้ แปลว่า"], [
    ["<b>ตัวแปรหลัก</b>", "<code>htl_mod_kind</code> <code>htl_dope</code> <code>htl_route</code> — สิ่งที่แล็บ<b>เลือกทำ</b>กับชั้น HTL",
     '<span class="ok">โมเดลเรียนผลของการดัดแปลง</span> — สิ่งที่เราต้องการ'],
    ["<b>ตัวควบคุม</b>", "<code>a_Cs</code> <code>a_FA</code> <code>x_Br</code> <code>etl_family</code> — สูตรเคมีและชนิด ETL",
     '<span class="no">โมเดลเรียนว่าแล็บไหนทำ</span> — สูตรของแต่ละแล็บเกือบไม่ซ้ำกัน จึงเป็นลายนิ้วมือ'],
]))

_am = MIRROR.iloc[:, 0]["ตัวแปรหลักอย่างเดียว"]; _ac = MIRROR.iloc[:, 0]["ตัวควบคุมอย่างเดียว"]
_cm = MIRROR.iloc[:, 1]["ตัวแปรหลักอย่างเดียว"]; _cc = MIRROR.iloc[:, 1]["ตัวควบคุมอย่างเดียว"]
b = ""
b += txt(400, 22, "คะแนนสลับข้างกันพอดีเมื่อเปลี่ยนคำถาม", 13.5, INK, weight="700")
b += txt(268, 58, "คำถามที่ 1", 11.5, INK2, weight="700")
b += txt(268, 74, "ทำนายค่าดิบ", 10, MUTED)
b += txt(560, 58, "คำถามที่ 2", 11.5, INK2, weight="700")
b += txt(560, 74, "ทำนายส่วนต่าง", 10, MUTED)
for j, (lab, v1, v2) in enumerate([("ตัวแปรหลัก", _am, _cm), ("ตัวควบคุม", _ac, _cc)]):
    y = 92 + j * 84
    b += txt(150, y + 46, lab, 11.5, INK2, anchor="end", weight="700")
    for i, v in enumerate([v1, v2]):
        x = 172 + i * 292
        good = v > 0.05
        b += box(x, y, 268, 68, f"{v:+.3f}",
                 "เรียนกลไกได้" if good and lab == "ตัวแปรหลัก" else
                 ("จำแล็บได้" if good else "ทำนายไม่ได้เลย"),
                 fill="#eefaf5" if (good and lab == "ตัวแปรหลัก") else
                      ("#fdf3ee" if good else "#ffffff"),
                 stroke="#bfe8d8" if (good and lab == "ตัวแปรหลัก") else ("#f2cdb9" if good else BASE),
                 fs=19)
b += txt(400, 276, "แนวทแยงสองเส้นนี้คือผลหลักของงานทั้งหมด", 12, INK, weight="700")
A(svg(800, 292, b, f"ตัวเลขคือ R<sup>2</sup> จากการทำนายเปเปอร์ที่ไม่เคยเห็น · "
                   f"ยิ่งใกล้ 1 ยิ่งแม่น · 0 หรือติดลบ = ไม่ดีกว่าการทายค่ากลาง"))

A(f'''<div class="box key"><h4>อ่านตารางนี้อย่างไร</h4>
<p><b>ช่องบนซ้าย ({_am:+.3f})</b> ตัวแปรการดัดแปลงทำนายค่าดิบไม่ได้เลย — สมเหตุผล เพราะรู้ว่าเคลือบสารอะไร
ก็ยังไม่รู้ว่าแล็บนั้นเก่งแค่ไหน</p>
<p><b>ช่องล่างซ้าย ({_ac:+.3f})</b> สูตรเคมีทำนายค่าดิบได้ดีกว่า — <b>แต่ไม่ใช่เพราะสูตรเคมีทำให้ประสิทธิภาพดี</b>
มันแค่บอกใบ้ว่านี่คือเปเปอร์ไหน แล้วเปเปอร์นั้นได้ประมาณเท่าไร นี่คือการจำ ไม่ใช่การเข้าใจ</p>
<p><b>ช่องล่างขวา ({_cc:+.3f})</b> พอลบค่ากลางเปเปอร์ ตัวควบคุมเหลือศูนย์พอดี — ตามที่ทฤษฎีบอกทุกประการ
เพราะมันคงที่ภายในเปเปอร์อยู่แล้ว จึงไม่เหลือข้อมูลอะไรเลย</p>
<p><b>ช่องบนขวา ({_cm:+.3f})</b> คือช่องเดียวที่เป็นความรู้จริง</p>
<p class="small muted">บทเรียนที่ใช้ได้กับงานอื่น: งานที่รายงานคะแนนเดียวจากการเทรนข้ามแล็บ
โดยไม่แยกดูว่าคะแนนมาจากกลุ่มตัวแปรไหน อาจกำลังรายงานความสามารถในการจำแล็บ</p></div>''')

# ───────── 6 ─────────
A('<h2 class="brk">6 · กลไก — ผลวิ่งผ่านทางไหน</h2>')
A('<p class="lead">รู้แล้วว่าดัดแปลงแล้วดีขึ้น คำถามถัดไปคือดีขึ้น<b>เพราะอะไร</b></p>')
A(f'<p>PCE ไม่ใช่ตัวเลขก้อนเดียว แต่เป็นผลคูณของสามส่วน '
  f'<b>PCE = Voc × Jsc × FF ÷ 100</b> การดูว่าส่วนไหนขยับ บอกกลไกได้</p>')

_ffjsc = COR.loc["Δff ↔ Δjsc", "rho"]
_vff = COR.loc["Δvtfl ↔ Δff", "rho"]; _vvoc = COR.loc["Δvtfl ↔ Δvoc", "rho"]
_cj = COR.loc["Δcontact_angle ↔ Δjsc", "rho"]; _cf = COR.loc["Δcontact_angle ↔ Δff", "rho"]
b = ""
b += box(300, 16, 260, 48, "ดัดแปลงผิว HTL", fill=TINT)
b += arrow(370, 68, 220, 104)
b += arrow(490, 68, 640, 104)
b += box(60, 108, 320, 52, "ผิวเปลี่ยนสมบัติทางไฟฟ้า", "กับดักพาหะที่รอยต่อลดลง",
         fill="#eef4fd", stroke="#c4d9f5")
b += box(480, 108, 320, 52, "ผิวเปลี่ยนสมบัติทางกายภาพ", "การตกผลึกของฟิล์มเปลี่ยน",
         fill="#fff7e6", stroke="#f0dca8")
b += arrow(220, 164, 220, 196); b += arrow(640, 164, 640, 196)
b += box(60, 200, 320, 46, "วัดด้วย VTFL", fill="#ffffff", stroke="#c4d9f5")
b += box(480, 200, 320, 46, "วัดด้วยมุมสัมผัส", fill="#ffffff", stroke="#f0dca8")
b += arrow(220, 250, 220, 282); b += arrow(640, 250, 640, 282)
b += box(60, 286, 320, 46, "FF ขึ้น", fill="#eef4fd", stroke="#c4d9f5", fs=14)
b += box(480, 286, 320, 46, "Jsc ขึ้น", fill="#fff7e6", stroke="#f0dca8", fs=14)
b += arrow(220, 336, 380, 368); b += arrow(640, 336, 480, 368)
b += box(300, 372, 260, 46, "PCE ขึ้น", fill=TINT, fs=14)
A(svg(860, 430, b, "สองเส้นทางที่<b>เป็นอิสระต่อกัน</b> ไม่ใช่กลไกเดียว"))

A("<h3>6.1 หลักฐานว่าสองทางนี้แยกกันจริง</h3>")
A(tbl(["ถ้าเป็นกลไกเดียว ควรเห็น", "ที่วัดได้จริง", "แปลว่า"], [
    ["FF กับ Jsc ขึ้นลงพร้อมกัน",
     f'สหสัมพันธ์ <b>{_ffjsc:+.2f}</b> (จาก {int(COR.loc["Δff ↔ Δjsc","n_paper"])} เปเปอร์)',
     '<span class="ok">ไม่เกี่ยวกันเลย</span> — คนละกลไก'],
    ["VTFL เกาะกับทุกส่วนพอ ๆ กัน",
     f'เกาะ FF <b>{_vff:+.2f}</b> · เกาะ Voc <b>{_vvoc:+.2f}</b>',
     '<span class="ok">เจาะจงกับ FF</span> เท่านั้น'],
    ["มุมสัมผัสเกาะกับทุกส่วนพอ ๆ กัน",
     f'เกาะ Jsc <b>{_cj:+.2f}</b> · เกาะ FF <b>{_cf:+.2f}</b>',
     '<span class="ok">เจาะจงกับ Jsc</span> เท่านั้น'],
]))
A('<p class="small muted">สหสัมพันธ์อ่านง่าย ๆ คือ +1 หมายถึงขึ้นด้วยกันเสมอ · −1 หมายถึงสวนทางกันเสมอ · '
  '0 หมายถึงไม่เกี่ยวกันเลย · ตัวเลขพวกนี้คำนวณโดยใช้ <b>หนึ่งเปเปอร์เป็นหนึ่งจุด</b> ไม่ใช่หนึ่งอุปกรณ์</p>')
A('<div class="box warn"><h4>ข้อควรระวังที่ต้องอ่านคู่กันเสมอ</h4>'
  '<p>ทิศทางของช่องกายภาพ<b>ตรงข้ามกับที่คาดไว้ตอนแรก</b> — ข้อมูลบอกว่าผิวที่ '
  '<b>ไม่ชอบน้ำมากขึ้น</b> ให้ Jsc ดีขึ้น ไม่ใช่ผิวที่เปียกง่ายขึ้น '
  'อธิบายได้ว่าผิวไม่ชอบน้ำทำให้จุดก่อผลึกน้อยลง เกรนจึงโตใหญ่ขึ้น '
  'แต่นี่คือ<b>การตีความ</b> ข้อต่อ «เกรนใหญ่ขึ้น» ยังยืนบนเปเปอร์น้อยมาก</p></div>')

# ───────── 7 ─────────
A('<h2 class="brk">7 · แผนผังทั้งเส้น — ไฟล์ไหนสร้างอะไร</h2>')
b = ""
steps = [
    ("Raw Data/*.xlsx", f"ไฟล์ Excel {N_DEV} ไฟล์ · หนึ่งไฟล์หนึ่งอุปกรณ์", TINT),
    ("explore_reschema · column_prepare", "แตกชีตเป็นตาราง · ทำความสะอาด · แปลงค่าเป็นตัวเลข", "#ffffff"),
    ("recheck_comlumn_preapare", "ตรวจซ้ำ · สร้าง target_table (ไฟล์เดียวที่ขั้นโมเดลอ่าน)", "#ffffff"),
    ("eda_htl_modification", "คัดกรองว่าตัวแปรไหนใช้ได้ · ตัวไหนแยกจากตัวตนเปเปอร์ไม่ออก", "#ffffff"),
    ("model_htl_modification", "สร้างแบบจำลอง · วัดผล · เขียนผลลง processed/model/", "#eefaf5"),
    ("gen_model_report · gen_model_flow", "สร้างรายงาน HTML และ PDF จากผลที่เขียนไว้", "#eef4fd"),
]
for i, (nm, d, col) in enumerate(steps):
    y = 16 + i * 74
    b += box(40, y, 760, 54, nm, d, fill=col, fs=12.5, sfs=10.5)
    if i < len(steps) - 1:
        b += arrow(420, y + 56, 420, y + 72)
A(svg(840, 16 + len(steps) * 74, b,
      "แก้กติกาการเลือกค่าต้องแก้ที่ขั้น <code>recheck</code> แล้วสร้างใหม่ทั้งเส้น "
      "<b>ห้ามแก้ไฟล์ผลลัพธ์ด้วยมือ</b>"))

A("<h3>7.1 ผลลัพธ์ที่ได้ และใช้ทำอะไร</h3>")
A(tbl(["ไฟล์", "ใช้ตอนไหน"], [
    ["<code>processed/model/*.csv</code>", "ตัวเลขดิบทั้งหมด · รายงานทุกฉบับอ่านจากที่นี่"],
    ["<code>paper_provenance.csv</code>", "ข้ออ้างแต่ละข้อมาจากเปเปอร์ไหนบ้าง — ใช้ตรวจย้อนด้วยมือ"],
    ["<code>paper_evidence_table.csv</code>", "รายเปเปอร์: ค่าควบคุม → ค่าหลังดัดแปลง → ผลต่าง"],
    ['<a href="model_report.html"><code>reports/model_report.html</code></a>', "รายงานผลฉบับเต็ม พร้อมข้อจำกัด"],
    ["<code>reports/model_flow.html</code>", "เอกสารนี้ — ฉบับอธิบายว่าทำไมถึงทำแบบนี้"],
]))

A(f'''<div class="box good"><h4>สรุปสิ่งที่ได้จริง</h4>
<p><b>ขนาดผล</b> {esc(_pce_claim)} — วัดจากเปเปอร์ที่มีคู่เทียบ {N_CTRL_PAP} ฉบับ หนึ่งฉบับหนึ่งเสียง</p>
<p><b>กลไก</b> แยกได้ว่ามีอย่างน้อยสองทางที่เป็นอิสระต่อกัน ซึ่งเปเปอร์เดี่ยวมองไม่เห็น
เพราะแต่ละฉบับมีสารเดียว</p>
<p><b>ระเบียบวิธี</b> แสดงให้เห็นตรง ๆ ว่าคะแนนที่ดูดีจากการเทรนข้ามแล็บ มาจากการจำแล็บ</p>
<p class="small">ทุกข้อคือ<b>สมมติฐานสำหรับการทดลองยืนยันในแล็บ</b> ไม่ใช่ข้อสรุปเชิงสาเหตุ ·
และต้องประกาศ<b>อคติจากการตีพิมพ์</b>เสมอ เพราะไม่มีใครตีพิมพ์การดัดแปลงที่ล้มเหลว</p></div>''')

A(f'<footer>เอกสารนี้สร้างอัตโนมัติด้วย <code>scripts/gen_model_flow.py</code> · '
  f'ตัวเลขดึงสดจาก <code>processed/model/</code> และ <code>processed/prepared_v2/</code> · '
  f'อุปกรณ์ {N_DEV} · เปเปอร์ {N_PAP} · เปเปอร์ที่มีคู่เทียบ {N_CTRL_PAP}</footer>')
A("</body></html>")

out = ROOT / "reports" / "model_flow.html"
out.write_text("".join(h), encoding="utf-8")
print("wrote", out, sum(len(x) for x in h), "chars")

_BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_exe = next((b for b in _BROWSERS if pathlib.Path(b).exists()), None)
if _exe is None:
    print("ข้าม PDF: หา Chrome/Edge ไม่เจอ")
else:
    pdf = (ROOT / "reports" / "model_flow.pdf").resolve()
    url = "file:" + urllib.request.pathname2url(str(out.resolve()))
    if not url.startswith("file:///"):
        url = url.replace("file:", "file:///", 1)
    r = subprocess.run([_exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
                        f"--print-to-pdf={pdf}", url], capture_output=True, text=True, timeout=300)
    if pdf.exists() and pdf.stat().st_size > 10_000:
        print(f"wrote {pdf} {pdf.stat().st_size:,} bytes")
    else:
        print("PDF ไม่ผ่าน:", (r.stderr or "")[-300:])
