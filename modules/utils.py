import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
import re
import difflib

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR"
MENU_POOL_SHEET_NAME = "YEMEK_HAVUZU"
SHEET_YATILI = "OGRENCI_YATILI"
SHEET_GUNDUZLU = "OGRENCI_GUNDUZLU"

# --- GÜVENLİK ---
def check_password():
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        st.text_input("🔑 Erişim Şifresi:", type="password", on_change=password_entered, key="password")
        return False
    return True

# --- BAĞLANTILAR ---
def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        return gspread.authorize(creds)
    except Exception as e: return None

def get_drive_service():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), ['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=creds)
    except: return None

# --- TEMİZLİK VE DÜZENLEME ---
def clean_number(num_str):
    try:
        clean = re.sub(r'[^\d.,-]', '', str(num_str))
        if not clean: return 0.0
        if clean.count('.') > 1 or clean.count(',') > 1: clean = clean.replace('.', '').replace(',', '.')
        elif ',' in clean and '.' not in clean: clean = clean.replace(',', '.')
        elif ',' in clean and '.' in clean:
             if clean.find(',') < clean.find('.'): clean = clean.replace(',', '')
             else: clean = clean.replace('.', '').replace(',', '.')
        return float(clean)
    except: return 0.0

def turkish_lower(text):
    if not text: return ""
    return text.replace('İ', 'i').replace('I', 'ı').lower().strip()

def standardize_name(text):
    if not text or len(text.strip()) < 2: return "Genel"
    cleaned = text.replace("*", "").replace("-", "").strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.6):
    if not ocr_text: return None
    ocr_key = turkish_lower(ocr_text)
    db_keys = [turkish_lower(p) for p in db_list]
    matches = difflib.get_close_matches(ocr_key, db_keys, n=1, cutoff=cutoff)
    if matches: return db_list[db_keys.index(matches[0])]
    return None

def get_or_create_worksheet(sh, title, cols, header):
    try:
        for ws in sh.worksheets():
            if turkish_lower(ws.title) == turkish_lower(title): return ws
        ws = sh.add_worksheet(title=title, rows=1000, cols=cols)
        ws.append_row(header)
        return ws
    except Exception as e:
        if "already exists" in str(e): return sh.worksheet(title)
        return None

# --- İSİM ÇÖZÜCÜLER (HEM İRSALİYE HEM FATURA KULLANIR) ---
def resolve_company_name(ocr_name, client, known_companies=None):
    std_name = standardize_name(ocr_name)
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(SETTINGS_SHEET_NAME)
            data = ws.get_all_values()
            alias_map = {}
            for row in data[1:]:
                if len(row) >= 2: alias_map[turkish_lower(row[0]).strip()] = row[1].strip()
            
            key = turkish_lower(std_name)
            if key in alias_map: return alias_map[key]
            for k, v in alias_map.items():
                if k in key: return v
            best = find_best_match(std_name, list(alias_map.keys()), cutoff=0.7)
            if best: return alias_map[turkish_lower(best)]
        except: pass
    except: pass
    
    if known_companies:
        best_db = find_best_match(std_name, known_companies, cutoff=0.6)
        if best_db: return best_db
    return std_name

def resolve_product_name(ocr_prod, client):
    clean_prod = ocr_prod.replace("*", "").strip()
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(SETTINGS_SHEET_NAME)
        except: return clean_prod
        data = ws.get_all_values()
        product_map = {}
        for row in data[1:]:
            if len(row) >= 4:
                if row[2] and row[3]: product_map[turkish_lower(row[2])] = row[3].strip()
        key = turkish_lower(clean_prod)
        if key in product_map: return product_map[key]
        for k, v in product_map.items():
            if k in key: return v
        best = find_best_match(clean_prod, list(product_map.keys()), cutoff=0.85)
        if best: return product_map[turkish_lower(best)]
        return clean_prod
    except: return clean_prod

# --- VERİTABANI ÇEKİCİ ---
def get_price_database(client):
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        data = ws.get_all_values()
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 3:
                ted = standardize_name(row[0])
                urn = row[1].strip()
                fyt = clean_number(row[2])
                kota = clean_number(row[5]) if len(row) >= 6 else 0.0
                kb = row[6].strip() if len(row) >= 7 else ""
                if ted not in price_db: price_db[ted] = {}
                price_db[ted][urn] = {"fiyat": fyt, "kota": kota, "birim": kb, "row": idx + 1}
        return price_db
    except: return {}
