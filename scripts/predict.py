#!/usr/bin/env python3
"""Oranlardan skor olasılıkları (Dixon-Coles). Sadece standart kütüphane.

Alt komutlar:
  match  <data/...json>         -> bir maç için A (model) ve B (model + düzeltme) tahmini
  coupon <tahmin.json> ...      -> tahminlerden en olası iki maçlık skor kuponu
  reduce <historic.json> <out>  -> OddsHarvester historic çıktısını backtest için küçültür

Yöntem:
  1. Her market için bookmaker oranları: Pinnacle varsa o, yoksa bookmaker medyanı.
     Marj orantılı yöntemle ayıklanır (olasılıklar 1'e normalize).
  2. Ev / deplasman gol beklentisi (λ), modelin 1X2 + Alt/Üst + Asya handikap
     olasılıkları piyasaya en iyi uyacak şekilde bulunur (ağırlıklı en küçük kareler,
     Nelder-Mead). Çeyrek baremler (x.25 / x.75) kullanılmaz.
  3. Skor matrisi: bağımsız Poisson + Dixon-Coles düşük skor düzeltmesi (ρ).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path

MAX_GOALS = 10
DEFAULT_RHO = -0.05
CHECK_TOLERANCE = 0.03  # model ile piyasa arasında izin verilen fark (1X2, 2.5 Üst)

OU_RE = re.compile(r"^over_under_(\d+(?:_\d+)?)_market$")
AH_RE = re.compile(r"^asian_handicap_([-+]?\d+(?:_\d+)?)_market$")
MARKETS = {
    "1x2_market": ("1x2", ("1", "X", "2")),
    "btts_market": ("btts", ("btts_yes", "btts_no")),
}


# ---------------------------------------------------------------- oranlar


def _odd(value) -> float | None:
    try:
        x = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 else None


def _fmt_line(line: float) -> str:
    return f"{line:g}"


def _summarize_rows(rows: list, labels: tuple[str, ...]) -> dict | None:
    valid, pinnacle = [], None
    for row in rows:
        if not isinstance(row, dict) or row.get("blocked_outcomes"):
            continue
        odds = [_odd(row.get(lab)) for lab in labels]
        if any(o is None for o in odds):
            continue
        valid.append(odds)
        if pinnacle is None and "pinnacle" in str(row.get("bookmaker_name", "")).lower():
            pinnacle = odds
    if not valid:
        return None
    median = [statistics.median(o[i] for o in valid) for i in range(len(labels))]
    return {"pinnacle": pinnacle, "median": median, "n": len(valid)}


def summarize_odds(period_rec: dict) -> dict:
    """Bir periyot kaydındaki marketleri küçük bir özete çevirir.

    {'1x2': {pinnacle, median, n}, 'btts': {...}, 'ou': {'2.5': {...}}, 'ah': {'-0.5': {...}}}
    Asya handikap baremi ev sahibine göredir (handicap_team_1 = ev sahibi).
    """
    out: dict = {"1x2": None, "btts": None, "ou": {}, "ah": {}}
    for key, rows in (period_rec or {}).items():
        if not isinstance(rows, list):
            continue
        if key in MARKETS:
            kind, labels = MARKETS[key]
            out[kind] = _summarize_rows(rows, labels)
        elif m := OU_RE.match(key):
            s = _summarize_rows(rows, ("odds_over", "odds_under"))
            if s:
                out["ou"][_fmt_line(float(m.group(1).replace("_", ".")))] = s
        elif m := AH_RE.match(key):
            s = _summarize_rows(rows, ("handicap_team_1", "handicap_team_2"))
            if s:
                out["ah"][_fmt_line(float(m.group(1).replace("_", ".")))] = s
    return out


def devig(odds: list[float]) -> list[float]:
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    return [x / total for x in inv]


def market_probs(summary: dict | None, source: str) -> list[float] | None:
    if not summary:
        return None
    odds = summary.get("pinnacle") if source in ("auto", "pinnacle") else None
    if odds is None:
        if source == "pinnacle":
            return None
        odds = summary.get("median")
    return devig(odds) if odds else None


# ---------------------------------------------------------------- model


def _pmf(lam: float) -> list[float]:
    p = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        p.append(p[-1] * lam / k)
    return p


def score_matrix(lh: float, la: float, rho: float = DEFAULT_RHO) -> list[list[float]]:
    ph, pa = _pmf(lh), _pmf(la)
    m = [[ph[i] * pa[j] for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    if rho:
        m[0][0] *= max(1 - lh * la * rho, 0.0)
        m[0][1] *= max(1 + lh * rho, 0.0)
        m[1][0] *= max(1 + la * rho, 0.0)
        m[1][1] *= max(1 - rho, 0.0)
    total = sum(map(sum, m))
    return [[x / total for x in row] for row in m]


def p_1x2(m) -> tuple[float, float, float]:
    h = d = a = 0.0
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    return h, d, a


def p_over(m, line: float) -> float:
    """İadeli baremde (tam sayı) iade hariç koşullu olasılık."""
    over = under = 0.0
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            if i + j > line:
                over += p
            elif i + j < line:
                under += p
    return over / (over + under) if over + under else 0.5


def p_ah_home(m, line: float) -> float:
    win = lose = 0.0
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            d = i - j + line
            if d > 0:
                win += p
            elif d < 0:
                lose += p
    return win / (win + lose) if win + lose else 0.5


def p_btts(m) -> float:
    return sum(p for i, row in enumerate(m) for j, p in enumerate(row) if i > 0 and j > 0)


def _usable_line(line: float) -> bool:
    return (line * 2) == int(line * 2)  # x.0 ve x.5; çeyrekler değil


def _weight(p: float) -> float:
    return 1.0 if 0.25 <= p <= 0.75 else 0.35  # dengeli baremler daha bilgilendirici


def build_targets(summary: dict, source: str) -> list[tuple]:
    t = []
    probs = market_probs(summary.get("1x2"), source)
    if probs:
        t += [("1", None, probs[0], 1.0), ("2", None, probs[2], 1.0)]
    for key, s in (summary.get("ou") or {}).items():
        line = float(key)
        probs = market_probs(s, source)
        if probs and _usable_line(line):
            t.append(("ou", line, probs[0], _weight(probs[0])))
    for key, s in (summary.get("ah") or {}).items():
        line = float(key)
        probs = market_probs(s, source)
        if probs and _usable_line(line):
            t.append(("ah", line, probs[0], _weight(probs[0])))
    probs = market_probs(summary.get("btts"), source)
    if probs:
        t.append(("btts", None, probs[0], 0.5))
    return t


def _model_value(m, kind, line, cache):
    if kind in ("1", "2"):
        if "1x2" not in cache:
            cache["1x2"] = p_1x2(m)
        return cache["1x2"][0 if kind == "1" else 2]
    if kind == "ou":
        return p_over(m, line)
    if kind == "ah":
        return p_ah_home(m, line)
    return p_btts(m)


def _loss(targets, lh, la, rho) -> float:
    m = score_matrix(lh, la, rho)
    cache: dict = {}
    return sum(w * (_model_value(m, k, line, cache) - target) ** 2 for k, line, target, w in targets)


def _nelder_mead(f, x0, step=0.25, iters=300, tol=1e-12):
    pts = [list(x0), [x0[0] + step, x0[1]], [x0[0], x0[1] + step]]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(3), key=vals.__getitem__)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[2] - vals[0]) < tol:
            break
        c = [(pts[0][k] + pts[1][k]) / 2 for k in range(2)]
        xr = [c[k] + (c[k] - pts[2][k]) for k in range(2)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c[k] + 2 * (c[k] - pts[2][k]) for k in range(2)]
            fe = f(xe)
            pts[2], vals[2] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[1]:
            pts[2], vals[2] = xr, fr
        else:
            xc = [c[k] + 0.5 * (pts[2][k] - c[k]) for k in range(2)]
            fc = f(xc)
            if fc < vals[2]:
                pts[2], vals[2] = xc, fc
            else:
                for i in (1, 2):
                    pts[i] = [pts[0][k] + 0.5 * (pts[i][k] - pts[0][k]) for k in range(2)]
                    vals[i] = f(pts[i])
    best = min(range(3), key=vals.__getitem__)
    return pts[best], vals[best]


def fit_lambdas(summary: dict, source: str = "auto", rho: float = DEFAULT_RHO) -> dict | None:
    targets = build_targets(summary, source)
    if len(targets) < 2:
        return None

    def f(x):
        return _loss(targets, math.exp(x[0]), math.exp(x[1]), rho)

    (xh, xa), loss = _nelder_mead(f, [math.log(1.4), math.log(1.1)])
    return {
        "lambda_home": math.exp(xh),
        "lambda_away": math.exp(xa),
        "rho": rho,
        "n_targets": len(targets),
        "fit_rmse": math.sqrt(loss / sum(t[3] for t in targets)),
    }


def describe(lh: float, la: float, rho: float, top: int = 8) -> dict:
    m = score_matrix(lh, la, rho)
    scores = sorted(
        ((f"{i}-{j}", p) for i, row in enumerate(m) for j, p in enumerate(row)), key=lambda x: -x[1]
    )
    h, d, a = p_1x2(m)
    return {
        "lambda_home": round(lh, 3),
        "lambda_away": round(la, 3),
        "top_scores": [[s, round(p, 4)] for s, p in scores[:top]],
        "p_home": round(h, 4),
        "p_draw": round(d, 4),
        "p_away": round(a, 4),
        "p_over_2_5": round(p_over(m, 2.5), 4),
        "p_btts": round(p_btts(m), 4),
    }


def checks(summary: dict, source: str, pred: dict) -> dict:
    diffs = {}
    probs = market_probs(summary.get("1x2"), source)
    if probs:
        diffs["1"] = pred["p_home"] - probs[0]
        diffs["X"] = pred["p_draw"] - probs[1]
        diffs["2"] = pred["p_away"] - probs[2]
    probs = market_probs((summary.get("ou") or {}).get("2.5"), source)
    if probs:
        diffs["over_2_5"] = pred["p_over_2_5"] - probs[0]
    worst = max((abs(v) for v in diffs.values()), default=None)
    return {
        "diffs": {k: round(v, 4) for k, v in diffs.items()},
        "max_abs_diff": None if worst is None else round(worst, 4),
        "ok": worst is not None and worst <= CHECK_TOLERANCE,
    }


def predict_period(period_rec: dict, source: str, rho: float, dh: float = 0.0, da: float = 0.0) -> dict | None:
    summary = summarize_odds(period_rec)
    fit = fit_lambdas(summary, source, rho)
    if fit is None:
        return None
    a = describe(fit["lambda_home"], fit["lambda_away"], rho)
    b = describe(max(fit["lambda_home"] + dh, 0.05), max(fit["lambda_away"] + da, 0.05), rho)
    one = summary.get("1x2") or {}
    return {
        "source": "pinnacle" if source != "median" and one.get("pinnacle") else "median",
        "n_bookmakers_1x2": one.get("n"),
        "fit_rmse": round(fit["fit_rmse"], 4),
        "n_targets": fit["n_targets"],
        "check": checks(summary, source, a),
        "A": a,
        "B": b,
        "adjustment": {"delta_home": dh, "delta_away": da},
    }


def predict_match(data: dict, source="auto", rho=DEFAULT_RHO, dh=0.0, da=0.0, note: str = "") -> dict:
    periods = data.get("periods") or {}
    ft_rec = periods.get("full_time") or {}
    out = {
        "match_link": data.get("match_link"),
        "league": data.get("league"),
        "home_team": ft_rec.get("home_team"),
        "away_team": ft_rec.get("away_team"),
        "kickoff_local": data.get("kickoff_local"),
        "scraped_at_utc": data.get("scraped_at_utc"),
        "adjustment_note": note,
        "full_time": predict_period(ft_rec, source, rho, dh, da),
        "1st_half": None,
    }
    ft = out["full_time"]
    if periods.get("1st_half") and ft:
        # İlk yarıya düzeltme, maç sonu λ'sındaki oranla taşınır.
        base_h, base_a = ft["A"]["lambda_home"], ft["A"]["lambda_away"]
        fh = predict_period(periods["1st_half"], source, rho)
        if fh:
            if dh or da:
                kh = (base_h + dh) / base_h if base_h else 1.0
                ka = (base_a + da) / base_a if base_a else 1.0
                fh["B"] = describe(fh["A"]["lambda_home"] * kh, fh["A"]["lambda_away"] * ka, rho)
                fh["adjustment"] = {"factor_home": round(kh, 3), "factor_away": round(ka, 3)}
            out["1st_half"] = fh
    return out


# ---------------------------------------------------------------- çıktı


def _pct(p: float) -> str:
    return f"%{p * 100:.1f}"


def render(pred: dict) -> str:
    lines = [f"### {pred.get('home_team')} - {pred.get('away_team')}  ({pred.get('kickoff_local') or '?'})"]
    for period, title in (("full_time", "Maç sonu"), ("1st_half", "İlk yarı")):
        p = pred.get(period)
        if not p:
            lines.append(f"- {title}: oran verisi yok")
            continue
        chk = p["check"]
        lines.append(
            f"- **{title}** (kaynak: {p['source']}, 1X2'de {p['n_bookmakers_1x2']} bookmaker, "
            f"uyum farkı {chk['max_abs_diff']}, kontrol {'OK' if chk['ok'] else 'UYARI'})"
        )
        for tag in ("A", "B"):
            if tag == "B" and p["B"] == p["A"]:
                lines.append("  - B: düzeltme yok (B = A)")
                continue
            d = p[tag]
            tops = " · ".join(f"{s} {_pct(q)}" for s, q in d["top_scores"][:5])
            lines.append(
                f"  - {tag}: λ {d['lambda_home']:.2f} - {d['lambda_away']:.2f} | {tops} | "
                f"1 {_pct(d['p_home'])} X {_pct(d['p_draw'])} 2 {_pct(d['p_away'])} | "
                f"2.5 Üst {_pct(d['p_over_2_5'])} | KG Var {_pct(d['p_btts'])}"
            )
    if pred.get("adjustment_note"):
        lines.append(f"- Düzeltme gerekçesi: {pred['adjustment_note']}")
    return "\n".join(lines)


def cmd_match(args) -> None:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    pred = predict_match(data, args.source, args.rho, args.delta_home, args.delta_away, args.note or "")
    if args.json:
        Path(args.json).write_text(json.dumps(pred, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(pred))
    if pred["full_time"] and not pred["full_time"]["check"]["ok"]:
        print("UYARI: model piyasa olasılıklarından fazla sapıyor; sunmadan önce incele.", file=sys.stderr)


def cmd_coupon(args) -> None:
    cands = []
    for f in args.files:
        pred = json.loads(Path(f).read_text(encoding="utf-8"))
        ft = pred.get("full_time")
        if not ft or not ft["check"]["ok"]:
            print(f"atlandı (veri yok / kontrol başarısız): {f}", file=sys.stderr)
            continue
        score, p = ft[args.use]["top_scores"][0]
        cands.append((p, score, pred))
    if len(cands) < 2:
        sys.exit("Kupon için en az 2 geçerli tahmin gerekli.")
    cands.sort(key=lambda c: -c[0])
    (p1, s1, m1), (p2, s2, m2) = cands[0], cands[1]
    print(f"Kupon ({args.use}):")
    print(f"  {m1['home_team']} - {m1['away_team']}: {s1} ({_pct(p1)})")
    print(f"  {m2['home_team']} - {m2['away_team']}: {s2} ({_pct(p2)})")
    print(f"  Birlikte tutma ihtimali: {_pct(p1 * p2)} (yaklaşık {round(1 / (p1 * p2))} kuponda 1)")


# ---------------------------------------------------------------- backtest verisi


def parse_partial(text: str | None) -> list[int] | None:
    m = re.search(r"(\d+)\s*:\s*(\d+)", text or "")
    return [int(m.group(1)), int(m.group(2))] if m else None


def cmd_reduce(args) -> None:
    """historic çıktısını (JSON liste ya da --stream-ndjson satırları) küçük JSONL'e çevirir."""
    text = Path(args.historic).read_text(encoding="utf-8")
    try:
        records = json.loads(text)
    except json.JSONDecodeError:  # NDJSON: satır başına bir maç; yarım kalan son satırı atla
        records = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out, kept = [], 0
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        try:
            hs, as_ = int(rec.get("home_score")), int(rec.get("away_score"))
        except (TypeError, ValueError):
            continue
        summary = summarize_odds(rec)
        if not summary.get("1x2"):
            continue
        out.append(
            json.dumps(
                {
                    "league": args.league,
                    "season": args.season,
                    "match_link": rec.get("match_link"),
                    "match_date": rec.get("match_date"),
                    "home": rec.get("home_team"),
                    "away": rec.get("away_team"),
                    "score": [hs, as_],
                    "ht": parse_partial(rec.get("partial_results")),
                    "odds": summary,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        kept += 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    print(f"{kept} maç -> {args.out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("match", help="bir maç için A ve B tahmini")
    m.add_argument("file")
    m.add_argument("--source", choices=("auto", "pinnacle", "median"), default="auto")
    m.add_argument("--rho", type=float, default=DEFAULT_RHO)
    m.add_argument("--delta-home", type=float, default=0.0, help="B için ev sahibi λ düzeltmesi")
    m.add_argument("--delta-away", type=float, default=0.0, help="B için deplasman λ düzeltmesi")
    m.add_argument("--note", help="B düzeltmesinin gerekçesi")
    m.add_argument("--json", help="tahmini JSON olarak bu dosyaya da yaz")
    m.set_defaults(func=cmd_match)

    c = sub.add_parser("coupon", help="tahmin JSON'larından en olası iki maçlık kupon")
    c.add_argument("files", nargs="+")
    c.add_argument("--use", choices=("A", "B"), default="B")
    c.set_defaults(func=cmd_coupon)

    r = sub.add_parser("reduce", help="historic çıktısını backtest JSONL'ine çevirir")
    r.add_argument("historic")
    r.add_argument("out")
    r.add_argument("--league", required=True)
    r.add_argument("--season", required=True)
    r.set_defaults(func=cmd_reduce)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
