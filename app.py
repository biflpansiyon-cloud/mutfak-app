import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import difflib # <-- YENİ SİLAHIMIZ: BULANIK MANTIK

st.set_page_config(page_title="Mutfak Zeka", page_icon="🧠")

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

# --- MODEL LİSTESİ ---
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

# --- YARDIMCI: SAYI TEMİZLEME ---
def clean_number(num_str):
    try:
        clean = ''.join(c for c in num_str if c.isdigit() or c in [',', '.'])
        clean = clean.replace(',', '.')
        return float(clean)
    except: return 0.0

# --- YARDIMCI: METİN STANDARTLAŞTIRMA ---
def standardize_name(text):
    """ 'ALP ET ' -> 'Alp Et' yapar. Boşlukları alır. """
    if not text: return "Genel"
    return text.strip().title()

# --- YENİ YETENEK: BULANIK EŞLEŞTİRME ---
def find_best_match(ocr_product, db_products_list):
    """ 
    'Tereyağ' gelirse ve listede 'Tereyağı' varsa onu bulur.
    Benzerlik oranı %70'in üzerindeyse eşleştirir.
    """
    if not ocr_product: return None
    
    # Python'ın difflib kütüphanesi en yakın eşleşmeyi bulur
    matches = difflib.get_close_matches(ocr_product.lower(), [p.lower() for p in db_products_list], n=1, cutoff=0.7)
    
    if matches:
        # Eşleşen ürünün orijinal halini (Fiyat listesindeki halini) bulmak lazım
        matched_lower = matches[0]
        for original_name in db_products_list:
            if original_name.lower() == matched_lower:
                return original_name
    return None

# --- FİYAT BANKASINI ÇEK (YENİ YAPI) ---
def get_price_database(client):
    """ 
    Yapıyı değiştirdik: { "Alp Et": { "Dana Biftek": 500, "Kıyma": 400 } } 
    Böylece firma bazında arama yapacağız.
    """
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(PRICE_SHEET_NAME)
        data = ws.get_all_values()
        
        for row in data[1:]:
            if len(row) >= 3:
                # Tedarikçi ismini standartlaştır (Alp Et)
                tedarikci = standardize_name(row[0])
                urun = row[1].strip() # Ürün adını olduğu gibi al (Büyük/küçük harf fuzzy'de çözülecek)
                fiyat = clean_number(row[2])
                
                if tedarikci not in price_db:
                    price_db[tedarikci] = {}
                
                price_db[tedarikci][urun] = fiyat
        return price_db
    except Exception:
        return {} 

# --- ANALİZ ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Bu irsaliyeyi analiz et. Tedarikçi firmayı logolardan bul.
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Sadece sayı ve birim) | BİRİM FİYAT | TOPLAM TUTAR
    
    KURALLAR:
    1. Fiyat/Tutar yazmıyorsa '0' yaz.
    2. Firma adını kısa tut (Alp Et, Yılmaz Gıda).
    3. Miktarı olduğu gibi yaz (5 KG, 10 Adet).
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, f"Hata: {response.text}"
        result = response.json()
        if 'candidates' in result: return True, result['candidates'][0]['content']['parts'][0]['text']
        return False, "Boş cevap."
    except Exception as e: return False, str(e)

# --- KAYIT (AKILLI FİYAT VE BULANIK MANTIK) ---
def save_with_pricing_smart(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    # 1. Fiyat Bankasını İndir
    price_db = get_price_database(client)
    
    try:
        sh = client.open(SHEET_NAME)
        firm_data = {}
        
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                
                # VERİLERİ ÇEK VE STANDARTLAŞTIR
                raw_firma = parts[0]
                firma_std = standardize_name(raw_firma) # ALP ET -> Alp Et
                
                tarih = parts[1].strip()
                urun_ocr = parts[2].strip()
                miktar_str = parts[3].strip()
                fiyat_str = parts[4].strip()
                tutar_str = parts[5].strip()
                
                # --- FİYAT MOTORU (V2.0 - BULANIK MANTIK) ---
                fiyat_val = clean_number(fiyat_str)
                
                # İrsaliyede fiyat yoksa bankaya sor
                if fiyat_val == 0:
                    # 1. Bu firmanın fiyat listesi var mı?
                    if firma_std in price_db:
                        # 2. Bu firmanın ürün listesini al
                        firma_urunleri = list(price_db[firma_std].keys())
                        
                        # 3. BULANIK ARAMA YAP (Tereyağ ~= Tereyağı)
                        best_match = find_best_match(urun_ocr, firma_urunleri)
                        
                        if best_match:
                            # Eşleşme bulundu!
                            found_price = price_db[firma_std][best_match]
                            fiyat_val = found_price
                            fiyat_str = str(found_price)
                            
                            # İsim uyuşmazlığını da düzeltelim mi? 
                            # (İsteğe bağlı: Ürün adını Bankadaki gibi yapmak veritabanı temizliği için iyidir)
                            urun_ocr = f"{urun_ocr} ({best_match})" # Örn: Tereyağ (Tereyağı)
                            
                            # Tutarı Hesapla
                            miktar_val = clean_number(miktar_str)
                            tutar_val = miktar_val * fiyat_val
                            tutar_str = f"{tutar_val:.2f}"
                
                # Satırı hazırla
                row_data = [tarih, urun_ocr, miktar_str, fiyat_str, tutar_str]
                
                if firma_std not in firm_data: firm_data[firma_std] = []
                firm_data[firma_std].append(row_data)

        # Kayıt İşlemi
        messages = []
        for firma, rows in firm_data.items():
            try:
                # Sekme adını kontrol et (Büyük küçük harf duyarlı olabilir, try-except ile yakala)
                ws = sh.worksheet(firma)
            except gspread.WorksheetNotFound:
                # Sekme yoksa yeni yarat
                ws = sh.add_worksheet(title=firma, rows=1000, cols=10)
                ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
            
            ws.append_rows(rows)
            messages.append(f"{firma}: {len(rows)} satır")
            
        if messages: return True, " | ".join(messages) + " kaydedildi."
        else: return False, "Veri yok."

    except Exception as e: return False, f"Hata: {str(e)}"

# --- ARAYÜZ ---
st.title("🧠 Mutfak Zeka")

with st.sidebar:
    st.header("Ayarlar")
    # Model Listesi
    fav_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    if st.button("Listeyi Güncelle"):
        f = fetch_google_models()
        if f: st.session_state['ml'] = sorted(list(set(fav_models + f)))
    
    cl = st.session_state.get('ml', fav_models)
    ix = 0
    if "models/gemini-2.5-flash" in cl: ix = cl.index("models/gemini-2.5-flash")
    sel_model = st.selectbox("Model", cl, index=ix)
    
    st.info("💡 İpucu: 'Tereyağ' yazsa bile listedeki 'Tereyağı'nı bulup fiyatı çeker.")

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Bulanık mantık çalışıyor..."):
            succ, txt = analyze_receipt(image, sel_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("edit_save"):
            st.info("Format: TEDARİKÇİ | TARİH | ÜRÜN | MİKTAR | FİYAT | TUTAR")
            edited = st.text_area("Sonuç", st.session_state['ocr_result'], height=150)
            
            if st.form_submit_button("💾 Akıllı Kaydet"):
                s_save, msg = save_with_pricing_smart(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['ocr_result']
                else:
                    st.error(msg)
