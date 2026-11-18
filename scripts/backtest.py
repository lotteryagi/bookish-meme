#!/usr/bin/env python3
"""Geçmiş sezonlarla geriye dönük test: backtest/data/*.jsonl -> backtest/RAPOR.md

Her maç için predict.py'deki modeli farklı ayarlarla (oran kaynağı × ρ) çalıştırır ve
gerçek skorlarla karşılaştırır. Sadece standart kütüphane.

Kullanım: python scripts/backtest.py [backtest/data] [-o backtest/RAPOR.md]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import predict as P  # noqa: E402

# auto = Pinnacle varsa o, yoksa medyan (canlı kullanımdaki varsayılan); iki ayar da aynı maçları kapsar.
SOURCES = ("auto", "median")
RHOS = (0.0, -0.05, -0.10)
EPS = 1e-9
BUCKETS = [(0, 0.08), (0.08, 0.10), (0.10, 0.12), (0.12, 0.14), (0.14, 1.01)]


def load(data_dir: Path) -> list[dict]:
    rows = []
    for f in sorted(data_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def evaluate(matches: list[dict], source: str, rho: float) -> dict:
    per = []
    for r in matches:
        fit = P.fit_lambdas(r["odds"], source, rho)
        if fit is None:
            continue
        m = P.score_matrix(fit["lambda_home"], fit["lambda_away"], rho)
        hs, as_ = r["score"]
        p_actual = m[hs][as_] if hs <= P.MAX_GOALS and as_ <= P.MAX_GOALS else EPS
        ranked = sorted(((p, i, j) for i, row in enumerate(m) for j, p in enumerate(row)), reverse=True)
        top_p, ti, tj = ranked[0]
        h, d, a = P.p_1x2(m)
        result = 0 if hs > as_ else 1 if hs == as_ else 2
        mk = P.market_probs(r["odds"]["1x2"], source)
        per.append(
            {
                "league": r["league"],
                "day": (r.get("match_date") or "")[:10],
                "ll_score": -math.log(max(p_actual, EPS)),
                "top_p": top_p,
                "top_hit": (ti, tj) == (hs, as_),
                "top3_hit": (hs, as_) in [(i, j) for _, i, j in ranked[:3]],
                "ll_1x2_model": -math.log(max((h, d, a)[result], EPS)),
                "ll_1x2_market": -math.log(max(mk[result], EPS)) if mk else None,
                "fit_rmse": fit["fit_rmse"],
            }
        )
    return {"source": source, "rho": rho, "per": per}


def summarize(ev: dict) -> dict:
    per = ev["per"]
    n = len(per)
    if not n:
        return {"n": 0}
    mk = [x["ll_1x2_market"] for x in per if x["ll_1x2_market"] is not None]
    # İki maçlık kupon simülasyonu: her gün en yüksek "en olası skor" olasılığına sahip iki maç.
    by_day = defaultdict(list)
    for x in per:
        by_day[x["day"]].append(x)
    coupons = hits = 0
    expected = 0.0
    for items in by_day.values():
        if len(items) < 2:
            continue
        a, b = sorted(items, key=lambda x: -x["top_p"])[:2]
        coupons += 1
        hits += a["top_hit"] and b["top_hit"]
        expected += a["top_p"] * b["top_p"]
    return {
        "n": n,
        "ll_score": sum(x["ll_score"] for x in per) / n,
        "top_hit": sum(x["top_hit"] for x in per) / n,
        "top_p_mean": sum(x["top_p"] for x in per) / n,
        "top3_hit": sum(x["top3_hit"] for x in per) / n,
        "ll_1x2_model": sum(x["ll_1x2_model"] for x in per) / n,
        "ll_1x2_market": sum(mk) / len(mk) if mk else None,
        "fit_rmse": sum(x["fit_rmse"] for x in per) / n,
        "coupons": coupons,
        "coupon_hits": hits,
        "coupon_expected": expected,
    }


def pct(x: float) -> str:
    return f"%{x * 100:.1f}"


def report(matches: list[dict], results: list[dict]) -> str:
    leagues = Counter(r["league"] for r in matches)
    seasons = sorted({r["season"] for r in matches})
    scores = Counter(tuple(r["score"]) for r in matches)
    common, common_n = scores.most_common(1)[0]
    pin = sum(1 for r in matches if (r["odds"].get("1x2") or {}).get("pinnacle"))

    valid = [r for r in results if r["summary"].get("n")]
    best = min(valid, key=lambda r: r["summary"]["ll_score"])
    bs = best["summary"]
    out = [
        "# Geriye dönük test raporu",
        "",
        f"Oluşturma: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
        f"{len(matches)} maç · sezonlar: {', '.join(seasons)} · Pinnacle oranı olan: {pin}",
        "",
        "Ligler: " + ", ".join(f"{k} ({v})" for k, v in sorted(leagues.items())),
        "",
        "Not: sadece maç sonu oranlarıyla (1X2, Alt/Üst 1.5/2.5/3.5, Asya -2…+1.5); kapanış oranlarıdır "
        "(maçtan 2 saat önce alınan canlı veriden biraz daha bilgilidir).",
        "",
        "## Ayarların karşılaştırması",
        "",
        "| Kaynak | ρ | Maç | Skor log-loss ↓ | En olası skor tuttu | Ort. tahmin | İlk 3'te | 1X2 log-loss model / piyasa | Uyum hatası |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in valid:
        s = r["summary"]
        mark = " **(en iyi)**" if r is best else ""
        mk = f"{s['ll_1x2_market']:.4f}" if s["ll_1x2_market"] is not None else "-"
        out.append(
            f"| {r['source']}{mark} | {r['rho']:+.2f} | {s['n']} | {s['ll_score']:.4f} | {pct(s['top_hit'])} | "
            f"{pct(s['top_p_mean'])} | {pct(s['top3_hit'])} | {s['ll_1x2_model']:.4f} / {mk} | {s['fit_rmse']:.4f} |"
        )
    out += [
        "",
        "- *Skor log-loss*: gerçekleşen skora verilen olasılığın −log'u; küçük olan daha iyi.",
        "- *En olası skor tuttu* ile *Ort. tahmin* birbirine yakınsa model iyi kalibre demektir.",
        "- *1X2 log-loss model / piyasa* yakınsa model piyasayı doğru yansıtıyor demektir.",
        "",
        f"## En iyi ayar: kaynak = {best['source']}, ρ = {best['rho']:+.2f}",
        "",
        f"- En olası skor {pct(bs['top_hit'])} tuttu (model ortalama {pct(bs['top_p_mean'])} bekliyordu).",
        f"- Gerçek skor modelin ilk 3 skorundan biri: {pct(bs['top3_hit'])}.",
        f"- Basit karşılaştırma: her maça '{common[0]}-{common[1]}' demek {pct(common_n / len(matches))} tutardı.",
        f"- İki maçlık kupon simülasyonu (her gün en emin iki maç): {bs['coupons']} kupon, "
        f"**{bs['coupon_hits']} tuttu** (model beklentisi {bs['coupon_expected']:.1f}).",
        "",
        "### Kalibrasyon (en olası skorun olasılığına göre)",
        "",
        "| Model olasılığı | Maç | Gerçekte tutma |",
        "|---|---|---|",
    ]
    for lo, hi in BUCKETS:
        xs = [x for x in best["per"] if lo <= x["top_p"] < hi]
        if xs:
            out.append(
                f"| {pct(lo)}–{pct(min(hi, 1))} | {len(xs)} | {pct(sum(x['top_hit'] for x in xs) / len(xs))} |"
            )
    out += ["", "### Lig bazında (en iyi ayar)", "", "| Lig | Maç | Skor log-loss | En olası skor tuttu | Ort. tahmin |", "|---|---|---|---|---|"]
    by_league = defaultdict(list)
    for x in best["per"]:
        by_league[x["league"]].append(x)
    for lg, xs in sorted(by_league.items()):
        n = len(xs)
        out.append(
            f"| {lg} | {n} | {sum(x['ll_score'] for x in xs) / n:.4f} | "
            f"{pct(sum(x['top_hit'] for x in xs) / n)} | {pct(sum(x['top_p'] for x in xs) / n)} |"
        )
    out += [
        "",
        "## Sonuç",
        "",
        f"`scripts/predict.py` varsayılanı: kaynak `auto` (Pinnacle, yoksa medyan), ρ = {P.DEFAULT_RHO:+.2f}. "
        f"Test en iyi ayarı kaynak = {best['source']}, ρ = {best['rho']:+.2f} buldu"
        + (
            "; varsayılanla uyumlu."
            if best["rho"] == P.DEFAULT_RHO
            else f"; `DEFAULT_RHO` değerinin {best['rho']:+.2f} yapılması düşünülmeli."
        ),
        "",
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?", default="backtest/data")
    ap.add_argument("-o", "--out", default="backtest/RAPOR.md")
    args = ap.parse_args()
    matches = load(Path(args.data_dir))
    if not matches:
        sys.exit(f"{args.data_dir} içinde veri yok")
    results = []
    for source in SOURCES:
        for rho in RHOS:
            ev = evaluate(matches, source, rho)
            ev["summary"] = summarize(ev)
            results.append(ev)
            s = ev["summary"]
            if s.get("n"):
                print(f"{source:8} ρ={rho:+.2f}: n={s['n']} log-loss={s['ll_score']:.4f} tuttu={pct(s['top_hit'])}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report(matches, results), encoding="utf-8")
    print(f"Rapor: {args.out}")


if __name__ == "__main__":
    main()
