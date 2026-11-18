#!/usr/bin/env python3
"""Geçmiş sezonlarla geriye dönük test: backtest/data/*.jsonl -> backtest/RAPOR.md

Her maç için predict.py'deki modeli farklı ayarlarla (oran kaynağı × ρ) çalıştırır ve
gerçek skorlarla karşılaştırır. Sadece standart kütüphane.

Kullanım: python scripts/backtest.py [backtest/data] [-o backtest/RAPOR.md]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
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


# ------------------------------------------------- Claude'un kör tahminleri (B ve C)
#
# C.csv: match_link,lambda_home,lambda_away,gerekce  -> oranları görmeden, sadece kendi bilgisiyle
# B.csv: match_link,delta_home,delta_away,gerekce    -> A'nın λ'larına kendi bilgisiyle düzeltme
# İkisi de skorlar görülmeden yazılır (--blind çıktısında skor yoktur) ve önce commit edilir.


def export_blind(matches: list[dict], out: Path, with_a: bool) -> None:
    """Skorları İÇERMEYEN maç listesi; with_a ise A modelinin λ ve olasılıkları da eklenir."""
    cols = ["match_link", "league", "date", "home", "away"]
    if with_a:
        cols += ["lambda_home_A", "lambda_away_A", "p_home_A", "p_draw_A", "p_away_A", "p_over_2_5_A"]
    rows = []
    for r in sorted(matches, key=lambda x: (x["league"], x.get("match_date") or "")):
        row = {
            "match_link": r["match_link"],
            "league": r["league"],
            "date": (r.get("match_date") or "")[:16],
            "home": r.get("home"),
            "away": r.get("away"),
        }
        if with_a:
            fit = P.fit_lambdas(r["odds"], "auto", P.DEFAULT_RHO)
            if fit is None:
                continue
            d = P.describe(fit["lambda_home"], fit["lambda_away"], P.DEFAULT_RHO)
            row.update(
                lambda_home_A=d["lambda_home"], lambda_away_A=d["lambda_away"], p_home_A=d["p_home"],
                p_draw_A=d["p_draw"], p_away_A=d["p_away"], p_over_2_5_A=d["p_over_2_5"],
            )
        rows.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} maç (skorsuz) -> {out}")


def _read_csv(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["match_link"]: r for r in csv.DictReader(fh) if r.get("match_link")}


def _score_metrics(m, score) -> tuple[float, bool, float]:
    hs, as_ = score
    p = m[hs][as_] if hs <= P.MAX_GOALS and as_ <= P.MAX_GOALS else EPS
    best = max(((q, i, j) for i, row in enumerate(m) for j, q in enumerate(row)))
    h, d, a = P.p_1x2(m)
    res = 0 if hs > as_ else 1 if hs == as_ else 2
    return -math.log(max(p, EPS)), (best[1], best[2]) == (hs, as_), -math.log(max((h, d, a)[res], EPS))


def claude_section(matches: list[dict], kor_dir: Path) -> list[str]:
    c_rows, b_rows = _read_csv(kor_dir / "C.csv"), _read_csv(kor_dir / "B.csv")
    if not c_rows and not b_rows:
        return []
    rho = P.DEFAULT_RHO
    stats = {k: {"ll": [], "hit": [], "ll1x2": []} for k in ("A", "B", "C")}
    diffs = {"B": [], "C": []}
    for r in matches:
        link = r["match_link"]
        if link not in c_rows and link not in b_rows:
            continue
        fit = P.fit_lambdas(r["odds"], "auto", rho)
        if fit is None:
            continue
        lh, la = fit["lambda_home"], fit["lambda_away"]
        res = {"A": _score_metrics(P.score_matrix(lh, la, rho), r["score"])}
        if link in b_rows:
            b = b_rows[link]
            dh, da = float(b.get("delta_home") or 0), float(b.get("delta_away") or 0)
            res["B"] = _score_metrics(P.score_matrix(max(lh + dh, 0.05), max(la + da, 0.05), rho), r["score"])
        if link in c_rows:
            c = c_rows[link]
            res["C"] = _score_metrics(
                P.score_matrix(float(c["lambda_home"]), float(c["lambda_away"]), rho), r["score"]
            )
        if "B" in res and "C" in res:  # üçü de olan maçlar üzerinden adil karşılaştırma
            for k, (ll, hit, ll1) in res.items():
                stats[k]["ll"].append(ll)
                stats[k]["hit"].append(hit)
                stats[k]["ll1x2"].append(ll1)
            diffs["B"].append(res["B"][0] - res["A"][0])
            diffs["C"].append(res["C"][0] - res["A"][0])
    n = len(stats["A"]["ll"])
    out = ["", "## Claude'un kör tahminleri (web yok, sonuçlar görülmeden)", ""]
    if not n:
        return out + ["B.csv ve C.csv'de ortak maç yok."]
    out += [
        f"{n} maç. A = sadece oranlar · B = oranlar + Claude'un bilgisi · C = sadece Claude'un bilgisi (oransız).",
        "",
        "| Tahmin | Skor log-loss ↓ | En olası skor tuttu | 1X2 log-loss ↓ |",
        "|---|---|---|---|",
    ]
    for k in ("A", "B", "C"):
        s = stats[k]
        out.append(
            f"| {k} | {sum(s['ll']) / n:.4f} | {pct(sum(s['hit']) / n)} | {sum(s['ll1x2']) / n:.4f} |"
        )
    out.append("")
    for k in ("B", "C"):
        d = diffs[k]
        mean = sum(d) / n
        se = statistics.stdev(d) / math.sqrt(n) if n > 1 else float("nan")
        verdict = (
            "A'dan anlamlı şekilde **daha iyi**" if mean + 2 * se < 0
            else "A'dan anlamlı şekilde **daha kötü**" if mean - 2 * se > 0
            else "A'dan farkı **şanstan ayırt edilemiyor**"
        )
        out.append(f"- {k} − A log-loss farkı: {mean:+.4f} (±{2 * se:.4f}, %95) → {verdict}.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?", default="backtest/data")
    ap.add_argument("-o", "--out", default="backtest/RAPOR.md")
    ap.add_argument("--blind", metavar="CSV", help="skorsuz maç listesini yaz ve çık")
    ap.add_argument("--with-a", action="store_true", help="--blind listesine A modelinin λ'larını ekle")
    ap.add_argument("--kor-dir", default="backtest/kor", help="Claude'un B.csv / C.csv dosyaları")
    args = ap.parse_args()
    matches = load(Path(args.data_dir))
    if not matches:
        sys.exit(f"{args.data_dir} içinde veri yok")
    if args.blind:
        export_blind(matches, Path(args.blind), args.with_a)
        return
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
    text = report(matches, results) + "\n".join(claude_section(matches, Path(args.kor_dir))) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Rapor: {args.out}")


if __name__ == "__main__":
    main()
