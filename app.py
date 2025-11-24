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

st.set_page_config(page_title="Mutfak ERP V10", page_icon="💎", layout="wide")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR"

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

def turkish_lower(text):
    if not text: return ""
    return text.replace('İ', 'i').replace('I', 'ı').lower().strip()

def standardize_name(text):
    if not text or len(text.strip()) < 2: return "Genel"
    # Yıldızları ve tireleri temizle
    cleaned = text.replace("*", "").replace("-", "").strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.6):
    if not ocr_text: return None
    ocr_key = turkish_lower(ocr_text)
    db_keys = [turkish_lower(p) for p in db_list]
    matches = difflib.get_close_matches(ocr_key, db_keys, n=1, cutoff=cutoff)
    if matches:
        matched_key = matches[0]
        idx = db_keys.index(matched_key)
        return db_list[idx]
    return None

def resolve_company_name(ocr_name, client):
    # İsimdeki Markdown artıklarını temizle
    std_name = standardize_name(ocr_name)
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(SETTINGS_SHEET_NAME)
            data = ws.get_all_values()
        except gspread.WorksheetNotFound: return std_name
        
        alias_map = {}
        for row in data[1:]:
            if len(row) >= 2:
                alias_map[turkish_lower(row[0])] = row[1].strip()
        
        key = turkish_lower(std_name)
        if key in alias_map: return alias_map[key]
        
        best_match = find_best_match(std_name, list(alias_map.keys()), cutoff=0.7)
        if best_match: return alias_map[turkish_lower(best_match)]
        return std_name
    except: return std_name

def get_price_database(client):
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(PRICE_SHEET_NAME)
        except: return {}
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
    Fiyat yoksa 0 yaz. Sadece veriyi ver, Markdown kullanma.
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
    try:
        sh = client.open(SHEET_NAME)
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        firm_data = {}
        for line in raw_text.split('\n'):
            line = line.replace("*", "").strip() # Temizlik
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client)
                tarih, urun, miktar, fiyat, tutar = parts[1], parts[2], parts[3], parts[4], parts[5]
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
            fn = turkish_lower(firma)
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
# MODÜL 2: FATURA MERKEZİ (DİSİPLİNLİ MOD)
# ==========================================
def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    pdf_bytes = uploaded_file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT DEĞİŞTİ: ASKERİ DİSİPLİN VE TEKRAR
    prompt = """
    FATURAYI analiz et. Amacımız fiyat listesi oluşturmak.
    
    KURALLAR:
    1. ASLA Markdown (kalın, italik, yıldız) kullanma. Dümdüz yazı istiyorum.
    2. Her satırda TEDARİKÇİ ADINI TEKRAR YAZ. (Tepede bir kere yazıp bırakma).
    3. Sadece ürünleri al (KDV, Toplam gibi satırları alma).
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | ÜRÜN ADI | BİRİM FİYAT
    
    Örnek:
    Fatma Yılmaz | Yumurta | 7.50
    Fatma Yılmaz | Kaşar Peyniri | 320.00
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
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                product_map[f"{k_firma}|{k_urun}"] = idx + 1
        
        updates_batch, new_rows_batch = [], []
        cnt_upd, cnt_new = 0, 0
        
        lines = raw_text.split('\n')
        for line in lines:
            # TEMİZLİK: Yıldızları ve tireleri temizle
            clean = line.replace("*", "").replace("- ", "").strip()
            
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                if "TEDARİKÇİ" in parts[0].upper() or "ÜRÜN" in parts[1].upper(): continue
                while len(parts) < 3: parts.append("0")
                
                # Fiyatın 0 olduğu veya saçma olduğu satırları atla
                if clean_number(parts[2]) == 0: continue
                
                raw_supplier = parts[0]
                # İsim Çeviriciyi burada çağırıyoruz
                target_supplier = resolve_company_name(raw_supplier, client)
                
                urun = parts[1].strip()
                fiyat = clean_number(parts[2])
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{turkish_lower(target_supplier)}|{turkish_lower(urun)}"
                
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
        st.title("Mutfak ERP V10")
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
        st.info("İpucu: 'AYARLAR' sekmesinde 'Fatma Yılmaz' -> 'Yılmaz Gıda' eşleşmesini yapmayı unutma.")
        pdf = st.file_uploader("PDF Fatura", type=['pdf'])
        if pdf:
            if st.button("Analiz Et"):
                with st.spinner("PDF Okunuyor..."):
                    s, r = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['inv'] = r
            if 'inv' in st.session_state:
                with st.form("upd"):
                    st.write("▼ **Algılanan Liste:**")
                    ed = st.text_area("Sonuç", st.session_state['inv'], height=200)
                    if st.form_submit_button("Fiyatları İşle"):
                        s, m = update_price_list(ed)
                        if s: st.success(m); del st.session_state['inv']
                        else: st.error(m)

if __name__ == "__main__":
    main()
