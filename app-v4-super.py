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

st.set_page_config(page_title="Mutfak Otorite", page_icon="⚖️")

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

# --- YARDIMCI FONKSİYONLAR ---
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

# --- KRİTİK NOKTA: BENZERLİK BULUCU ---
def find_best_match(ocr_text, db_list, cutoff=0.6):
    """
    Hem ÜRÜN hem FİRMA için kullanılır.
    cutoff=0.6 demek: %60 benziyorsa yapıştır geç demektir.
    """
    if not ocr_text: return None
    matches = difflib.get_close_matches(ocr_text.lower(), [p.lower() for p in db_list], n=1, cutoff=cutoff)
    if matches:
        matched_lower = matches[0]
        # Orijinal (Büyük/Küçük harfli) halini bulup döndür
        for original in db_list:
            if original.lower() == matched_lower:
                return original
    return None

# --- FİYAT BANKASINI ÇEK ---
def get_price_database(client):
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
                # Tedarikçi ismini standartlaştır ama olduğu gibi sakla
                tedarikci = standardize_name(row[0]) 
                urun = row[1].strip()
                fiyat = clean_number(row[2])
                
                if tedarikci not in price_db: price_db[tedarikci] = {}
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
    Bu irsaliyeyi analiz et. Tedarikçi firmayı bul.
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Sayı ve Birim) | BİRİM FİYAT | TOPLAM TUTAR
    
    KURALLAR:
    1. Fiyat/Tutar yazmıyorsa '0' yaz.
    2. Firma adını, irsaliyenin en tepesindeki büyük logodan/yazıdan al.
    3. Miktarı olduğu gibi yaz.
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

# --- KAYIT (FİRMA EŞLEŞTİRMELİ MOD) ---
def save_with_pricing_smart(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    # 1. Bankayı Çek
    price_db = get_price_database(client)
    
    # 2. Bankadaki Firma Listesini Çıkar (Otorite Listesi)
    known_companies = list(price_db.keys())
    
    try:
        sh = client.open(SHEET_NAME)
        
        # Mevcut sekmeleri haritala
        existing_sheets_map = {ws.title.strip().lower(): ws for ws in sh.worksheets()}

        firm_data = {}
        
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                
                # --- YENİ ÖZELLİK: FİRMA ADI MUTABAKATI ---
                ocr_firma = standardize_name(parts[0])
                
                # Bu firma bankada var mı? Benzeri var mı?
                # "Uysallar Ekmek" geldi -> Listede "Uysallar Ekmek Fırını" var mı?
                matched_company = find_best_match(ocr_firma, known_companies, cutoff=0.6)
                
                if matched_company:
                    final_firma = matched_company # Evet var, bankadaki ismini kullan!
                else:
                    final_firma = ocr_firma # Yoksa mecburen OCR sonucunu kullan
                # -------------------------------------------
                
                tarih = parts[1].strip()
                urun_ocr = parts[2].strip()
                miktar_str = parts[3].strip()
                fiyat_str = parts[4].strip()
                tutar_str = parts[5].strip()
                
                # Fiyat Motoru
                fiyat_val = clean_number(fiyat_str)
                if fiyat_val == 0:
                    # Artık final_firma ile arıyoruz, yani eşleşme garanti
                    if final_firma in price_db:
                        firma_urunleri = list(price_db[final_firma].keys())
                        best_match = find_best_match(urun_ocr, firma_urunleri, cutoff=0.7)
                        
                        if best_match:
                            found_price = price_db[final_firma][best_match]
                            fiyat_val = found_price
                            fiyat_str = str(found_price)
                            urun_ocr = f"{urun_ocr} ({best_match})"
                            
                            miktar_val = clean_number(miktar_str)
                            tutar_val = miktar_val * fiyat_val
                            tutar_str = f"{tutar_val:.2f}"
                
                row_data = [tarih, urun_ocr, miktar_str, fiyat_str, tutar_str]
                
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append(row_data)

        # Kayıt İşlemi
        messages = []
        for firma_adi, rows in firm_data.items():
            firma_norm = firma_adi.strip().lower()
            
            if firma_norm in existing_sheets_map:
                ws = existing_sheets_map[firma_norm]
                action = "Eklendi"
            else:
                try:
                    # Yeni sekme açılırken de artık standardize edilmiş ismi (örn: Uysallar Ekmek Fırını) kullanacak
                    ws = sh.add_worksheet(title=firma_adi, rows=1000, cols=10)
                    ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
                    existing_sheets_map[firma_norm] = ws
                    action = "Yeni sekme"
                except Exception as e:
                     return False, f"Sekme hatası ({firma_adi}): {str(e)}"
            
            ws.append_rows(rows)
            messages.append(f"{firma_adi}: {len(rows)} satır ({action})")
            
        if messages: return True, " | ".join(messages)
        else: return False, "Veri yok."

    except Exception as e: return False, f"Hata: {str(e)}"

# --- ARAYÜZ ---
st.title("⚖️ Mutfak Otorite")

with st.sidebar:
    st.header("Ayarlar")
    fav_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    if st.button("Listeyi Güncelle"):
        f = fetch_google_models()
        if f: st.session_state['ml'] = sorted(list(set(fav_models + f)))
    
    cl = st.session_state.get('ml', fav_models)
    ix = 0
    if "models/gemini-2.5-flash" in cl: ix = cl.index("models/gemini-2.5-flash")
    sel_model = st.selectbox("Model", cl, index=ix)
    st.info("Firma isimlerini 'FIYAT_ANAHTARI' sekmesine göre düzeltir.")

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Firma veritabanı taranıyor..."):
            succ, txt = analyze_receipt(image, sel_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("edit_save"):
            st.info("TEDARİKÇİ | TARİH | ÜRÜN | MİKTAR | FİYAT | TUTAR")
            edited = st.text_area("Sonuç", st.session_state['ocr_result'], height=150)
            
            if st.form_submit_button("💾 Otoriter Kaydet"):
                s_save, msg = save_with_pricing_smart(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['ocr_result']
                else:
                    st.error(msg)
