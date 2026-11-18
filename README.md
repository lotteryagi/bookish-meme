# Hafta sonu oranları — OddsHarvester + GitHub Actions

Cuma, Cumartesi ve Pazar günleri Premier League, Şampiyonlar Ligi, La Liga, Serie A ve
Bundesliga maçlarının oranlarını, maç başlamadan **2 saat önce** çeker.

## Kurulum
1. Bu klasörü bir GitHub reposu olarak gönderin.
2. **Settings → Secrets and variables → Actions** altına secret'ları ekleyin:
   | Secret | Zorunlu | Örnek |
   |---|---|---|
   | `PROXY_URL` | evet | `http://user:pass@host:port` (birden fazla için boşlukla ayırın) |
   | `PROXY_USER` | hayır | tek proxy, kimlik bilgisi URL'de değilse |
   | `PROXY_PASS` | hayır | aynı |
   | `WORKFLOW_PAT` | evet | workflow dosyasını güncelleyebilen token (aşağıda) |
3. **`WORKFLOW_PAT` oluşturma:** GitHub → Settings → Developer settings → Personal access tokens →
   *Fine-grained tokens* → yalnızca bu repo; izinler: **Contents: Read and write**,
   **Workflows: Read and write**. (GitHub'ın kendi `GITHUB_TOKEN`'ı `.github/workflows`
   altındaki dosyaları değiştiremediği için gerekli.) Token'ın bitiş tarihini not edin.
4. **Settings → Actions → General → Workflow permissions**: "Read and write" seçin.
5. Test için Actions sekmesinden workflow'u `force` + `force_plan` ile elle çalıştırın.

## Nasıl çalışır
- **Haftada bir liste:** Perşembe 09:00 TR'de (`0 6 * * 4` UTC) fikstür `--links-only` ile
  çekilir, `state/plan.json` yazılır ve Cuma/Cmt/Pazar maçlarının saatlerine göre workflow
  dosyasındaki `MAÇ SLOTLARI` bloğuna cron satırları yazılıp commit edilir.
- **Sadece gereken anlarda çalışma:** Her farklı başlama saati için maçtan 2 saat önce bir
  çalışma, ayrıca GitHub bir zamanlanmış çalışmayı atlarsa diye 45 dk sonra bir yedek
  (`BACKUP_MINUTES`). Yedek, maç zaten çekildiyse ~30 sn'de hiçbir şey yapmadan biter.
  Tipik hafta sonu: ~10–15 farklı saat → ~20–30 kısa çalışma.
- Liste haftada bir çıktığı için Perşembe'den sonra saati değişen / ertelenen maçlar kaçabilir;
  gerekirse Actions'tan `force_plan` ile elle yeniden çıkarın.
- Zamanı gelen maçlar için (skor tahmini için hafif ayar; maç başı ~10–15 dk):
  - **1. geçiş, güncel oranlar:** 1X2, KG Var/Yok, tüm Alt/Üst baremleri, tüm Asya
    handikapları — tüm bookmaker'lar, stadyum yerel saati, topluluk oy dağılımı
  - **2. geçiş, oran geçmişi** (açılış oranı + hareketler): sadece 1X2 ve Alt/Üst 2.5
  - Periyotlar: maç sonucu ve 1. yarı
- Her maç **`data/<maç-günü>/<lig>/<maç>.json`** olarak bir kez yazılır ve repoya commit edilir.
  Ham çıktılar 14 gün artifact olarak da saklanır.
- **Pazartesi 09:00 TR (`0 6 * * 1` UTC) sonuçlar:** Başlamasından 3 saat geçmiş ve skoru
  alınmamış maçların skoru (ilk yarı dahil) + kapanış 1X2 oranları çekilir,
  **`results/<maç-günü>/<lig>/<maç>.json`** olarak yazılır. Ertelenen maçlar 21 gün boyunca
  sonraki Pazartesilerde tekrar denenir. Elle: Actions → `results`.

## Veri formatı
Düz JSON, girintisiz (boşluksuz) yazılır. Oran dosyası ile sonuç dosyası aynı yolu paylaşır
(`data/...` ↔ `results/...`). Okumak için:
```bash
python -m json.tool data/2026-11-21/england-premier-league/arsenal-chelsea-AbC123.json
```
```python
import json, glob
from pathlib import Path
for p in sorted(glob.glob("data/**/*.json", recursive=True)):
    oran = json.load(open(p))
    r = Path("results", *Path(p).parts[1:])
    sonuc = json.load(open(r)) if r.exists() else None   # henüz yoksa None
```

Oran kaydı (`data/...`):
```json
{ "match_link": "...", "league": "...", "kickoff_utc": "...", "kickoff_local": "...",
  "scraped_at_utc": "...", "minutes_before_kickoff": 112,
  "periods": { "full_time": {...}, "1st_half": {...} },
  "history": { "full_time": {...}, "1st_half": {...} },
  "community_votes": {...} }
```
Sonuç kaydı (`results/...`):
```json
{ "match_link": "...", "home_team": "...", "away_team": "...",
  "home_score": "2", "away_score": "1", "partial_results": "...",
  "fetched_at_utc": "...", "odds_file": "data/...", "record": {...} }
```

## Durum dosyaları (`state/`)
`plan.json` haftalık liste · `scraped.txt` oranı alınan maçlar · `index.jsonl` maç → dosya
eşlemesi · `results_done.txt` skoru alınan maçlar. Elle düzenlemeyin.

## Ayarlar (workflow içindeki `env`)
`MARKETS` (geçmişsiz), `HISTORY_MARKETS` (geçmişli), `PERIODS`, `WINDOW_HOURS`,
`BACKUP_MINUTES`, `LEAGUES`, `OH_CONCURRENCY`, `OH_TIMEZONE` vb.

## Tahmin ve geriye dönük test
- `python scripts/predict.py match data/<gün>/<lig>/<maç>.json` — oranlardan skor
  olasılıkları (Dixon-Coles); `coupon` ile iki maçlık kupon. Kupon görevleri bunu kullanır.
- **Geriye dönük test:** Actions → "Geriye dönük test" → Run workflow (sezon/lig seçilebilir).
  Geçmiş maçların kapanış oranları + skorları çekilir, `backtest/RAPOR.md` yazılır.
  Hafta sonu oran çekimiyle aynı proxy'yi kullandığı için Cuma–Pazar dışında çalıştırın.
