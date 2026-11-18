# Hatırlatmalar

Claude: bir oturumun başında bugünün tarihini kontrol et; tarihi gelmiş ve `[ ]` olan maddeleri
kullanıcıya hatırlat. Yapılınca `[x]` ve yapılış tarihini yaz.

| Tarih | Durum | Ne yapılacak |
|---|---|---|
| 2026-11-23 | [ ] | **İlk hafta sonu değerlendirmesi:** `data/`, `kuponlar/`, `results/` oluştu mu; Actions ve bulut görevlerinde hata var mı. Gerçek çekim süresi ve dosya boyutu. Kaçan maç var mı → kupon görevlerini **saatte bir** çalışmaya geçirme kararı. |
| (token bitiş tarihi − 7 gün) | [ ] | **`WORKFLOW_PAT` yenile:** yeni fine-grained token (bookish-meme; Contents + Workflows: Read and write), secret'ı güncelle. Bitiş tarihi henüz yazılmadı — öğrenince bu satıra koy. |
| (proxy bitiş tarihi − 7 gün) | [ ] | **Proxy aboneliğini yenile**, değişirse `PROXY_URL` secret'ını güncelle. Bitiş tarihi henüz yazılmadı. |
| 2027-03-22 | [ ] | **Yaz saati (Avrupa 28 Mart 2027):** maçlar TR saatiyle 1 saat erkene kayar. Kupon görevlerinin saatlerini (claude.ai/code/routines) yeni maç saatlerine göre kaydır — saatte bire geçildiyse gerek yok. |
| 2027-05-31 | [ ] | **Yaz arası:** ligler bitti. Kupon ve değerlendirme görevlerini duraklat (boşa kullanım). |
| 2027-08-09 | [ ] | **Yeni sezon:** görevleri tekrar aç, ilk Perşembe planını kontrol et; OddsPortal değiştiyse `ODDSHARVESTER_VERSION` yükselt. |
| 2027-10-25 | [ ] | **Kış saati (Avrupa 31 Ekim 2027):** maçlar TR saatiyle 1 saat geri gelir; kupon saatlerini geri al. |
