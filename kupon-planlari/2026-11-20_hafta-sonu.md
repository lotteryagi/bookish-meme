# Kupon hazırlama planı — 20–22 Kasım 2026

İki maçlık skor kuponu için ne zaman hazırlatılacağı. Saatler **TR (UTC+3)**.

Kural: kupondaki **en geç** maçın oranları repoya yazılmış, **ilk** maç henüz başlamamış olmalı.
Oranlar maçtan ~2 saat önce çekilir; cron gecikmesi + çekim süresiyle veri genelde maçtan
~1,5–1 saat önce hazır olur.

## Cuma 20.11

| Hazırlat | Maç havuzu | Son süre |
|---|---|---|
| **~21:30** | 22:30 Bundesliga · 23:00 La Liga | 22:30 |

## Cumartesi 21.11

| Hazırlat | Maç havuzu | Son süre |
|---|---|---|
| **~17:00** | 18:00 Premier League (5) · 18:15 La Liga | 18:00 |
| **~19:15** | 20:00 Serie A · 20:30 Bundesliga, La Liga, Premier League | 20:00 |
| **~21:30** | 22:45 Serie A · 23:00 La Liga | 22:45 |

17:30 Bundesliga (5 maç) da katılacaksa ~16:45'te hazırlatılmalı; 17:30 ve 18:00 slotları
üst üste çekildiği için veri son anda yetişebilir.

## Pazar 22.11

| Hazırlat | Maç havuzu | Son süre |
|---|---|---|
| **~16:30** | 17:00 Premier League, Serie A (2) · 17:30 Bundesliga · 18:15 La Liga | 17:00 |
| **~19:00** | 19:30 Bundesliga, Premier League · 20:00 Serie A · 20:30 La Liga | 19:30 |
| **~21:30** | 22:45 Serie A · 23:00 La Liga | 22:45 |

## Notlar

- Hazırlatma: Claude'a ör. *"Cumartesi 18:00 maçlarından iki maçlık skor kuponu"* yazılır;
  repodaki oranlardan skor olasılıkları hesaplanır. Verisi henüz gelmemiş maç dışarıda bırakılır.
- İlk 11'ler maçtan ~1 saat önce açıklanır; oranlar bundan önce alınmıştır. Sürpriz bir eksik
  varsa o maç kupondan çıkarılmalı.
- Beklenti: tek maçta en olası skor ~%10–13, iki maçlık kupon **~%1–2** tutar. Doğru skor
  marketinde bookmaker marjı yüksektir (~%15–30); uzun vadede beklenen getiri negatiftir.
  Sadece kaybetmeyi göze alınabilecek küçük tutarlarla.
- Saatler 18.11 tarihli plana (`state/plan.json`) göredir; maç saati değişirse geçersiz olur.
