#!/usr/bin/env python3
"""Decompose municipal jeugdzorg cost growth 2021->2024 into a real
within-care-type *price/intensity* component versus a *volume/mix* component.

Question answered
-----------------
Rising blended cost-per-child only counts as an "efficiency" story if it is
NOT explained by (a) inflation, or (b) the care mix shifting toward expensive
residential care (JH met verblijf). This script strips both out.

Sources (already downloaded under data/analysis/jeugdzorg/):
  costs    83454NED  GerealiseerdeKosten_2  (x EUR 1.000)
  caseload 85099NED  JongerenMetJeugdzorg_1 (unique youths in that care form)

Method
------
Deflate 2024 euros to 2021 price level with CBS CPI (all items, 2015=100):
  2021 = 116.61, 2024 = 137.27  ->  factor 1.1772 (+17.7% cumulative).
For each aligned care type t in {zonder verblijf, met verblijf, JB, JR}:
  p_t = real cost per child in care = cost_t_real / kids_t
  dCost_t = cost24_t_real - cost21_t
         = kids21_t*(p24_t-p21_t)        # PRICE  (real intensity per child) -> counts
         + p21_t*(kids24_t-kids21_t)      # VOLUME (more/fewer kids, incl. mix) -> discount
         + (dkids)*(dp)                   # interaction (reported separately)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BASE = Path("data/analysis/jeugdzorg")
CPI_2021_TO_2024 = 137.27 / 116.61  # 1.1772

COST_MAP = {
    "total": "JZ Totaal jeugdzorg",
    "jh": "JH 1 Totaal jeugdhulp",
    "zonder": "JH zonder verblijf",
    "met": "JH met verblijf (Zin+PGB)",
    "jb": "JB 1 Totaal jeugdbescherming",
    "jr": "JR 1 Totaal jeugdreclassering",
    "jbr": "Totaal Jeugdbescherming en - reclasserin",  # JB+JR aggregate (often present when JB/JR alone are suppressed)
}
CASE_MAP = {
    "total": "JZ Totaal jeugdzorg",
    "jh": "JH 1 Totaal jeugdhulp",
    "zonder": "JH 121 Totaal jeugdhulp zonder verblijf",
    "met": "JH 122 Totaal jeugdhulp met verblijf",
    "jb": "JB 1 Totaal jeugdbescherming",
    "jr": "JR 1 Totaal jeugdreclassering",
}
ALIGNED = ["zonder", "met", "jb", "jr"]  # the four types with both cost and caseload


def load_costs() -> pd.DataFrame:
    df = pd.read_csv(BASE / "jz_kosten_83454.csv", dtype=str)
    df["VormenVanJeugdzorg"] = df["VormenVanJeugdzorg"].str.strip()
    df = df[df["Perioden"].isin(["2021", "2024"])].copy()
    df["cost"] = pd.to_numeric(df["GerealiseerdeKosten_2"], errors="coerce") * 1000.0  # EUR
    df = df.dropna(subset=["cost"])
    df = df.rename(columns={"Regio": "geo"})
    return df[["geo", "VormenVanJeugdzorg", "Perioden", "cost"]]


def load_caseload() -> pd.DataFrame:
    frames = []
    for yr in ("2021", "2024"):
        d = pd.read_csv(BASE / f"jz_85099_{yr}.csv", dtype=str)
        d["VormenVanJeugdzorg"] = d["VormenVanJeugdzorg"].str.strip()
        d = d[d["Perioden"] == yr].copy()
        d["kids"] = pd.to_numeric(d["JongerenMetJeugdzorg_1"], errors="coerce")
        d = d.dropna(subset=["kids"])
        d = d.rename(columns={"RegioS": "geo"})
        frames.append(d[["geo", "VormenVanJeugdzorg", "Perioden", "kids"]])
    return pd.concat(frames, ignore_index=True)


def pivot(df: pd.DataFrame, value: str, mapping: dict[str, str]) -> pd.DataFrame:
    inv = {v: k for k, v in mapping.items()}
    df = df[df["VormenVanJeugdzorg"].isin(inv)].copy()
    df["key"] = df["VormenVanJeugdzorg"].map(inv)
    wide = df.pivot_table(index="geo", columns=["key", "Perioden"], values=value, aggfunc="first")
    wide.columns = [f"{value[:1]}_{k}_{p}" for k, p in wide.columns]
    return wide


def main() -> int:
    costs = pivot(load_costs(), "cost", COST_MAP)
    cases = pivot(load_caseload(), "kids", CASE_MAP)
    m = costs.join(cases, how="inner")

    # deflate every 2024 cost column to 2021 euros
    for c in [c for c in m.columns if c.startswith("c_") and c.endswith("_2024")]:
        m[c] = m[c] / CPI_2021_TO_2024

    # cost has no single "zonder verblijf" row (it is split wijkteam / niet-wijkteam);
    # derive it exactly as total jeugdhulp minus jeugdhulp met verblijf.
    for yr in ("2021", "2024"):
        m[f"c_zonder_{yr}"] = m[f"c_jh_{yr}"] - m[f"c_met_{yr}"]

    rows = []
    for geo, r in m.iterrows():
        c21t, c24t = r.get("c_total_2021"), r.get("c_total_2024")
        k21t, k24t = r.get("k_total_2021"), r.get("k_total_2024")
        if pd.isna(c21t) or c21t < 5_000_000:  # budget >= EUR 5M (real)
            continue
        if any(pd.isna(x) for x in (c24t, k21t, k24t)) or k21t <= 0 or k24t <= 0:
            continue
        real_growth = 100 * (c24t - c21t) / c21t
        # blended (inflation-stripped, NOT mix-stripped) real cost per child
        blended_cpc_g = 100 * ((c24t / k24t) / (c21t / k21t) - 1)
        # caseload mix proxy: residential (met verblijf) share of all youths in care
        kmet21, kmet24 = r.get("k_met_2021"), r.get("k_met_2024")
        case_met_shift = (round(100 * kmet24 / k24t - 100 * kmet21 / k21t, 1)
                          if not (pd.isna(kmet21) or pd.isna(kmet24)) else None)

        # full per-care-type price/volume decomposition (needs cost split)
        price = volume = inter = base_aligned = 0.0
        per_type = {}
        ok = True
        for t in ALIGNED:
            c21 = r.get(f"c_{t}_2021"); c24 = r.get(f"c_{t}_2024")
            k21 = r.get(f"k_{t}_2021"); k24 = r.get(f"k_{t}_2024")
            if any(pd.isna(x) for x in (c21, c24, k21, k24)) or k21 <= 0 or k24 <= 0:
                ok = False
                break
            p21, p24 = c21 / k21, c24 / k24
            price += k21 * (p24 - p21)
            volume += p21 * (k24 - k21)
            inter += (k24 - k21) * (p24 - p21)
            base_aligned += c21
            per_type[t] = (c21, c24)

        row = {
            "Regio": geo,
            "cost24_realM": round(c24t / 1e6, 1),
            "real_growth_%": round(real_growth, 0),
            "blended_cpc_g_%": round(blended_cpc_g, 0),
            "case_met_shift_pp": case_met_shift,
        }
        if ok and base_aligned > 0:
            cost_met_shift = round(100 * per_type["met"][1] / sum(per_type[t][1] for t in ALIGNED)
                                   - 100 * per_type["met"][0] / sum(per_type[t][0] for t in ALIGNED), 1)
            denom = price + volume + inter
            row.update({
                "coverage": "full",
                "price_%base": round(100 * price / base_aligned, 0),
                "volume_%base": round(100 * volume / base_aligned, 0),
                "price_share_%": round(100 * price / denom, 0) if denom else float("nan"),
                "cost_met_shift_pp": cost_met_shift,
            })
            if real_growth < 8:
                row["verdict"] = "nominal only (< inflation)"
            elif row["price_share_%"] >= 60:
                row["verdict"] = "REAL efficiency decay (price-driven)"
            elif row["price_share_%"] <= 35:
                row["verdict"] = "volume/mix — discount"
            else:
                row["verdict"] = "mixed"
        else:
            cjh21, cjh24 = r.get("c_jh_2021"), r.get("c_jh_2024")
            kjh21, kjh24 = r.get("k_jh_2021"), r.get("k_jh_2024")
            jh_ok = not any(pd.isna(x) for x in (cjh21, cjh24, kjh21, kjh24)) and kjh21 > 0 and kjh24 > 0
            if jh_ok:
                # real cost-per-child WITHIN jeugdhulp (strips JB/JR mix + inflation);
                # residential met/zonder mix checked on the caseload side (case_met_shift).
                jh_cpc_g = 100 * ((cjh24 / kjh24) / (cjh21 / kjh21) - 1)
                jh_real_g = 100 * (cjh24 - cjh21) / cjh21
                row.update({"coverage": "jh-level", "price_%base": None, "volume_%base": None,
                            "price_share_%": None, "cost_met_shift_pp": None,
                            "jh_cpc_g_%": round(jh_cpc_g, 0), "jh_real_g_%": round(jh_real_g, 0)})
                if jh_real_g < 8:
                    row["verdict"] = "nominal only (< inflation)"
                elif jh_cpc_g >= 15 and (case_met_shift is None or case_met_shift <= 2):
                    row["verdict"] = "REAL (jeugdhulp cost/child up, residential mix flat)"
                elif case_met_shift is not None and case_met_shift > 2:
                    row["verdict"] = "jeugdhulp cost/child up but kids shifted to residential — partial mix"
                else:
                    row["verdict"] = "jeugdhulp cost/child up (modest)"
            else:
                row.update({"coverage": "blended-only", "price_%base": None,
                            "volume_%base": None, "price_share_%": None, "cost_met_shift_pp": None})
                if real_growth < 8:
                    row["verdict"] = "nominal only (< inflation)"
                elif case_met_shift is not None and case_met_shift > 2:
                    row["verdict"] = "cost split N/A — caseload shifted to residential (possible mix)"
                else:
                    row["verdict"] = "cost split N/A — caseload mix ~flat (blended rise likely genuine)"
        rows.append(row)

    out = pd.DataFrame(rows)
    for c in ("jh_cpc_g_%", "jh_real_g_%"):
        if c not in out:
            out[c] = float("nan")
    rank = {"full": 0, "jh-level": 1, "blended-only": 2}
    out["_r"] = out["coverage"].map(rank)
    out = out.sort_values(["_r", "price_%base", "jh_cpc_g_%", "blended_cpc_g_%"],
                          ascending=[True, False, False, False]).drop(columns="_r")
    dest = BASE / "cost_decomposed.csv"
    out.to_csv(dest, index=False)
    print(f"CPI deflator 2021->2024 = {CPI_2021_TO_2024:.4f} (+{(CPI_2021_TO_2024-1)*100:.1f}%)")
    print(f"municipalities (real budget >= EUR 5M): {len(out)}  [full: {(out.coverage=='full').sum()}, "
          f"jh-level: {(out.coverage=='jh-level').sum()}, blended-only: {(out.coverage=='blended-only').sum()}]")
    print(f"written: {dest}\n=== verdict counts ===")
    print(out["verdict"].value_counts().to_string())
    with pd.option_context("display.max_rows", None, "display.width", 230):
        full = out[out.coverage == "full"]
        print(f"\n=== FULL DECOMP — top 18 by real within-type PRICE growth (genuine, mix-stripped) ===")
        print(full[["Regio", "cost24_realM", "real_growth_%", "price_%base", "volume_%base",
                    "price_share_%", "cost_met_shift_pp", "verdict"]].head(18).to_string(index=False))
        jh = out[(out.coverage == "jh-level") & (out["jh_real_g_%"] >= 8)]
        print(f"\n=== JH-LEVEL (upgraded) — real jeugdhulp cost/child + caseload residential-mix check ===")
        print(jh[["Regio", "cost24_realM", "jh_real_g_%", "jh_cpc_g_%",
                  "case_met_shift_pp", "verdict"]].head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
