# 📊 Menü Algoritması Analiz Raporu

**İncelenen Dosya:** `modules/menu.py`
**Tarih:** 26 Kasım 2024
**Hazırlayan:** Jules

## 1. Genel Bakış
`modules/menu.py` dosyası, Google Sheets tabanlı bir yemek havuzunu kullanarak, belirli kısıtlamalara (constraint) ve gurme kurallarına göre aylık yemek menüsü oluşturan kapsamlı bir algoritma içermektedir.

Sistem şu temel bileşenlerden oluşur:
*   **PoolAnalyzer:** Yemek havuzunu kategorize eder ve istatistik çıkarır.
*   **ConstraintManager:** Kısıtlamaları (fırın yasağı, protein dengesi vb.) yönetir ve sıkıdan gevşeğe doğru 4 seviyeli bir filtreleme planı sunar.
*   **GourmetScorer:** Aday yemekleri tat, doku, renk ve çeşitlilik uyumuna göre puanlar.
*   **DishSelector:** Filtreleme ve puanlama mantığını birleştirerek günün yemeğini seçer.

## 2. Tespit Edilen Hatalar (Bugs)

Kod incelemesi sonucunda mantıksal akışta ve kural yönetiminde şu sorunlar tespit edilmiştir:

### 🔴 Kritik: Hafta Sonu Kullanım Sayısı Hatası
**Durum:** Kullanıcı talebine göre hafta sonları öğle ve akşam yemeklerinin aynı olması ve bunun aylık limitten 2 adet düşmesi gerekmektedir.
**Hata:** Kodda (Satır 466 civarı) hafta sonu döngüsünde öğle yemeği seçildikten sonra `record_usage` çağrılıyor. Daha sonra bu yemekler akşam yemeği değişkenlerine kopyalanıyor (`a_ana = o_ana`) ancak bu kopyalanan akşam yemeği için **tekrar `record_usage` çağrılmıyor.**
**Sonuç:** Hafta sonları yemekler limitten 2 değil, **1 düşüyor**. Bu durum popüler yemeklerin ay içinde gereğinden fazla çıkmasına veya limitlerin yanlış hesaplanmasına yol açıyor.

### 🟡 Orta: Hafta Sonu Balık Günü Seçimi
**Durum:** Kullanıcı arayüzden "Cumartesi" veya "Pazar" gününü balık günü olarak seçebilir.
**Hata:** Hafta sonu blok yapısı (Satır 466), `plan_meal_set()` fonksiyonunu parametresiz çağırıyor. Hafta içi bloğunda `is_f` (balık günü mü?) kontrolü yapılırken, hafta sonu bloğunda bu kontrol atlanmış.
**Sonuç:** Eğer kullanıcı Cumartesi veya Pazar'ı balık günü seçerse, algoritma bunu görmezden geliyor ve rastgele bir ana yemek atıyor.

### 🟠 Düşük: Acil Durumda Fırın Kuralı İhlali Riski
**Durum:** `_emergency_selection` fonksiyonu, hiçbir aday bulunamadığında çağrılıyor.
**Risk:** Fonksiyon önce fırınsız yemekleri bulmaya çalışıyor. Ancak eğer o kategorideki (örn: ANA YEMEK) *tüm* adaylar fırın yemeğiyse (`non_oven` listesi boş kalırsa), fonksiyon `random.choice(candidates)` ile rastgele bir seçim yapıyor.
**Sonuç:** Çok düşük bir ihtimal de olsa, "Günde 1 Fırın" kuralı acil durumlarda delinebilir.

## 3. Geliştirme Önerileri (Improvements)

### 🛠️ Kod Yapısı ve Performans
1.  **Meta Veri Önbellekleme (Caching):** `get_dish_meta` fonksiyonu döngüler içinde sürekli çağrılıyor. Havuz yüklendiğinde bu metalar bir kez hesaplanıp nesne üzerinde saklanabilir. Bu işlem süresini kısaltacaktır.
2.  **String Sabitleri:** 'FIRIN', 'KIRMIZI', 'BAKLIYAT' gibi string değerler kodun içine dağılmış durumda. Bunlar dosyanın başında `CONSTANTS` olarak tanımlanmalı.
3.  **Global Değişken Yönetimi:** `OVEN_LOCKED` değişkeni döngü içinde `nonlocal` ile yönetiliyor. Bu yapı çalışsa da karmaşıklaşmaya müsait. Günlük durumu tutan bir `DailyContext` sınıfı veya sözlüğü daha temiz bir yapı sunabilir.

### 🧠 Algoritma Mantığı
1.  **Hafta Sonu Limit Düzeltmesi:** Hafta sonu bloğunda kopyalanan akşam yemekleri için de `record_usage` fonksiyonunun çağrılması gerekiyor.
2.  **Balık Günü Kontrolü:** Hafta sonu bloğuna da `day == fish_day` kontrolü eklenerek `plan_meal_set(is_fish_meal=is_f)` şeklinde çağrı yapılmalı.
3.  **Geriye Dönük Kontrol (Backtracking):** Mevcut algoritma "Açgözlü" (Greedy) çalışıyor; yani o an en iyisini seçip ilerliyor. Ayın sonuna gelindiğinde seçenekler tükenebiliyor (ZORUNLU seçimler artıyor). İleri versiyonlarda, çıkmaza girildiğinde bir önceki günü değiştirip tekrar deneyen basit bir backtracking mekanizması eklenebilir.

## 4. Sonuç
Algoritma genel hatlarıyla "Gurme" mantığını başarıyla uyguluyor. Özellikle kademeli (progressive) constraint gevşetme mantığı çok başarılı. Ancak **Hafta Sonu Limit Hatası** veri tutarlılığını bozduğu için öncelikli olarak düzeltilmelidir.

Onayınız durumunda yukarıdaki hataları (bug fix) uygulayıp kodu güncelleyebilirim.
