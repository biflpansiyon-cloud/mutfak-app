import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
import re
import difflib
import requests
from datetime import datetime
import pandas as pd

# =========================================================
# 📂 DOSYA İSİMLERİ 
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
# 🔐 BAĞLANTILAR (Kısmen Mevcut Koddan alındı)
# =========================================================

# NOT: check_password, get_gspread_client, get_drive_service, fetch_google_models,
#      move_and_rename_file_in_drive, get_or_create_worksheet gibi temel fonksiyonların 
#      değişmediği varsayılmıştır. Yalnızca kritik olanlar buraya eklenecektir.

def get_gspread_client():
    try:
        # Streamlit secrets'tan Google Sheets kimlik bilgilerini yükle
        creds_json = st.secrets["gcp_service_account"]
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"Google Sheets bağlantı hatası: {e}")
        return None

def get_or_create_worksheet(sh, title, cols=10, headers=[]):
    try:
        ws = sh.worksheet(title)
        # Başlıkları kontrol et ve ekle
        if headers and not ws.row_values(1):
             ws.update([headers], 'A1')
        return ws
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=cols)
        if headers:
             ws.update([headers], 'A1')
        return ws

def clean_number(value):
    """Sayıları temizler ve float'a çevirir."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Virgülleri noktaya çevir, binlik ayraçları kaldır, alfabetik olmayanları temizle
        cleaned = value.replace('.', '').replace(',', '.').strip()
        cleaned = re.sub(r'[^\d.]', '', cleaned)
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0

# =========================================================
# ✨ YENİ NORMALİZASYON VE EŞLEŞTİRME FONKSİYONLARI
# =========================================================

def turkish_lower(text):
    """
    Türkçe karakterlere, noktalama işaretlerine ve boşluklara karşı dayanıklı küçük harfe çevirme.
    """
    if not isinstance(text, str):
        text = str(text)
        
    # Türkçe uyumlu küçük harfe çevirme
    text = text.replace('İ', 'i').replace('I', 'ı')
    text = text.lower()
    
    # Gereksiz noktalama, binlik ayraç ve sembolleri kaldır
    # Sadece harfleri, sayıları ve boşlukları koru
    text = re.sub(r'[^\w\s]', '', text) 
    
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
        # st.error(f"Eşleştirme Sözlüğü Yükleme Hatası: {e}") # Hata ayıklama için geçici olarak kaldırıldı
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

def add_product_to_price_sheet(client, product_name, company_name, unit, initial_quota=0.0):
    """
    Yeni bir ürünü (faturası gelmemiş irsaliye kalemi) FIYAT_ANAHTARI sayfasına ekler.
    """
    try:
        sh = client.open(FILE_STOK)
        # FIYAT_ANAHTARI sayfasının başlıkları (7 sütun)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        
        today = datetime.now().strftime("%d.%m.%Y")
        
        new_row = [
            company_name,           # TEDARİKÇİ
            product_name,           # ÜRÜN ADI
            "0.00",                 # BİRİM FİYAT (Fatura gelmediği için şimdilik 0)
            "₺",                    # PARA BİRİMİ
            today,                  # GÜNCELLEME TARİHİ
            initial_quota,          # KALAN KOTA (İrsaliye ile gelen miktar)
            unit                    # KOTA BİRİMİ
        ]
        
        ws.append_row(new_row)
        return True
    except Exception as e:
        st.error(f"Fiyat Anahtarına Ekleme Hatası: {e}")
        return False
        
# =========================================================
# ⚙️ MEVCUT VE GÜNCELLENEN FONKSİYONLAR
# =========================================================

def get_company_list(client):
    """Mevcut tedarikçi listesini döndürür."""
    try:
        sh = client.open(FILE_STOK)
        # AYARLAR sayfasından 1. sütunu okuyarak firma listesini alır
        ws = sh.worksheet(SHEET_STOK_AYARLAR)
        col_values = ws.col_values(1)
        companies = [c.strip() for c in col_values[1:] if c.strip()]
        return sorted(list(set(companies)))
    except: return []


def get_price_database(client):
    """Fiyat Anahtarını (Stok ve Fiyatları) çeker."""
    price_db = {}
    try:
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        data = ws.get_all_values()
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 3:
                ted = row[0].strip()
                urn = row[1].strip()
                fyt = clean_number(row[2])
                kot = clean_number(row[5]) if len(row) >= 6 else 0.0
                birim = row[6].strip() if len(row) >= 7 else "ADET"
                
                # Tedarikçi bazında ürünleri ve detaylarını kaydet
                if ted not in price_db:
                    price_db[ted] = {}
                
                # Ürün adını anahtar olarak kullan
                price_db[ted][urn] = {
                    'price': fyt,
                    'quota': kot,
                    'unit': birim,
                    'row_num': idx + 1 # Güncelleme için satır numarasını tut
                }
        return price_db
    except Exception as e: 
        st.error(f"Fiyat Anahtarı Veritabanı Hatası: {e}")
        return {}


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
            
            # Bulanık eşleştirme yap
            best = find_best_match(clean_prod, company_products, cutoff=0.7) 
            if best: return best
            
        # C) Hiçbiri Yoksa, ham metni döndür (kullanıcı manuel düzeltecek)
        return clean_prod
    except: 
        return clean_prod 

# ... (Diğer utils fonksiyonları)
