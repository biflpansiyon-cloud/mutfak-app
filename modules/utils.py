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

FILE_STOK = "Mutfak_Stok_SatinAlma"      
FILE_FINANS = "Mutfak_Ogrenci_Finans"    
FILE_MENU = "Mutfak_Menu_Planlama"       

# =========================================================
# 📑 SAYFA İSİMLERİ 
# =========================================================
SHEET_YATILI = "OGRENCI_YATILI"
SHEET_GUNDUZLU = "OGRENCI_GUNDUZLU"
SHEET_FINANS_AYARLAR = "FINANS_AYARLAR"

SHEET_STOK_AYARLAR = "AYARLAR" 
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
MENU_POOL_SHEET_NAME = "YEMEK_HAVUZU"
MAPPING_SHEET_NAME = "ESLESTIRME_SOZLUGU" # Eklendi

# =========================================================
# 🔐 BAĞLANTILAR VE GÜVENLİK
# =========================================================

def check_password():
    """Kullanıcı kimlik doğrulamasını yapar."""
    # Mevcut şifre kontrolü korunmuştur
    if st.session_state.get("authenticated", False): 
        return True
        
    with st.form("login_form"):
        st.subheader("🔒 Sisteme Giriş")
        # Güvenlik için şifreyi Streamlit secrets'tan çekiyoruz
        SECRET_PASSWORD = st.secrets.get("login_password", "1234") 
        password = st.text_input("Şifrenizi Girin:", type="password")
        
        if st.form_submit_button("Giriş Yap"):
            if password == SECRET_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Yanlış Şifre.")
                st.session_state["authenticated"] = False
    return False

def get_gspread_client():
    """Google Sheets bağlantı objesini döndürür."""
    try:
        creds_json = st.secrets["gcp_service_account"]
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        return None

def get_or_create_worksheet(sh, title, cols=10, headers=[]):
    """Belirtilen isimde bir çalışma sayfası bulur veya oluşturur."""
    try:
        ws = sh.worksheet(title)
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
    
    # Gereksiz karakterleri kaldır
    text = re.sub(r'[^\w\s]', '', text) 
    
    # Fazla boşlukları tek boşluğa indir
    return ' '.join(text.split()).strip()

def find_best_match(target, candidates, cutoff=0.7):
    """Bulanık eşleştirme (fuzzy matching) yapar."""
    if not candidates:
        return None
    
    normalized_candidates = {turkish_lower(c): c for c in candidates}
    normalized_target = turkish_lower(target)
    
    matches = difflib.get_close_matches(normalized_target, normalized_candidates.keys(), n=1, cutoff=cutoff)
    
    if matches:
        return normalized_candidates[matches[0]]
    
    return None

def get_mapping_database(client):
    """'ESLESTIRME_SOZLUGU' sayfasından eşleşmeleri çeker."""
    mapping_db = {}
    try:
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, MAPPING_SHEET_NAME, 2, ["OCR METNİ (Ham)", "STANDART ÜRÜN ADI"])
        data = ws.get_all_values()
        
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                ocr_key = turkish_lower(row[0].strip()) 
                std_value = row[1].strip()
                mapping_db[ocr_key] = std_value
        return mapping_db
    except Exception as e:
        return {}

def add_to_mapping(client, ocr_text, standard_product_name):
    """Yeni bir eşleşmeyi sözlüğe ekler."""
    try:
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, MAPPING_SHEET_NAME, 2, ["OCR METNİ (Ham)", "STANDART ÜRÜN ADI"])
        ws.append_row([ocr_text, standard_product_name])
        return True
    except: return False

def add_product_to_price_sheet(client, product_name, company_name, unit, initial_quota=0.0):
    """
    Yeni bir ürünü FIYAT_ANAHTARI sayfasına ekler (irsaliyeden borçlandırma için).
    """
    try:
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        
        today = datetime.now().strftime("%d.%m.%Y")
        
        new_row = [
            company_name, product_name, "0.00", "₺", today, initial_quota, unit                    
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
                
                if ted not in price_db:
                    price_db[ted] = {}
                
                price_db[ted][urn] = {
                    'price': fyt,
                    'quota': kot,
                    'unit': birim,
                    'row_num': idx + 1 
                }
        return price_db
    except Exception as e: 
        return {}


def resolve_product_name(ocr_prod, client, company_name):
    """
    Ürün adını sırayla 1) Eşleştirme Sözlüğü ve 2) Bulanık Eşleştirme kullanarak çözer.
    """
    
    clean_prod = ocr_prod.replace("*", "").strip()
    norm_prod = turkish_lower(clean_prod) 

    try:
        # A) Eşleştirme Sözlüğünde Ara
        mapping_db = get_mapping_database(client)
        if norm_prod in mapping_db:
            return mapping_db[norm_prod] 
        
        # B) Sözlükte Yoksa, Fiyat Veritabanında Bulanık Eşleştirme Yap
        price_db = get_price_database(client)
        if company_name in price_db:
            company_products = list(price_db[company_name].keys())
            best = find_best_match(clean_prod, company_products, cutoff=0.7) 
            if best: return best
            
        # C) Hiçbiri Yoksa, ham metni döndür (kullanıcı manuel düzeltecek)
        return clean_prod
    except: 
        return clean_prod
