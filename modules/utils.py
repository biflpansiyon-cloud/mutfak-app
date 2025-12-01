import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
import re
import difflib
import requests

# =========================================================
# 📂 DOSYA İSİMLERİ (Mevcut)
# =========================================================

FILE_STOK = "Mutfak_Stok_SatinAlma"      # Fatura/İrsaliye
FILE_FINANS = "Mutfak_Ogrenci_Finans"    # Öğrenci İşleri
FILE_MENU = "Mutfak_Menu_Planlama"       # Yemek Menüsü

# =========================================================
# 📑 SAYFA İSİMLERİ (Mevcut ve Yeni Eklendi)
# =========================================================
SHEET_YATILI = "OGRENCI_YATILI"
SHEET_GUNDUZLU = "OGRENCI_GUNDUZLU"
SHEET_FINANS_AYARLAR = "FINANS_AYARLAR"

SHEET_STOK_AYARLAR = "AYARLAR" 
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
MENU_POOL_SHEET_NAME = "YEMEK_HAVUZU"
MAPPING_SHEET_NAME = "ESLESTIRME_SOZLUGU" # <--- YENİ EKLENDİ

# =========================================================
# 🔐 BAĞLANTILAR
# =========================================================

# ... (check_password, get_gspread_client, get_drive_service, get_or_create_worksheet fonksiyonları değişmedi)
# ... (fetch_google_models, clean_number fonksiyonları değişmedi)

# =========================================================
# ✨ YENİ NORMALİZASYON VE EŞLEŞTİRME FONKSİYONLARI
# =========================================================

def turkish_lower(text):
    """
    Türkçe karakterlere, noktalama işaretlerine ve boşluklara karşı dayanıklı küçük harfe çevirme.
    Bu, eşleştirme sözlüğü ve bulanık eşleştirme için kritik öneme sahiptir.
    """
    if not isinstance(text, str):
        text = str(text)
        
    # Türkçe uyumlu küçük harfe çevirme
    text = text.replace('İ', 'i').replace('I', 'ı')
    text = text.lower()
    
    # Gereksiz noktalama, binlik ayraç ve sembolleri kaldır
    text = re.sub(r'[^\w\s]', '', text) # Alfabetik olmayan karakterleri ve boşlukları koru
    
    # Fazla boşlukları tek boşluğa indir ve baş/son boşlukları kaldır
    return ' '.join(text.split()).strip()

def find_best_match(target, candidates, cutoff=0.7):
    """Mevcut bulanık eşleştirme fonksiyonunuz."""
    if not candidates:
        return None
    
    # Adayları turkish_lower ile normalleştir
    normalized_candidates = {turkish_lower(c): c for c in candidates}
    
    # Hedefi turkish_lower ile normalleştir
    normalized_target = turkish_lower(target)
    
    matches = difflib.get_close_matches(normalized_target, normalized_candidates.keys(), n=1, cutoff=cutoff)
    
    if matches:
        # Normalleştirilmiş anahtardan orijinal aday ismi bul ve döndür
        return normalized_candidates[matches[0]]
    
    return None

def get_mapping_database(client):
    """
    'ESLESTIRME_SOZLUGU' sayfasından (OCR Metni -> Standart Ürün Adı) haritasını çeker.
    Anahtarlar (Key) normalleştirilmiş haldedir.
    """
    mapping_db = {}
    try:
        sh = client.open(FILE_STOK)
        # Eğer sayfa yoksa, otomatik oluştur
        ws = get_or_create_worksheet(sh, MAPPING_SHEET_NAME, 2, ["OCR METNİ (Ham)", "STANDART ÜRÜN ADI"])
        data = ws.get_all_values()
        
        # İlk satırı atla (başlıklar)
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                # Ham OCR metnini normalleştirerek anahtar yapıyoruz
                ocr_key = turkish_lower(row[0].strip()) 
                std_value = row[1].strip()
                mapping_db[ocr_key] = std_value
        return mapping_db
    except Exception as e:
        # Hata durumunda boş sözlük döndür
        st.error(f"Eşleştirme Sözlüğü Yükleme Hatası: {e}")
        return {}

def add_to_mapping(client, ocr_text, standard_product_name):
    """Yeni bir eşleşmeyi sözlüğe ekler."""
    try:
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, MAPPING_SHEET_NAME, 2, ["OCR METNİ (Ham)", "STANDART ÜRÜN ADI"])
        # Eşleşmeyi direkt olarak, ham metin ve standart ürün adı olarak ekle
        ws.append_row([ocr_text, standard_product_name])
        return True
    except: return False


# =========================================================
# ⚙️ GÜNCELLENEN resolve_product_name
# =========================================================

def resolve_product_name(ocr_prod, client, company_name):
    """
    Ürün adını sırayla 1) Eşleştirme Sözlüğü ve 2) Bulanık Eşleştirme kullanarak çözer.
    """
    
    # 1. Normalleştirme
    clean_prod = ocr_prod.replace("*", "").strip()
    norm_prod = turkish_lower(clean_prod) # Anahtar olarak kullanılacak normalleştirilmiş metin

    try:
        # A) Eşleştirme Sözlüğünde Ara
        mapping_db = get_mapping_database(client)
        if norm_prod in mapping_db:
            return mapping_db[norm_prod] # Direkt standart ismi döndür
        
        # B) Sözlükte Yoksa, Fiyat Veritabanında Bulanık Eşleştirme Yap
        price_db = get_price_database(client)
        if company_name in price_db:
            # Sadece ilgili firmanın ürünlerini al
            company_products = list(price_db[company_name].keys())
            
            # Bulanık eşleştirme yap (find_best_match artık içeride turkish_lower kullanıyor)
            best = find_best_match(clean_prod, company_products, cutoff=0.7) 
            if best: return best
            
        # C) Hiçbiri Yoksa, ham metni döndür (kullanıcı manuel düzeltecek)
        return clean_prod
    except: 
        return clean_prod 

# ... (get_company_list ve get_price_database fonksiyonları değişmedi)
