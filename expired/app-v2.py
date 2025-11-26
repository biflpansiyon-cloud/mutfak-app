import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak ERP", page_icon="🏢")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"

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

# --- ANALİZ (TEDARİKÇİ AVCI MODU) ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT GÜNCELLENDİ: ARTIK FİRMA ADINI DA İSTİYORUZ
    prompt = """
    Sen uzman bir stok yöneticisisin. Bu irsaliyeyi analiz et.
    
    GÖREVLER:
    1. TEDARİKÇİ FİRMA ADINI bul (Örn: Yılmaz Gıda, Alp Et). Logolara dikkat et.
       - Firma adını kısa ve net tut (Yılmaz Gıda Sanayi Ticaret A.Ş. deme, 'Yılmaz Gıda' de).
    2. TARİHİ bul (GG.AA.YYYY).
    3. Kalemleri listele.
    4. Miktar ve Birimleri koru.
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek:
    Alp Et | 23.10.2025 | Dana Kıyma | 5 KG | 0 | 0
    Yılmaz Gıda | 23.10.2025 | Salça | 2 Teneke | 0 | 0
    
    Başka hiçbir şey yazma. Sadece veriyi ver.
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

# --- KAYIT (OTOMATİK SEKME AÇMA MODU) ---
def save_to_sheet_smart(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    try:
        sh = client.open(SHEET_NAME)
        
        # Verileri Firma Bazında Gruplayalım
        # Hangi firmanın verisi hangi satırlar?
        firm_data = {} # { "Alp Et": [[tarih, urun...], [tarih, urun...]] }
        
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                
                # Başlıkları atla
                if "TEDARİKÇİ" in parts[0].upper() or "TARİH" in parts[1].upper(): continue
                
                # Sütunları tamamla (En az 6 sütun lazım: Tedarikçi, Tarih, Ürün, Miktar, Fiyat, Tutar)
                while len(parts) < 6: parts.append("0")
                
                firma_adi = parts[0]
                row_data = parts[1:6] # Tarih'ten Tutara kadar olan kısım
                
                # Firma adını temizle (Dosya adı olacağı için)
                firma_adi = firma_adi.replace("/", "-").replace(":", "").strip()
                if len(firma_adi) > 30: firma_adi = firma_adi[:30] # Excel sekme adı sınırı
                if not firma_adi: firma_adi = "Genel"

                if firma_adi not in firm_data:
                    firm_data[firma_adi] = []
                
                firm_data[firma_adi].append(row_data)
        
        # Şimdi her firma için ayrı kayıt yapalım
        messages = []
        for firma, rows in firm_data.items():
            # Sekme var mı kontrol et
            try:
                worksheet = sh.worksheet(firma)
            except gspread.WorksheetNotFound:
                # Yoksa YENİ OLUŞTUR
                worksheet = sh.add_worksheet(title=firma, rows=1000, cols=10)
                # Başlık satırını ekle
                worksheet.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
            
            # Verileri ekle
            worksheet.append_rows(rows)
            messages.append(f"{firma}: {len(rows)} satır")
            
        if messages:
            return True, " | ".join(messages) + " kaydedildi."
        else:
            return False, "Kaydedilecek geçerli veri bulunamadı."
            
    except Exception as e:
        return False, f"Kayıt Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("🏢 Mutfak ERP (Firma Modu)")

with st.sidebar:
    st.header("⚙️ Ayarlar")
    favorite_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    
    if st.button("Listeyi Güncelle"):
        fetched = fetch_google_models()
        if fetched: st.session_state['model_list'] = sorted(list(set(favorite_models + fetched)))
    
    current_list = st.session_state.get('model_list', favorite_models)
    
    # Varsayılan 2.5 flash
    def_ix = 0
    if "models/gemini-2.5-flash" in current_list: def_ix = current_list.index("models/gemini-2.5-flash")
    
    selected_model = st.selectbox("Model", current_list, index=def_ix)
    st.info(f"Seçili: {selected_model}")

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Firma tespit ediliyor..."):
            succ, txt = analyze_receipt(image, selected_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("edit_save"):
            st.info("Format: TEDARİKÇİ | TARİH | ÜRÜN | MİKTAR | FİYAT | TUTAR")
            edited = st.text_area("Veriler", st.session_state['ocr_result'], height=150)
            
            if st.form_submit_button("💾 Akıllı Kaydet"):
                s_save, msg = save_to_sheet_smart(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['ocr_result']
                else:
                    st.error(msg)
