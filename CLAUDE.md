# CLAUDE.md

Bu repo, hafta sonu (Cuma–Pazar, TR saati) Premier League, La Liga, Serie A ve Bundesliga
maçlarının oranlarını OddsPortal'dan GitHub Actions ile otomatik toplar. Kullanıcı Türkçe
konuşur; cevaplar Türkçe olmalı.

## Repo düzeni

| Yol | İçerik | Kim yazar |
|---|---|---|
| `data/<gün>/<lig>/<maç>.json` | Maçtan ~2 saat önce çekilen oranlar | bot |
| `results/<gün>/<lig>/<maç>.json` | Skor, devre skoru, kapanış 1X2 (Pazartesi) | bot |
| `state/plan.json` | Haftalık fikstür (link, lig, başlama saati) | bot |
| `state/index.jsonl` | maç linki → `data/` dosyası eşlemesi | bot |
| `kupon-planlari/` | Kuponun hangi saatlerde hazırlanacağı | Claude |
| `kuponlar/<gün>.md` | Hazırlanan kuponlar + Pazartesi değerlendirmesi | Claude |
| `scripts/planner.py`, `.github/workflows/weekend-odds.yml` | Toplama sistemi | — |

- `state/` ve workflow'daki `MAÇ SLOTLARI` bloğunu **elle düzenleme**; bot yönetir.
- Bot sık commit atar: commit'ten önce her zaman `git pull --rebase`.
- `<gün>` maçın **TR yerel** tarihidir; `kickoff_utc` UTC'dir.

## Oran dosyasının yapısı (`data/...`)

```
match_link, league, kickoff_utc, kickoff_local, scraped_at_utc, minutes_before_kickoff
periods.full_time / periods.1st_half   -> güncel oranlar, TÜM bookmaker'lar, geçmişsiz
history.full_time / history.1st_half   -> 1X2 ve Alt/Üst 2.5, oran geçmişli
community_votes                        -> OddsPortal kullanıcı oyları
```

Her periyot kaydında maç bilgisi (`home_team`, `away_team`, `match_date`, ...) ve
`<market>_market` anahtarları vardır (ör. `1x2_market`, `btts_market`,
`over_under_2_5_market`, `asian_handicap_-0_5_market`). Her market bir bookmaker satırı
listesidir:

- 1X2: `"1"`, `"X"`, `"2"` · KG: `btts_yes`, `btts_no` ·
  Alt/Üst: `odds_over`, `odds_under` · Asya: `handicap_team_1`, `handicap_team_2`
- Ortak: `bookmaker_name`, `period`, `submarket_name` (barem burada, ör. `Over/Under +2.5`),
  varsa `blocked_outcomes`
- Oranlar **string**dir; sayıya çevrilemeyen / bloke olanları atla.
- `history` satırlarında `odds_history_data`: sonuç başına
  `{odds_history: [{timestamp, odds}], opening_odds: {timestamp, odds}}`

Tam anahtar adlarından emin değilsen önce bir dosyayı açıp yapıya bak; varsayma.

## Kupon hazırlama (kullanıcı "… maçlarından iki maçlık skor kuponu" dediğinde)

1. `git pull --rebase`. `state/plan.json`'dan istenen saatteki maçları bul. `data/`'da
   dosyası **olmayan** maçı (henüz çekilmemiş) söyle ve dışarıda bırak.
2. **İki tahmin** hazırla; ikisi de **kod çalıştırılarak** hesaplanır, skor asla
   oranlara bakıp uydurulmaz.

### A) Model tahmini (saf hesap)
1. **Marjı ayıkla:** Pinnacle varsa onun oranları, yoksa bookmaker'ların medyanı.
   Her marketin olasılıklarını normalize et (toplam = 1).
2. **Beklenen goller:** Ev (λ_ev) ve deplasman (λ_dep) gol beklentisini, modelin verdiği
   olasılıkların piyasa olasılıklarına en iyi uyacağı şekilde bul (en küçük kareler):
   1X2 + en dengeli 2–3 Alt/Üst baremi + en dengeli 1–2 Asya baremi.
3. **Skor matrisi:** Dixon-Coles (bağımsız Poisson + düşük skor düzeltmesi, ρ ≈ −0,05),
   0–6 gol. 1. yarı için aynı işlemi `1st_half` oranlarıyla yap.
4. **Kontrol:** Matrisin toplamı ~1; modelin 1X2 ve 2.5 Üst olasılıkları piyasadakine
   birkaç puan içinde olmalı. Değilse hesapta hata vardır, düzeltmeden sunma.

### B) Model + yorum
- Web'de güncel haber ara: açıklanan ilk 11'ler, sakat/cezalı, rotasyon (yakında Avrupa
  maçı), motivasyon (küme düşme, hedefsizlik), derbi, hava/saha.
- Sadece **somut bir bilgi** varsa λ'ları düzelt; her düzeltmeyi **gerekçesi ve büyüklüğüyle**
  yaz (ör. "Kane kadroda yok: λ_ev 1,90 → 1,70"). Bilgi yoksa B = A; düzeltme uydurma.
- Düzeltmeler küçük tutulur (genelde λ başına ±0,25'ten az). Piyasa çoğu bilgiyi zaten
  fiyatlar; kadro haberi oranlar alındıktan sonra çıktıysa en değerli düzeltme budur.

### Sunum
Her maç için: λ'lar, en olası 5 skor (%), MS 1/X/2, 2.5 Üst, KG Var; A ve B yan yana.
Kupon için: önerilen iki skor, her birinin olasılığı ve **toplam tutma ihtimali**
(çarpım), bulunabiliyorsa doğru skor oranlarına göre beklenen getiri. Her seferinde
gerçekçi beklentiyi hatırlat: iki maçlık skor kuponu tipik olarak ~%1–2 tutar ve
uzun vadede beklenen getiri negatiftir.

### Kayıt
Kuponu sunduktan sonra **ilk maç başlamadan** `kuponlar/<gün>.md` dosyasına ekle ve
commit + push et: saat, maçlar, A ve B'nin λ'ları ve skor olasılıkları, B'deki
düzeltmeler ve gerekçeleri, önerilen kupon. (Önceden kaydedilmeyen tahmin değerlendirmeye
girmez.)

## Pazartesi değerlendirme (kullanıcı isterse)
`results/` geldikten sonra o haftanın `kuponlar/` kayıtlarını sonuçlarla karşılaştır ve
aynı dosyaya ekle: kupon tuttu mu, gerçekleşen skora A'nın ve B'nin verdiği olasılık,
log-loss (A vs B). Haftalar biriktikçe B'nin A'yı gerçekten geçip geçmediğini özetle;
geçmiyorsa kullanıcıya yorum katmayı bırakmayı öner.
