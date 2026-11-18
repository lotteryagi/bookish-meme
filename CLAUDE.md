# CLAUDE.md

Bu repo, hafta sonu (Cuma–Pazar, TR saati) Premier League, La Liga, Serie A ve Bundesliga
maçlarının oranlarını OddsPortal'dan GitHub Actions ile otomatik toplar. Kullanıcı Türkçe
konuşur; cevaplar Türkçe olmalı.

**Her oturumun başında `HATIRLATMALAR.md`'ye bak:** tarihi gelmiş açık madde varsa
kullanıcıya hatırlat (zamanlanmış bulut görevlerinde değil, kullanıcıyla konuşulan
oturumlarda).

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
| `scripts/predict.py` | Oranlardan skor olasılıkları (kupon hesabı) | — |
| `scripts/backtest.py`, `.github/workflows/backtest.yml`, `backtest/` | Geçmiş sezonlarla test + `RAPOR.md` | bot (elle tetiklenir) |

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
2. **İki tahmin** hazırla. Hesap **her zaman `scripts/predict.py` ile** yapılır; modeli
   yeniden yazma, skoru oranlara bakıp uydurma. Betik hata verirse düzeltip testle,
   sonra kullan.

### A) Model tahmini (saf hesap)
```bash
python scripts/predict.py match data/<gün>/<lig>/<maç>.json --json /tmp/p_<maç>.json
```
Betik: marjı ayıklar (Pinnacle, yoksa medyan), 1X2 + Alt/Üst + Asya baremlerine en iyi uyan
λ_ev / λ_dep'i bulur, Dixon-Coles (ρ = −0,05) skor matrisini çıkarır; maç sonu ve ilk yarı.
Çıktıdaki **kontrol** `UYARI` ise (model piyasadan >3 puan sapıyor) o maçı kupona koyma ve
nedenini yaz. Ayarların gerekçesi: `backtest/RAPOR.md` (geçmiş sezonlarla test).

### B) Model + yorum
- Web'de güncel haber ara: açıklanan ilk 11'ler, sakat/cezalı, rotasyon (yakında Avrupa
  maçı), motivasyon (küme düşme, hedefsizlik), derbi, hava/saha.
- Sadece **somut bir bilgi** varsa λ'ları düzelt; her düzeltmeyi **gerekçesi ve büyüklüğüyle**
  yaz. Bilgi yoksa B = A; düzeltme uydurma. Düzeltme betiğe verilir:
  ```bash
  python scripts/predict.py match <dosya> --delta-home -0.20 --note "Kane kadroda yok" --json /tmp/p_<maç>.json
  ```
- Düzeltmeler küçük tutulur (genelde λ başına ±0,25'ten az). Piyasa çoğu bilgiyi zaten
  fiyatlar; kadro haberi oranlar alındıktan sonra çıktıysa en değerli düzeltme budur.

### Kupon seçimi
```bash
python scripts/predict.py coupon /tmp/p_*.json            # B'ye göre (varsayılan)
python scripts/predict.py coupon /tmp/p_*.json --use A    # karşılaştırma için
```
Kontrolü geçen maçlardan, en olası skorlarının birlikte tutma ihtimali en yüksek ikiliyi seçer.

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
log-loss (A vs B). Olasılıklar kuponla birlikte kaydedilen değerlerden alınır (sonradan
yeniden hesaplanmaz). Haftalar biriktikçe B'nin A'yı gerçekten geçip geçmediğini özetle;
geçmiyorsa kullanıcıya yorum katmayı bırakmayı öner.

## Kör test (Claude'un kendi bilgisi, geçmiş maçlar)
Sadece Claude'un bilgi kesim tarihinden (Haziran 2026) **sonra** oynanan maçlarda geçerlidir.
Web araması **yok**; `backtest/data/*.jsonl` dosyaları skor içerdiği için **okunmaz**.
1. `python scripts/backtest.py --blind backtest/kor/liste.csv` → oranları görmeden her maç
   için λ_ev, λ_dep yaz: `backtest/kor/C.csv` (`match_link,lambda_home,lambda_away,gerekce`).
   **Commit + push.**
2. `python scripts/backtest.py --blind backtest/kor/liste_A.csv --with-a` → A'yı görerek
   kendi bilginle düzeltme: `backtest/kor/B.csv` (`match_link,delta_home,delta_away,gerekce`;
   bilgi yoksa 0). **Commit + push.**
3. Ancak sonra `python scripts/backtest.py` → `RAPOR.md` A/B/C karşılaştırması. Commit + push.
