#!/usr/bin/env python3
"""recheck ของ evidence_proof.ipynb และหน้า retention — รันซ้ำได้

    ../../.venv/bin/python analysis.py

อ่าน processed/prepared_v2/ แล้วเขียน figures/*.png + summary_tables.xlsx ลงโฟลเดอร์นี้
ไม่แก้ไฟล์ต้นทางใด ๆ
"""
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "processed").exists())
FIG = HERE / "figures"
FIG.mkdir(parents=True, exist_ok=True)

C_OK, C_BAD, C_ALT = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, BASE = "#0b0b0b", "#898781", "#e1e0d9", "#c3c2b7"
plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "figure.facecolor": "white",
    "savefig.facecolor": "white", "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": BASE, "axes.grid": True, "grid.color": GRID, "grid.linewidth": .8,
    "axes.axisbelow": True, "font.size": 9, "axes.titlesize": 10.5})

LOG = {"trap_density", "rs", "rrec"}   # กินหลายอันดับของขนาด -> log10 ก่อนหาสหสัมพันธ์


# ------------------------------------------------------------------ โหลดข้อมูล
def load():
    tgt = pd.read_csv(ROOT / "processed/prepared_v2/target_table.csv")
    dev = pd.read_csv(ROOT / "processed/prepared_v2/devices.csv")
    D = tgt.merge(dev[["device_id", "doi_raw", "sample_no"]], on="device_id", how="left")
    # DOI เดียวกันถูกกรอกสองแบบ (จุด vs ขีดล่าง) -> ยุบเป็นเปเปอร์เดียว
    D["paper"] = D["doi_raw"].str.replace(r"[._-]", "", regex=True).str.lower()
    D = D.drop_duplicates(["paper", "sample_no"], keep="first").reset_index(drop=True)
    D["is_mod"] = D["htl_mod_kind"].notna()
    return D


def corr(df, a, b, mode, use_log=True):
    """สหสัมพันธ์ภายในเปเปอร์ (หักค่าเฉลี่ยเปเปอร์ก่อน) หรือแบบเทกองรวม"""
    s = df.dropna(subset=[a, b]).copy()
    for col, src in (("_a", a), ("_b", b)):
        s[col] = np.log10(s[src].where(s[src] > 0)) if (use_log and src in LOG) else s[src]
    s = s.dropna(subset=["_a", "_b"])
    if mode == "naive":
        if len(s) < 4 or s["_a"].nunique() < 2 or s["_b"].nunique() < 2:
            return np.nan, 0, 0
        return float(np.corrcoef(s["_a"], s["_b"])[0, 1]), len(s), s["paper"].nunique()
    parts = [pd.DataFrame({"x": g["_a"] - g["_a"].mean(), "y": g["_b"] - g["_b"].mean()})
             for _, g in s.groupby("paper")
             if len(g) >= 2 and g["_a"].nunique() > 1 and g["_b"].nunique() > 1]
    if not parts:
        return np.nan, 0, 0
    w = pd.concat(parts)
    return float(np.corrcoef(w["x"], w["y"])[0, 1]), len(w), len(parts)


# ------------------------------------------------------- 1. ΔPCE: max vs median
def delta_table(D):
    P = D.dropna(subset=["pce"])
    g = P.groupby("paper")["is_mod"].agg(["sum", "count"])
    paired = g[(g["sum"] > 0) & (g["sum"] < g["count"])].index
    ctrl = P[P["paper"].isin(paired) & ~P["is_mod"]].groupby("paper")["pce"].median()
    mod = P[P["paper"].isin(paired) & P["is_mod"]].groupby("paper")["pce"]
    T = pd.DataFrame({"ctrl": ctrl, "mod_max": mod.max(), "mod_med": mod.median()}).dropna()
    T["d_max"] = T["mod_max"] - T["ctrl"]
    T["d_med"] = T["mod_med"] - T["ctrl"]
    T["n_mod"] = mod.size()
    return T.sort_values("d_med")


def fig_delta(T):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.6),
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    ys = np.arange(len(T))
    ax1.hlines(ys, T["d_med"], T["d_max"], color=BASE, lw=1.8, zorder=1)
    ax1.scatter(T["d_med"], ys, s=34, color=C_OK, zorder=3, label="median of modified (symmetric)")
    ax1.scatter(T["d_max"], ys, s=34, color=C_BAD, zorder=3, label="max of modified (champion)")
    ax1.axvline(0, color=MUTED, lw=1.2)
    ax1.set_yticks([]), ax1.set_ylim(-1, len(T))
    ax1.set_xlabel("$\\Delta$PCE vs same-paper control (%abs)")
    ax1.set_title(f"One row = one paper (n = {len(T)})", pad=12)
    ax1.legend(frameon=False, fontsize=8.4, loc="lower right")

    bars = [("median\nof modified", T["d_med"].median(), C_OK),
            ("max\nof modified", T["d_max"].median(), C_BAD)]
    ax2.bar(range(2), [v for _, v, _ in bars], color=[c for _, _, c in bars], width=.55)
    for i, (_, v, _) in enumerate(bars):
        ax2.text(i, v + .05, f"{v:+.2f}", ha="center", fontsize=10, color=INK)
    pos_med, pos_max = int((T["d_med"] > 0).sum()), int((T["d_max"] > 0).sum())
    ax2.set_xticks(range(2), [k for k, _, _ in bars], fontsize=8.6)
    ax2.set_ylim(0, max(T["d_max"].median(), T["d_med"].median()) * 1.35)
    ax2.set_ylabel("median $\\Delta$PCE (%abs)")
    ax2.set_title(f"positive papers: {pos_med}/{len(T)}  vs  {pos_max}/{len(T)}", pad=12)
    fig.tight_layout()
    fig.savefig(FIG / "01_delta_max_vs_median.png")
    plt.close(fig)


# ----------------------------------------------- 2. flip list: with/without log
FEATS = ["voc", "jsc", "ff", "grain_size", "contact_angle", "afm_rms",
         "trap_density", "rs", "rrec", "vbm", "work_function"]


def flip_table(D):
    P = D.dropna(subset=["pce"])
    rows = []
    for f in FEATS:
        raw_n, _, _ = corr(P, f, "pce", "naive", use_log=False)
        raw_w, n, npap = corr(P, f, "pce", "within", use_log=False)
        log_n, _, _ = corr(P, f, "pce", "naive", use_log=True)
        log_w, _, _ = corr(P, f, "pce", "within", use_log=True)
        rows.append(dict(feature=f, logged=f in LOG, n_dev=n, n_paper=npap,
                         raw_naive=raw_n, raw_within=raw_w,
                         log_naive=log_n, log_within=log_w,
                         flip_raw=np.sign(raw_n) != np.sign(raw_w),
                         flip_log=np.sign(log_n) != np.sign(log_w)))
    F = pd.DataFrame(rows)
    F["disagrees"] = F["flip_raw"] != F["flip_log"]
    return F


def fig_flip(F):
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    x = np.arange(len(F))
    ax.axhline(0, color=MUTED, lw=1.2)
    ax.bar(x - .2, F["log_naive"], width=.38, color=BASE, label="pooled across papers")
    ax.bar(x + .2, F["log_within"], width=.38, color=C_OK, label="within paper")
    for i, r in F.iterrows():
        if r["disagrees"]:
            ax.annotate("scale-dependent", (i, -1.02), ha="center", fontsize=7.6, color=C_BAD)
            ax.axvspan(i - .45, i + .45, color=C_BAD, alpha=.07, zorder=0)
    ax.set_xticks(x, [f + ("\n(log)" if lg else "") for f, lg in zip(F["feature"], F["logged"])],
                  fontsize=7.8, rotation=35, ha="right")
    ax.set_ylim(-1.15, 1.15)
    ax.set_ylabel("Spearman-free Pearson r with PCE")
    ax.set_title("Correlation with PCE, log10 applied to trap density / Rs / Rrec", pad=12)
    ax.legend(frameon=False, fontsize=8.4, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG / "02_flip_list_log_vs_raw.png")
    plt.close(fig)


# --------------------------------- 3. retention: max-across-protocol vs matched
RET_FEATS = ["n_coating_layers", "trap_density", "afm_rms", "rs", "contact_angle",
             "rrec", "grain_size", "hi", "vbm", "work_function"]


def retention_tables(D):
    dev = pd.read_csv(ROOT / "processed/prepared_v2/devices.csv")
    st = pd.read_csv(ROOT / "processed/prepared_v2/stability.csv")
    s = st.merge(dev[["device_id", "doi_raw"]], on="device_id", how="left")
    s["paper"] = s["doi_raw"].str.replace(r"[._-]", "", regex=True).str.lower()
    s["ret"] = pd.to_numeric(s["retention_pct"], errors="coerce")
    s["Tp"] = pd.to_numeric(s["temperature_c_parsed"], errors="coerce")
    s["t_hr"] = pd.to_numeric(s["time_hr"], errors="coerce")
    s = s[(s["Tp"] <= 30) | s["Tp"].isna()].dropna(subset=["ret"])
    # โปรโตคอล = สภาพแวดล้อม + ชั่วโมงทดสอบ ต้องตรงกันถึงเทียบกันได้
    s["proto"] = s["environment"].astype(str).str.strip() + " @ " + s["t_hr"].astype(str) + "h"
    keep = [g for _, g in s.groupby(["paper", "proto"]) if g["device_id"].nunique() >= 2]
    M = pd.concat(keep) if keep else s.iloc[:0]
    X = (M.groupby(["device_id", "paper", "proto"])["ret"].median().reset_index()
           .merge(D.drop(columns=["paper"]), on="device_id", how="left"))

    def matched(a):
        t = X.dropna(subset=[a, "ret"]).copy()
        t["_a"] = np.log10(t[a].where(t[a] > 0)) if a in LOG else t[a]
        t = t.dropna(subset=["_a"])
        parts = [pd.DataFrame({"x": g["_a"] - g["_a"].mean(), "y": g["ret"] - g["ret"].mean()})
                 for _, g in t.groupby(["paper", "proto"])
                 if len(g) >= 2 and g["_a"].nunique() > 1 and g["ret"].nunique() > 1]
        if not parts:
            return np.nan, 0, 0
        w = pd.concat(parts)
        return float(np.corrcoef(w["x"], w["y"])[0, 1]), len(w), len(parts)

    rows = []
    for f in RET_FEATS + ["pce", "voc", "jsc", "ff"]:
        o_r, o_n, o_p = corr(D, f, "retention_25c", "within")
        m_r, m_n, m_g = matched(f)
        rows.append(dict(feature=f, old_r=o_r, old_dev=o_n, old_paper=o_p,
                         matched_r=m_r, matched_dev=m_n, matched_groups=m_g))
    return pd.DataFrame(rows), s, M


def fig_retention(RT):
    T = RT[RT["matched_r"].notna()].copy()
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    ys = np.arange(len(T))[::-1]
    ax.hlines(ys, T["old_r"], T["matched_r"], color=BASE, lw=1.8, zorder=1)
    ax.scatter(T["old_r"], ys, s=36, color=C_BAD, zorder=3,
               label="max across protocols (as published)")
    ax.scatter(T["matched_r"], ys, s=36, color=C_OK, zorder=3,
               label="protocol-matched (environment + hours)")
    ax.axvline(0, color=MUTED, lw=1.2)
    ax.set_yticks(ys, [f"{r.feature}  ({r.old_paper}p → {r.matched_groups}g)"
                       for r in T.itertuples()], fontsize=8.2)
    ax.set_xlim(-1.08, 1.08)
    ax.set_xlabel("within-group correlation with retention")
    ax.set_title("Retention column: forcing the same stability protocol", pad=12)
    ax.legend(frameon=False, fontsize=8.4, loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG / "03_retention_protocol_matched.png")
    plt.close(fig)


# --------------------------------------------- 4. ปีตีพิมพ์ในฐานะ confounder
def year_tables(D):
    P = D.dropna(subset=["pce", "year"])
    pl = P.groupby("paper").agg(pce=("pce", "median"), year=("year", "first")).dropna()
    slope, intercept = np.polyfit(pl["year"], pl["pce"], 1)
    pred = intercept + slope * pl["year"]
    r2 = 1 - ((pl["pce"] - pred) ** 2).sum() / ((pl["pce"] - pl["pce"].mean()) ** 2).sum()

    T = delta_table(D)
    T["year"] = P.groupby("paper")["year"].first()
    Ty = T.dropna(subset=["year"])
    rho = np.corrcoef(Ty["year"].rank(), Ty["d_med"].rank())[0, 1]

    summary = pd.DataFrame([
        dict(quantity="PCE ~ year (paper medians)", value=slope, unit="%abs/yr", n=len(pl)),
        dict(quantity="R2 of year on paper medians", value=r2, unit="fraction", n=len(pl)),
        dict(quantity="Spearman(year, dPCE)", value=rho, unit="rho", n=len(Ty)),
        dict(quantity="median dPCE <=2022", value=Ty.loc[Ty["year"] <= 2022, "d_med"].median(),
             unit="%abs", n=int((Ty["year"] <= 2022).sum())),
        dict(quantity="median dPCE >=2023", value=Ty.loc[Ty["year"] >= 2023, "d_med"].median(),
             unit="%abs", n=int((Ty["year"] >= 2023).sum())),
    ])
    return pl, Ty, summary, slope, intercept, r2, rho


def fig_year(pl, Ty, slope, intercept, r2, rho):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.4))
    ax1.scatter(pl["year"], pl["pce"], s=40, color=C_BAD, edgecolor="white", lw=.7)
    xs = np.array([pl["year"].min(), pl["year"].max()])
    ax1.plot(xs, intercept + slope * xs, color=INK, lw=1.4, ls="--")
    ax1.set_xlabel("publication year")
    ax1.set_ylabel("paper median PCE (%)")
    ax1.set_title(f"Field trend: {slope:+.2f} %abs/yr · R² = {r2:.1%} (n = {len(pl)} papers)", pad=12)

    ax2.scatter(Ty["year"], Ty["d_med"], s=40, color=C_OK, edgecolor="white", lw=.7)
    ax2.axhline(Ty["d_med"].median(), color=INK, lw=1.4, ls="--")
    ax2.axhline(0, color=MUTED, lw=1.2)
    ax2.set_xlabel("publication year")
    ax2.set_ylabel("$\\Delta$PCE within paper (%abs)")
    ax2.set_title(f"Effect is flat in time: Spearman $\\rho$ = {rho:+.2f} (n = {len(Ty)} papers)", pad=12)
    fig.tight_layout()
    fig.savefig(FIG / "04_year_confounder.png")
    plt.close(fig)


# ------------------------------------------------------------------------ main
def main():
    D = load()
    T = delta_table(D)
    F = flip_table(D)
    RT, s_all, s_matched = retention_tables(D)

    fig_delta(T)
    fig_flip(F)
    fig_retention(RT)

    has_year = "year" in D.columns and D["year"].notna().any()
    if has_year:
        pl, Ty, yr_summary, slope, intercept, r2, rho = year_tables(D)
        fig_year(pl, Ty, slope, intercept, r2, rho)
    else:
        yr_summary = pd.DataFrame([dict(quantity="year column missing", value=np.nan,
                                        unit="", n=0)])

    P = D.dropna(subset=["pce"])

    def share(col):
        x = P.dropna(subset=[col])
        y = x[col].to_numpy(float)
        gm = x.groupby("paper")[col].transform("mean").to_numpy(float)
        return ((gm - y.mean()) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    var = pd.DataFrame([dict(metric=c, paper_share=share(c),
                             n_dev=int(P[c].notna().sum()),
                             n_paper=int(P.dropna(subset=[c])["paper"].nunique()))
                        for c in ["pce", "voc", "jsc", "ff", "retention_25c"]])

    with pd.ExcelWriter(HERE / "summary_tables.xlsx", engine="openpyxl") as xl:
        T.round(3).to_excel(xl, sheet_name="delta_per_paper")
        F.round(3).to_excel(xl, sheet_name="flip_log_vs_raw", index=False)
        RT.round(3).to_excel(xl, sheet_name="retention_matched", index=False)
        var.round(4).to_excel(xl, sheet_name="variance_share", index=False)
        yr_summary.round(4).to_excel(xl, sheet_name="year_confounder", index=False)

    print(f"เปเปอร์ที่จับคู่ได้ {len(T)}")
    print(f"  Δ ใช้ max    {T['d_max'].median():+.2f}  บวก {int((T['d_max']>0).sum())}/{len(T)}")
    print(f"  Δ ใช้ median {T['d_med'].median():+.2f}  บวก {int((T['d_med']>0).sum())}/{len(T)}")
    print(f"ตัวแปรที่รายชื่อพลิกทิศเปลี่ยนเมื่อใส่ log: {list(F.loc[F['disagrees'],'feature'])}")
    print(f"stability ~25C: {len(s_all)} แถว · จับคู่โปรโตคอลได้ {len(s_matched)} แถว")
    if has_year:
        print(f"ปี: {int(D['year'].notna().sum())}/{len(D)} เครื่อง · "
              f"{D['year'].min():.0f}-{D['year'].max():.0f} · "
              f"เทรนด์ {slope:+.2f} จุด/ปี (R2 {r2:.1%}) · Spearman(ปี,ΔPCE) {rho:+.2f}")
    print(f"เขียน {FIG}/*.png และ summary_tables.xlsx")


if __name__ == "__main__":
    main()
