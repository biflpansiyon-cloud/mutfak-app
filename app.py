import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Finans", page_icon="💰")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI" # Fiyatların durduğu sekme

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
    """ '17,02 KG' gibi metinleri 17.02 (float) yapar. """
    try:
        # Harfleri temizle, sadece rakam ve virgül/nokta kalsın
        clean = ''.join(c for c in num_str if c.isdigit() or c in [',', '.'])
        # Virgülü noktaya çevir
        clean = clean.replace(',', '.')
        return float(clean)
    except:
        return 0.0

# --- FİYAT BANKASINI ÇEK ---
def get_price_database(client):
    """ FIYAT_ANAHTARI sekmesini okuyup hafızaya alır. """
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(PRICE_SHEET_NAME)
        data = ws.get_all_values()
        
        # Başlığı atla, verileri oku
        for row in data[1:]:
            if len(row) >= 3:
                tedarikci = row[0].strip().lower()
                urun = row[1].strip().lower()
                fiyat = clean_number(row[2])
                
                # Anahtar: "firma_adı | ürün_adı"
                key = f"{tedarikci}|{urun}"
                price_db[key] = fiyat
        return price_db
    except Exception:
        return {} # Sekme yoksa veya hataysa boş dön

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
    TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Birimli) | BİRİM FİYAT | TOPLAM TUTAR
    
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

# --- KAYIT (AKILLI FİYAT EŞLEŞTİRME) ---
def save_with_pricing(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    # 1. Önce Fiyat Bankasını İndir
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
                
                # Verileri Ayrıştır
                firma = parts[0].strip()
                tarih = parts[1].strip()
                urun = parts[2].strip()
                miktar_str = parts[3].strip()
                fiyat_str = parts[4].strip()
                tutar_str = parts[5].strip()
                
                # --- FİYAT MOTORU ---
                fiyat_val = clean_number(fiyat_str)
                
                # Eğer irsaliyede fiyat yoksa (0 ise), Bankaya bak!
                if fiyat_val == 0:
                    # Anahtarı oluştur (Alp Et | Dana Kıyma)
                    search_key = f"{firma.lower()}|{urun.lower()}"
                    
                    if search_key in price_db:
                        found_price = price_db[search_key]
                        fiyat_str = str(found_price) # Fiyatı bulduk!
                        
                        # Tutarı da hesaplayalım (Miktar x Fiyat)
                        miktar_val = clean_number(miktar_str)
                        tutar_val = miktar_val * found_price
                        tutar_str = f"{tutar_val:.2f}"
                
                # Güncellenmiş satırı hazırla
                row_data = [tarih, urun, miktar_str, fiyat_str, tutar_str]
                
                # Firma grubuna ekle
                firma_key = firma.replace("/", "-").strip()
                if not firma_key: firma_key = "Genel"
                if len(firma_key) > 30: firma_key = firma_key[:30]
                
                if firma_key not in firm_data: firm_data[firma_key] = []
                firm_data[firma_key].append(row_data)

        # Kayıt İşlemi
        messages = []
        for firma, rows in firm_data.items():
            try:
                ws = sh.worksheet(firma)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=firma, rows=1000, cols=10)
                ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
            
            ws.append_rows(rows)
            messages.append(f"{firma}: {len(rows)} satır")
            
        if messages: return True, " | ".join(messages) + " kaydedildi (Fiyatlar güncellendi)."
        else: return False, "Veri yok."

    except Exception as e: return False, f"Hata: {str(e)}"

# --- ARAYÜZ ---
st.title("💰 Mutfak Finans")

with st.sidebar:
    st.header("Ayarlar")
    favorite_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    if st.button("Listeyi Güncelle"):
        f = fetch_google_models()
        if f: st.session_state['ml'] = sorted(list(set(favorite_models + f)))
    
    cl = st.session_state.get('ml', favorite_models)
    ix = 0
    if "models/gemini-2.5-flash" in cl: ix = cl.index("models/gemini-2.5-flash")
    sel_model = st.selectbox("Model", cl, index=ix)
    
    st.info("İpucu: Eğer irsaliyede fiyat 0 ise, 'FIYAT_ANAHTARI' sekmesindeki son fiyattan çeker.")

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Finansal veriler işleniyor..."):
            succ, txt = analyze_receipt(image, sel_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("edit_save"):
            st.info("Format: TEDARİKÇİ | TARİH | ÜRÜN | MİKTAR | FİYAT | TUTAR")
            edited = st.text_area("Sonuç", st.session_state['ocr_result'], height=150)
            
            if st.form_submit_button("💾 Hesapla ve Kaydet"):
                s_save, msg = save_with_pricing(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['ocr_result']
                else:
                    st.error(msg)
