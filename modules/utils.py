import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
import re
import difflib
import requests
from datetime import datetime # YENİ EKLENDİ
import pandas as pd # YENİ EKLENDİ

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
MAPPING_SHEET_NAME = "ESLESTIRME_SOZLUGU" # YENİ EKLENDİ

# =========================================================
# 🔐 BAĞLANTILAR
# =========================================================

def check_password():
    if st.session_state.get("authenticated", False): return True
    with st.form("login_form"):
        st.subheader("🔒 Sisteme Giriş")
        password = st.text_input("Şifrenizi Girin:", type="password")
        if st.form_submit_button("Giriş Yap"):
            if password == st.secrets.get("APP_PASSWORD", "admin"): 
                st.session_state["authenticated"] = True
                st.rerun()
                return True
            else: st.error("Yanlış şifre.")
    return False

def get_gspread_client():
    try:
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Sheets Bağlantı Hatası: {e}")
        return None

def get_drive_service():
    scope = ['https://www.googleapis.com/auth/drive']
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return build('drive', 'v3', credentials=creds)
    except Exception as e: return None

def find_folder_id(service, folder_name, parent_id=None):
    try:
        query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and trashed=false"
        if parent_id: query += f" and '{parent_id}' in parents"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get('files', [])
        if files: return files[0]['id']
        return None
    except: return None

def fetch_google_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        res = requests.get(url)
        if res.status_code == 200:
            data = res.json()
            return sorted([m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']])
        return []
    except: return []

# =========================================================
# 🛠️ YARDIMCI ARAÇLAR VE YENİ EŞLEŞTİRME FONKSİYONLARI
# =========================================================

def clean_number(num_str):
    if not num_str: return 0.0
    clean = re.sub(r'[^\d.,-]', '', str(num_str))
    try:
        if '.' in clean and ',' in clean: clean = clean.replace('.', '').replace(',', '.')
        elif ',' in clean and '.' not in clean: clean = clean.replace(',', '.')
        elif '.' in clean and ',' not in clean:
            parts = clean.split('.')
            if len(parts) == 2 and len(parts[1]) == 3: clean = clean.replace('.', '') 
        return float(clean)
    except: return 0.0

def turkish_lower(text):
    """
    Türkçe karakterlere, noktalama işaretlerine ve boşluklara karşı dayanıklı küçük harfe çevirme.
    """
    if not isinstance(text, str):
        text = str(text)
        
    text = text.replace('İ', 'i').replace('I', 'ı')
    text = text.lower()
    
    text = re.sub(r'[^\w\s]', '', text) 
    
    return ' '.join(text.split()).strip()

def standardize_name(text):
    if not text or len(text.strip()) < 2: return "Genel"
    cleaned = text.replace("*", "").replace("-", "").strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.7):
    """Bulanık eşleştirme (fuzzy matching) yapar. Cutoff 0.7'ye yükseltildi."""
    if not db_list: return None
    
    normalized_candidates = {turkish_lower(c): c for c in db_list}
    normalized_target = turkish_lower(ocr_text)
    
    matches = difflib.get_close_matches(normalized_target, normalized_candidates.keys(), n=1, cutoff=cutoff)
    
    if matches:
        return normalized_candidates[matches[0]]
    
    return None

def get_or_create_worksheet(sh, title, cols, header):
    try:
        for ws in sh.worksheets():
            if turkish_lower(ws.title) == turkish_lower(title): return ws
        ws = sh.add_worksheet(title=title, rows=1000, cols=cols)
        if header: ws.append_row(header)
        return ws
    except Exception as e:
        if "already exists" in str(e): return sh.worksheet(title)
        return None

def get_mapping_database(client):
    """'ESLESTIRME_SOZLUGU' sayfasından (OCR Metni -> Standart Ürün Adı) haritasını çeker."""
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
    """Yeni bir ürünü (faturası gelmemiş irsaliye kalemi) FIYAT_ANAHTARI sayfasına ekler."""
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
        return False
        
# =========================================================
# 🏢 FİRMA VE STOK İŞLEMLERİ
# =========================================================

def get_company_list(client):
    # (Mevcut fonksiyonunuz)
    try:
        sh = client.open(FILE_STOK)
        try: ws = sh.worksheet(SHEET_STOK_AYARLAR)
        except: 
            ws = sh.add_worksheet(SHEET_STOK_AYARLAR, 100, 2)
            ws.update_cell(1, 1, "FİRMA LİSTESİ")
            return []
        col_values = ws.col_values(1)
        companies = [c.strip() for c in col_values[1:] if c.strip()]
        return sorted(list(set(companies)))
    except: return []

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

def get_price_database(client):
    # (Mevcut fonksiyonunuz, veri çekme mantığı korundu)
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
                kota = clean_number(row[5]) if len(row) >= 6 else 0.0
                kb = row[6].strip() if len(row) >= 7 else ""
                if ted not in price_db: price_db[ted] = {}
                # Anahtarların isimleri değiştirildi: fiyat->fiyat, kota->kota, row->row
                price_db[ted][urn] = {"fiyat": fyt, "kota": kota, "birim": kb, "row": idx + 1}
        return price_db
    except: return {}
