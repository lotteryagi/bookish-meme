#!/usr/bin/env python3
"""Hafta sonu (Cuma/Cumartesi/Pazar) maç planlayıcısı ve birleştirici.

Alt komutlar:
  check        -> liste çalışması mı, şu an çekilecek maç var mı? ($GITHUB_OUTPUT'a yazar)
  update-plan  -> OddsHarvester --links-only CSV çıktısından state/plan.json üretir
  schedule     -> plandaki maç saatlerine göre workflow'daki cron satırlarını yeniden yazar
  due          -> çekilme zamanı gelmiş maç linklerini dosyaya yazar
  merge        -> periyot çıktılarını + topluluk oylarını maç başına
                  data/<gün>/<lig>/<maç>.json olarak yazar, state/scraped.txt + index.jsonl'i günceller
  results-due  -> bitmiş ve skoru henüz alınmamış maçların linklerini yazar
  merge-results-> historic çıktısındaki skorları results/<gün>/<lig>/<maç>.json olarak yazar

Sadece standart kütüphane kullanır (tarayıcı kurulmadan çalışabilsin diye).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PLAN_FILE = ROOT / "state" / "plan.json"
SCRAPED_FILE = ROOT / "state" / "scraped.txt"
DATA_DIR = ROOT / "data"
INDEX_FILE = ROOT / "state" / "index.jsonl"
RESULTS_DIR = ROOT / "results"
RESULTS_DONE_FILE = ROOT / "state" / "results_done.txt"
RESULT_AFTER_HOURS = 3       # başlama + bu kadar saat sonra maç bitmiş sayılır
RESULT_GIVE_UP_DAYS = 21     # ertelenen maçın skoru bu kadar gün denenir

TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Istanbul"))
WINDOW_HOURS = float(os.environ.get("WINDOW_HOURS", "2"))  # maçtan kaç saat önce çekilecek
# Zamanlanmış bir çalışma GitHub yoğunluğunda atlanırsa diye, her slot için bu kadar dakika
# sonra bir yedek çalışma daha kurulur (0 = yedek yok). WINDOW_HOURS*60'tan küçük olmalı.
BACKUP_MINUTES = int(os.environ.get("BACKUP_MINUTES", "45"))
SCHEDULE_HORIZON_DAYS = 7
SLOTS_BEGIN = "# >>> MAÇ SLOTLARI (planner.py schedule yazar, elle düzenlemeyin)"
SLOTS_END = "# <<< MAÇ SLOTLARI"
# Python weekday: Pazartesi=0 ... Cuma=4, Cumartesi=5, Pazar=6
ALLOWED_WEEKDAYS = {int(d) for d in os.environ.get("ALLOWED_WEEKDAYS", "4,5,6").split(",")}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_kickoff(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def load_plan() -> dict:
    if PLAN_FILE.exists():
        try:
            return json.loads(PLAN_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"generated_at": None, "matches": {}}


def load_scraped() -> set[str]:
    if not SCRAPED_FILE.exists():
        return set()
    return {line.strip() for line in SCRAPED_FILE.read_text().splitlines() if line.strip()}


def write_output(**kwargs: object) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kwargs.items()]
    if out:
        with open(out, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def safe_name(text: str) -> str:
    """Dosya/klasör adında güvenli karakterler bırakır."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return cleaned[:120] or "x"


def match_slug(link: str) -> str:
    """https://.../premier-league/arsenal-chelsea-AbC123/#xyz -> arsenal-chelsea-AbC123__xyz"""
    parts = urlsplit(link)
    slug = [p for p in parts.path.split("/") if p][-1:] or ["match"]
    return safe_name("__".join(slug + ([parts.fragment] if parts.fragment else [])))


def write_json(path: Path, obj: object) -> int:
    """Kayıpsız, boşluksuz (girintisiz) düz JSON yazar; yazılan bayt sayısını döner."""
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return len(data)


def due_matches(plan: dict, scraped: set[str], now: datetime) -> list[dict]:
    """Başlamasına <= WINDOW_HOURS kalmış, henüz başlamamış ve daha önce çekilmemiş maçlar."""
    due = []
    for link, m in plan.get("matches", {}).items():
        ko = parse_kickoff(m.get("kickoff_utc"))
        if ko is None or link in scraped:
            continue
        if now < ko <= now + timedelta(hours=WINDOW_HOURS):
            due.append(m)
    return sorted(due, key=lambda m: m["kickoff_utc"])


def is_weekend_window(now: datetime) -> bool:
    """Bugün ya da WINDOW_HOURS sonrası Cuma/Cmt/Pazar'a denk geliyor mu (yerel saat)?"""
    for dt in (now, now + timedelta(hours=WINDOW_HOURS)):
        if dt.astimezone(TZ).weekday() in ALLOWED_WEEKDAYS:
            return True
    return False


# ----------------------------------------------------------------- commands


def cmd_check(args: argparse.Namespace) -> None:
    now = now_utc()
    if args.results:  # haftalık sonuç çalışması
        write_output(need_plan="false", due_count=0, run="true", results="true")
        return
    if args.plan:  # haftalık liste çalışması: gün kontrolü yok, her zaman çalışır
        write_output(need_plan="true", due_count=0, run="true")
        return
    if not args.force and not is_weekend_window(now):
        write_output(need_plan="false", due_count=0, run="false")
        print("Cuma/Cumartesi/Pazar penceresi dışında, çıkılıyor.")
        return

    due = due_matches(load_plan(), load_scraped(), now)
    write_output(need_plan="false", due_count=len(due), run=str(bool(due)).lower())


def cmd_update_plan(args: argparse.Namespace) -> None:
    now = now_utc()
    plan = load_plan()
    matches: dict[str, dict] = {}

    # Eski plandan henüz başlamamış maçları koru (listeleme geçici olarak boş dönerse diye)
    for link, m in plan.get("matches", {}).items():
        ko = parse_kickoff(m.get("kickoff_utc"))
        if ko and ko > now - timedelta(hours=6):
            matches[link] = m

    src = Path(args.links_csv)
    added = 0
    if src.exists() and src.stat().st_size > 0:
        with src.open(newline="") as fh:
            for row in csv.DictReader(fh):
                link = (row.get("match_link") or "").strip()
                ko = parse_kickoff(row.get("kickoff_utc"))
                if not link or ko is None:
                    continue
                local = ko.astimezone(TZ)
                if local.weekday() not in ALLOWED_WEEKDAYS:
                    matches.pop(link, None)  # maç hafta içine kaymış olabilir
                    continue
                matches[link] = {
                    "match_link": link,
                    "league": row.get("league", ""),
                    "kickoff_utc": ko.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "kickoff_local": local.isoformat(),
                    "match_day": local.date().isoformat(),
                }
                added += 1
    else:
        print(f"UYARI: {src} bulunamadı veya boş; eski plan korunuyor.", file=sys.stderr)

    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(
        json.dumps(
            {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"), "matches": matches},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Plan güncellendi: listede {added} hafta sonu maçı, planda toplam {len(matches)} maç.")


def cmd_schedule(args: argparse.Namespace) -> None:
    """Plandaki maçlar için workflow dosyasındaki slot cron satırlarını yeniden yazar.

    Her farklı başlama saati için (başlama - WINDOW_HOURS) anında bir çalışma, artı
    BACKUP_MINUTES sonra bir yedek. Cron UTC'dir; gün/ay sabit verildiği için satır
    yalnızca o tarihte tetiklenir ve sonraki haftanın liste çalışması onu siler.
    """
    now = now_utc()
    horizon = now + timedelta(days=SCHEDULE_HORIZON_DAYS)
    scraped = load_scraped()
    slots: dict[datetime, list[str]] = {}
    for link, m in load_plan().get("matches", {}).items():
        ko = parse_kickoff(m.get("kickoff_utc"))
        if ko is None or link in scraped or not (now < ko <= horizon):
            continue
        offsets = [0] + ([BACKUP_MINUTES] if BACKUP_MINUTES else [])
        for off in offsets:
            t = (ko - timedelta(hours=WINDOW_HOURS) + timedelta(minutes=off)).replace(second=0, microsecond=0)
            t = max(t, (now + timedelta(minutes=5)).replace(second=0, microsecond=0))  # geçmişe kurma
            if t < ko:
                slots.setdefault(t, []).append(m.get("league") or "?")

    lines = []
    for t in sorted(slots):
        local = t.astimezone(TZ)
        note = f"TR {local:%a %d.%m %H:%M} - {len(set(slots[t]))} lig"
        lines.append(f'    - cron: "{t.minute} {t.hour} {t.day} {t.month} *"   # {note}')

    wf = Path(args.workflow)
    text = wf.read_text()
    start, end = text.find(SLOTS_BEGIN), text.find(SLOTS_END)
    if start < 0 or end < start:
        sys.exit(f"HATA: {wf} içinde slot işaretleri bulunamadı")
    indent = text[text.rfind("\n", 0, start) + 1 : start]
    body = "\n".join(lines) + "\n" + indent if lines else ""
    text = text[: start + len(SLOTS_BEGIN)] + "\n" + body + text[end:]
    wf.write_text(text)
    print(f"{len(lines)} slot yazıldı:")
    print("\n".join(lines) if lines else "  (bu hafta sonu için maç yok)")
    write_output(slot_count=len(lines))


def cmd_due(args: argparse.Namespace) -> None:
    due = due_matches(load_plan(), load_scraped(), now_utc())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(m["match_link"] + "\n" for m in due))
    for m in due:
        print(f"  {m['kickoff_local']}  {m['league']:<25} {m['match_link']}")
    write_output(due_count=len(due))


def cmd_merge(args: argparse.Namespace) -> None:
    now = now_utc()
    plan = load_plan().get("matches", {})
    links_file = Path(args.links)
    wanted = [l.strip() for l in links_file.read_text().splitlines() if l.strip()]
    work = Path(args.work_dir)
    # Çekim, `due` adımı link dosyasını yazdıktan hemen sonra başlar; merge anı değil bu an esas alınır.
    scrape_start = datetime.fromtimestamp(links_file.stat().st_mtime, timezone.utc)

    per_match: dict[str, dict] = {}
    for link in wanted:
        meta = plan.get(link, {})
        ko = parse_kickoff(meta.get("kickoff_utc"))
        per_match[link] = {
            "match_link": link,
            "league": meta.get("league"),
            "kickoff_utc": meta.get("kickoff_utc"),
            "kickoff_local": meta.get("kickoff_local"),
            "scraped_at_utc": scrape_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "minutes_before_kickoff": round((ko - scrape_start).total_seconds() / 60) if ko else None,
            "periods": {},
            "history": {},
            "community_votes": None,
        }

    # Periyot çıktıları: work/odds_<period>.json (tüm marketler, geçmişsiz) -> "periods"
    #                    work/hist_<period>.json (seçili marketler, oran geçmişli) -> "history"
    for prefix, key in (("odds_", "periods"), ("hist_", "history")):
        for path in sorted(work.glob(f"{prefix}*.json")):
            period = path.stem.removeprefix(prefix)
            try:
                records = json.loads(path.read_text())
            except json.JSONDecodeError:
                print(f"UYARI: {path} okunamadı", file=sys.stderr)
                continue
            for rec in records if isinstance(records, list) else []:
                if not isinstance(rec, dict):
                    continue
                link = rec.get("match_link")
                if not link:
                    print(f"UYARI: {path} içinde match_link olmayan kayıt atlandı", file=sys.stderr)
                    continue
                if link not in per_match:
                    per_match[link] = {"match_link": link, "periods": {}, "community_votes": None}
                per_match[link].setdefault(key, {})[period] = rec

    # Topluluk oyları: work/community/<n>.json
    for path in sorted(glob.glob(str(work / "community" / "*.json"))):
        try:
            data = json.loads(Path(path).read_text())
        except json.JSONDecodeError:
            continue
        for rec in data if isinstance(data, list) else [data]:
            if not isinstance(rec, dict):
                continue
            url = rec.get("match_url")
            for link, m in per_match.items():
                if url and (url == link or url.split("#")[0] == link.split("#")[0]):
                    m["community_votes"] = rec

    # Her maç kendi dosyasına, bir kez yazılır: data/<maç-günü>/<lig>/<maç>.json
    # (gün dosyasını tekrar tekrar yeniden yazmak git geçmişini şişiriyordu)
    done = []
    index_rows: list[dict] = []
    total = 0
    for link, m in per_match.items():
        if not m["periods"]:
            print(f"UYARI: veri gelmedi, sonraki çalıştırmada tekrar denenecek: {link}", file=sys.stderr)
            continue
        meta = plan.get(link, {})
        day = meta.get("match_day") or now.astimezone(TZ).date().isoformat()
        f = DATA_DIR / day / safe_name(meta.get("league") or "unknown") / f"{match_slug(link)}.json"
        if f.exists():  # aynı maç ikinci kez çekildiyse eskisini ezme
            f = f.with_name(f"{match_slug(link)}__{scrape_start:%Y%m%dT%H%M%S}.json")
        size = write_json(f, m)
        total += size
        done.append(link)
        index_rows.append(
            {"match_link": link, "kickoff_utc": m.get("kickoff_utc"), "file": f.relative_to(ROOT).as_posix()}
        )
        print(f"{f.relative_to(ROOT)}: {size / 1e6:.2f} MB")
    if done:
        print(f"Toplam: {len(done)} maç, {total / 1e6:.2f} MB")

    if done:
        SCRAPED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SCRAPED_FILE.open("a") as fh:
            fh.write("".join(l + "\n" for l in done))
        with INDEX_FILE.open("a") as fh:
            fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in index_rows))
    write_output(saved_count=len(done))


# ------------------------------------------------------------------ sonuçlar


def load_index() -> dict[str, dict]:
    """state/index.jsonl: kaydedilen her maç için link -> {kickoff_utc, file}. Son satır kazanır."""
    rows: dict[str, dict] = {}
    if INDEX_FILE.exists():
        for line in INDEX_FILE.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("match_link"):
                rows[r["match_link"]] = r
    return rows


def load_results_done() -> set[str]:
    if not RESULTS_DONE_FILE.exists():
        return set()
    return {l.strip() for l in RESULTS_DONE_FILE.read_text().splitlines() if l.strip()}


def result_path(match_file: str) -> Path:
    """data/<gün>/<lig>/<maç>.json -> results/<gün>/<lig>/<maç>.json"""
    rel = Path(match_file)
    return RESULTS_DIR.joinpath(*rel.parts[1:]) if rel.parts and rel.parts[0] == "data" else RESULTS_DIR / rel.name


def cmd_results_due(args: argparse.Namespace) -> None:
    """Bitmiş (başlamasından RESULT_AFTER_HOURS geçmiş), sonucu henüz alınmamış maçlar."""
    now = now_utc()
    done = load_results_done()
    links = []
    for link, r in load_index().items():
        ko = parse_kickoff(r.get("kickoff_utc"))
        if link in done or ko is None:
            continue
        if now - timedelta(days=RESULT_GIVE_UP_DAYS) < ko <= now - timedelta(hours=RESULT_AFTER_HOURS):
            links.append(link)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(l + "\n" for l in sorted(links)))
    print(f"Sonucu alınacak {len(links)} maç")
    write_output(results_count=len(links))


def cmd_merge_results(args: argparse.Namespace) -> None:
    """historic çıktısındaki skorları results/<gün>/<lig>/<maç>.json olarak yazar."""
    src = Path(args.results_json)
    try:
        records = json.loads(src.read_text()) if src.exists() and src.stat().st_size else []
    except json.JSONDecodeError:
        print(f"UYARI: {src} okunamadı", file=sys.stderr)
        records = []
    index = load_index()
    fetched_at = now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")
    saved = []
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        link = rec.get("match_link")
        if link not in index:
            print(f"UYARI: indekste olmayan maç atlandı: {link}", file=sys.stderr)
            continue
        if rec.get("home_score") in (None, "") or rec.get("away_score") in (None, ""):
            print(f"UYARI: skor yok (ertelendi/bitmedi?), sonra tekrar denenecek: {link}", file=sys.stderr)
            continue
        out = {
            "match_link": link,
            "home_team": rec.get("home_team"),
            "away_team": rec.get("away_team"),
            "home_score": rec.get("home_score"),
            "away_score": rec.get("away_score"),
            "partial_results": rec.get("partial_results"),
            "fetched_at_utc": fetched_at,
            "odds_file": index[link]["file"],
            # Ham kayıt aynen (kapanış 1X2 oranları dahil)
            "record": rec,
        }
        f = result_path(index[link]["file"])
        write_json(f, out)
        saved.append(link)
        print(f"{f.relative_to(ROOT)}: {out['home_team']} {out['home_score']}-{out['away_score']} {out['away_team']}")
    if saved:
        with RESULTS_DONE_FILE.open("a") as fh:
            fh.write("".join(l + "\n" for l in saved))
    write_output(results_saved=len(saved))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--force", action="store_true", help="gün kontrolünü atla")
    c.add_argument("--plan", action="store_true", help="haftalık liste çalışması")
    c.add_argument("--results", action="store_true", help="haftalık sonuç çalışması")
    c.set_defaults(func=cmd_check)

    rd = sub.add_parser("results-due", help="sonucu alınacak bitmiş maçların linklerini yazar")
    rd.add_argument("out")
    rd.set_defaults(func=cmd_results_due)

    mr = sub.add_parser("merge-results", help="historic çıktısındaki skorları results/ altına yazar")
    mr.add_argument("results_json")
    mr.set_defaults(func=cmd_merge_results)

    s = sub.add_parser("schedule", help="workflow dosyasındaki maç slot cron'larını yazar")
    s.add_argument("workflow")
    s.set_defaults(func=cmd_schedule)

    u = sub.add_parser("update-plan")
    u.add_argument("links_csv")
    u.set_defaults(func=cmd_update_plan)

    d = sub.add_parser("due")
    d.add_argument("out")
    d.set_defaults(func=cmd_due)

    m = sub.add_parser("merge")
    m.add_argument("links")
    m.add_argument("work_dir")
    m.set_defaults(func=cmd_merge)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
