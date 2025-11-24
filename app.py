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

st.set_page_config(page_title="Mutfak ERP", page_icon="🏢", layout="wide")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"

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

# --- ORTAK YARDIMCI FONKSİYONLAR ---
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
        # 1.250,50 gibi formatları düzeltmek gerekebilir
        # Basitçe: Rakam ve son ayırıcıyı al
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
            if original.lower() == matched_lower:
                return original
    return None

def get_price_database(client):
    """ Fiyat listesini okur ve döner """
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(PRICE_SHEET_NAME)
        except gspread.WorksheetNotFound:
            return {}
        
        data = ws.get_all_values()
        for row in data[1:]:
            if len(row) >= 3:
                tedarikci = standardize_name(row[0])
                urun = row[1].strip()
                fiyat = clean_number(row[2])
                if tedarikci not in price_db: price_db[tedarikci] = {}
                price_db[tedarikci][urun] = fiyat
        return price_db
    except Exception: return {}

# ==========================================
# MODÜL 1: İRSALİYE (GÜNLÜK GİRİŞ)
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
    İrsaliyeyi analiz et. Tedarikçi firmayı logolardan bul.
    ÇIKTI FORMATI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Birimli) | BİRİM FİYAT | TOPLAM TUTAR
    KURALLAR:
    1. Fiyat yoksa '0' yaz.
    2. Firma adını kısa tut.
    3. Sadece verileri ver.
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
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
                
                ocr_firma = standardize_name(parts[0])
                matched = find_best_match(ocr_firma, known_companies, cutoff=0.6)
                final_firma = matched if matched else ocr_firma
                
                tarih, urun, miktar, fiyat, tutar = parts[1], parts[2], parts[3], parts[4], parts[5]
                
                # Fiyat Eşleştirme
                f_val = clean_number(fiyat)
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
            if fn in existing_sheets:
                ws = existing_sheets[fn]
            else:
                ws = sh.add_worksheet(title=firma, rows=1000, cols=10)
                ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
                existing_sheets[fn] = ws
            ws.append_rows(rows)
            msg.append(f"{firma}: {len(rows)}")
            
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)


# ==========================================
# MODÜL 2: FATURA MERKEZİ (PDF & FİYAT GÜNCELLEME)
# ==========================================
def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    
    # PDF'i Base64'e çevir
    pdf_bytes = uploaded_file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Sen bir maliyet muhasebecisisin. Bu FATURAYI analiz et.
    Amacımız: Ürünlerin güncel birim fiyatlarını çıkarmak.
    
    1. Tedarikçi Firmayı Bul.
    2. Kalemleri listele.
    3. BİRİM FİYAT sütununu bul (KDV HARİÇ fiyatı al).
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT
    
    Örnek:
    Alp Et | Dana Kıyma | 450.00
    Yılmaz Gıda | Salça | 120.50
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
        # Fiyat sekmesini bul veya yarat
        try:
            ws = sh.worksheet(PRICE_SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=PRICE_SHEET_NAME, rows=1000, cols=5)
            ws.append_row(["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "GÜNCELLEME TARİHİ"])
            
        # Mevcut Fiyat Listesini Çek (Karşılaştırma ve Güncelleme için)
        existing_data = ws.get_all_values()
        # Bir sözlük yap: keys = "Firma|Ürün" -> value = Satır Numarası
        product_map = {}
        for idx, row in enumerate(existing_data):
            if idx == 0: continue # Başlık
            if len(row) >= 2:
                key = f"{standardize_name(row[0])}|{row[1].strip().lower()}"
                product_map[key] = idx + 1 # Google Sheets 1-based index
        
        updated_count = 0
        new_count = 0
        updates_batch = [] # Toplu güncelleme listesi
        new_rows_batch = []
        
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 3: parts.append("0")
                
                firma = standardize_name(parts[0])
                urun = parts[1].strip()
                fiyat = clean_number(parts[2])
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{firma}|{urun.lower()}"
                
                if key in product_map:
                    # GÜNCELLEME: Mevcut satırın Fiyat(C) ve Tarih(D) sütununu güncelle
                    row_idx = product_map[key]
                    # Batch listesine ekle: (Row, Col, Value)
                    # Col 3 = Fiyat, Col 4 = Tarih
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})
                    updates_batch.append({'range': f'D{row_idx}', 'values': [[bugun]]})
                    updated_count += 1
                else:
                    # YENİ EKLEME
                    new_rows_batch.append([firma, urun, fiyat, bugun])
                    new_count += 1
        
        # İşlemleri Uygula
        if updates_batch:
            ws.batch_update(updates_batch)
        if new_rows_batch:
            ws.append_rows(new_rows_batch)
            
        return True, f"✅ {updated_count} fiyat güncellendi, {new_count} yeni ürün eklendi."
        
    except Exception as e: return False, str(e)


# ==========================================
# ANA ARAYÜZ (NAVIGASYON)
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP")
        page = st.radio("Menü", ["📝 Günlük İrsaliye", "🧾 Fatura & Fiyatlar"])
        
        st.divider()
        st.header("⚙️ Model Ayarı")
        models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
        sel_model = st.selectbox("Model", models)

    if page == "📝 Günlük İrsaliye":
        st.title("📝 İrsaliye Girişi")
        st.caption("Bıldırcınlar buraya...")
        
        f = st.file_uploader("İrsaliye Fişi", type=['jpg', 'png', 'jpeg'])
        if f:
            img = Image.open(f)
            st.image(img, width=300)
            if st.button("İrsaliyeyi İşle", type="primary"):
                with st.spinner("Okunuyor..."):
                    suc, res = analyze_receipt_image(img, sel_model)
                    st.session_state['receipt_res'] = res
            
            if 'receipt_res' in st.session_state:
                with st.form("save_receipt"):
                    edited = st.text_area("Kontrol", st.session_state['receipt_res'], height=150)
                    if st.form_submit_button("💾 Kaydet"):
                        s, m = save_receipt_smart(edited)
                        if s: st.success(m); del st.session_state['receipt_res']
                        else: st.error(m)

    elif page == "🧾 Fatura & Fiyatlar":
        st.title("🧾 Fatura Merkezi")
        st.info("PDF Faturayı yükle, 'FIYAT_ANAHTARI' sekmesini otomatik güncelle.")
        
        pdf = st.file_uploader("PDF Fatura Yükle", type=['pdf'])
        if pdf:
            if st.button("Faturayı Analiz Et ve Fiyatları Güncelle", type="primary"):
                with st.spinner("PDF okunuyor, fiyatlar çekiliyor..."):
                    suc, res = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['invoice_res'] = res
            
            if 'invoice_res' in st.session_state:
                with st.form("update_prices"):
                    st.write("▼ **Algılanan Fiyatlar (Kontrol Et):**")
                    edited_prices = st.text_area("Listesi", st.session_state['invoice_res'], height=200)
                    
                    if st.form_submit_button("💰 Fiyatları Veritabanına İşle"):
                        s, m = update_price_list(edited_prices)
                        if s: st.balloons(); st.success(m); del st.session_state['invoice_res']
                        else: st.error(m)

if __name__ == "__main__":
    main()
