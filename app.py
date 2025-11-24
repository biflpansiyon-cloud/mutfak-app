import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import difflib

st.set_page_config(page_title="Mutfak ERP Pro", page_icon="👔", layout="wide")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR" # Yeni sözlüğümüz

# --- GOOGLE SHEETS BAĞLANTISI ---
def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client, creds_dict.get("client_email")
    except Exception as e:
        return None, str(e)

# --- YARDIMCI FONKSİYONLAR ---
def fetch_google_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            return [m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']]
        return []
    except: return []

def clean_number(num_str):
    try:
        clean = ''.join(c for c in num_str if c.isdigit() or c in [',', '.'])
        clean = clean.replace(',', '.')
        return float(clean)
    except: return 0.0

def standardize_name(text):
    if not text or len(text.strip()) < 2: return "Genel"
    cleaned = text.strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.6):
    if not ocr_text: return None
    matches = difflib.get_close_matches(ocr_text.lower(), [p.lower() for p in db_list], n=1, cutoff=cutoff)
    if matches:
        matched_lower = matches[0]
        for original in db_list:
            if original.lower() == matched_lower: return original
    return None

# --- YENİ: İSİM ÇEVİRİCİ ---
def resolve_company_name(ocr_name, client):
    """
    Faturadaki 'Ahmet Yılmaz'ı 'Yılmaz Gıda'ya çevirir.
    AYARLAR sekmesinden veriyi çeker.
    """
    std_name = standardize_name(ocr_name)
    
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(SETTINGS_SHEET_NAME)
            data = ws.get_all_values()
        except gspread.WorksheetNotFound:
            return std_name # Ayarlar sekmesi yoksa olduğu gibi dön
        
        # Sözlüğü oluştur: { "ahmet yılmaz": "Yılmaz Gıda" }
        alias_map = {}
        for row in data[1:]: # Başlığı atla
            if len(row) >= 2:
                official_name = row[0].strip().lower()
                system_name = row[1].strip() # Büyük/küçük harfi koru (Sistem adı bu)
                alias_map[official_name] = system_name
        
        # Şimdi kontrol et
        if std_name.lower() in alias_map:
            return alias_map[std_name.lower()]
        
        # Tam eşleşme yoksa benzerlik ara
        best_match = find_best_match(std_name, list(alias_map.keys()), cutoff=0.7)
        if best_match:
            return alias_map[best_match.lower()]
            
        return std_name # Eşleşme yoksa orijinalini kullan
        
    except Exception:
        return std_name

def get_price_database(client):
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(PRICE_SHEET_NAME)
        except gspread.WorksheetNotFound: return {}
        data = ws.get_all_values()
        for row in data[1:]:
            if len(row) >= 3:
                ted = standardize_name(row[0])
                urn = row[1].strip()
                fyt = clean_number(row[2])
                if ted not in price_db: price_db[ted] = {}
                price_db[ted][urn] = fyt
        return price_db
    except: return {}

# ==========================================
# MODÜL 1: İRSALİYE
# ==========================================
def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    İrsaliyeyi analiz et. Tedarikçi firmayı bul.
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def save_receipt_smart(raw_text):
    client, err = get_gspread_client()
    if not client: return False, err
    
    price_db = get_price_database(client)
    known_companies = list(price_db.keys())
    
    try:
        sh = client.open(SHEET_NAME)
        existing_sheets = {ws.title.strip().lower(): ws for ws in sh.worksheets()}
        firm_data = {}
        
        for line in raw_text.split('\n'):
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                
                # İsim Çeviriciyi Burada da Kullan (Garanti olsun)
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client)
                
                tarih, urun, miktar, fiyat, tutar = parts[1], parts[2], parts[3], parts[4], parts[5]
                f_val = clean_number(fiyat)
                
                # Fiyat Eşleştirme
                if f_val == 0 and final_firma in price_db:
                    prods = list(price_db[final_firma].keys())
                    match_prod = find_best_match(urun, prods, cutoff=0.7)
                    if match_prod:
                        f_val = price_db[final_firma][match_prod]
                        fiyat = str(f_val)
                        urun = f"{urun} ({match_prod})"
                        m_val = clean_number(miktar)
                        tutar = f"{m_val * f_val:.2f}"
                
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append([tarih, urun, miktar, fiyat, tutar])
        
        msg = []
        for firma, rows in firm_data.items():
            fn = firma.strip().lower()
            if fn in existing_sheets: ws = existing_sheets[fn]
            else:
                ws = sh.add_worksheet(title=firma, rows=1000, cols=10)
                ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
                existing_sheets[fn] = ws
            ws.append_rows(rows)
            msg.append(f"{firma}: {len(rows)}")
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)

# ==========================================
# MODÜL 2: FATURA MERKEZİ
# ==========================================
def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    pdf_bytes = uploaded_file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    FATURAYI analiz et.
    1. Tedarikçi Firmayı Bul (Kişi adıysa da bul).
    2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar.
    ÇIKTI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "application/pdf", "data": base64_pdf}}]}],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def update_price_list(raw_text):
    client, err = get_gspread_client()
    if not client: return False, err
    
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(PRICE_SHEET_NAME)
        except: 
            ws = sh.add_worksheet(title=PRICE_SHEET_NAME, rows=1000, cols=5)
            ws.append_row(["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "GÜNCELLEME TARİHİ"])
            
        existing_data = ws.get_all_values()
        product_map = {}
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                # Burada da ismi standartlaştırarak map'e atıyoruz
                k_firma = standardize_name(row[0])
                k_urun = row[1].strip().lower()
                product_map[f"{k_firma}|{k_urun}"] = idx + 1
        
        updates_batch, new_rows_batch = [], []
        cnt_upd, cnt_new = 0, 0
        
        lines = raw_text.split('\n')
        for line in lines:
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 3: parts.append("0")
                
                # --- İSİM DÖNÜŞTÜRME BURADA YAPILIYOR ---
                raw_supplier = parts[0]
                # Faturadaki 'Ahmet Yılmaz' -> 'Yılmaz Gıda'ya dönüşür
                target_supplier = resolve_company_name(raw_supplier, client)
                
                urun = parts[1].strip()
                fiyat = clean_number(parts[2])
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{target_supplier}|{urun.lower()}"
                
                if key in product_map:
                    row_idx = product_map[key]
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})
                    updates_batch.append({'range': f'D{row_idx}', 'values': [[bugun]]})
                    cnt_upd += 1
                else:
                    new_rows_batch.append([target_supplier, urun, fiyat, bugun])
                    cnt_new += 1
        
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        return True, f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi. (Firma: {target_supplier})"
    except Exception as e: return False, str(e)

# ==========================================
# UI
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP V8")
        page = st.radio("Menü", ["📝 Günlük İrsaliye", "🧾 Fatura & Fiyatlar"])
        st.divider()
        models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
        sel_model = st.selectbox("Yapay Zeka Modeli", models)

    if page == "📝 Günlük İrsaliye":
        st.header("📝 İrsaliye Girişi")
        f = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])
        if f:
            img = Image.open(f)
            st.image(img, width=300)
            if st.button("Analiz Et"):
                with st.spinner("Okunuyor..."):
                    s, r = analyze_receipt_image(img, sel_model)
                    st.session_state['res'] = r
            if 'res' in st.session_state:
                with st.form("save"):
                    ed = st.text_area("Veriler", st.session_state['res'], height=150)
                    if st.form_submit_button("Kaydet"):
                        s, m = save_receipt_smart(ed)
                        if s: st.success(m); del st.session_state['res']
                        else: st.error(m)

    elif page == "🧾 Fatura & Fiyatlar":
        st.header("🧾 Fatura Fiyat Güncelleme")
        st.info("PDF Faturayı yükle, isimler 'AYARLAR' sekmesine göre otomatik düzeltilir.")
        pdf = st.file_uploader("PDF Fatura", type=['pdf'])
        if pdf:
            if st.button("Analiz Et"):
                with st.spinner("PDF Okunuyor..."):
                    s, r = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['inv'] = r
            if 'inv' in st.session_state:
                with st.form("upd"):
                    ed = st.text_area("Algılanan", st.session_state['inv'], height=200)
                    if st.form_submit_button("Fiyatları İşle"):
                        s, m = update_price_list(ed)
                        if s: st.success(m); del st.session_state['inv']
                        else: st.error(m)

if __name__ == "__main__":
    main()
