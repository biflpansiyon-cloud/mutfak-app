import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Bedava", page_icon="💸")

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
    Bu fişi oku.
    1. TARİHİ bul (GG.AA.YYYY). Yoksa bugünü yaz.
    2. Ürünleri çıkar.
    3. Format: TARİH | ÜRÜN | MİKTAR | FİYAT | TUTAR
    4. Fiyat/Tutar yoksa boş bırakma, 0 yaz.
    
    Örnek:
    30.10.2025 | Bıldırcın | 17.02 KG | 0 | 0
    
    Sadece veriyi ver.
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

# --- KAYIT (NAZ YAPMAYAN MOD) ---
def save_to_sheet(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    try:
        sheet = client.open(SHEET_NAME).sheet1
        rows_to_add = []
        
        # Satır satır parçala
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            # İçinde en az bir çizgi varsa işlemeye çalış
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                
                # Başlık satırıysa atla
                if "TARİH" in parts[0].upper(): continue
                
                # BOŞLUKLARI DOLDUR (En kritik kısım burası)
                # Eğer parça boşsa ("") hemen "0" yapıyoruz.
                cleaned_parts = [p if p != "" else "0" for p in parts]
                
                # 5 Sütuna tamamla
                while len(cleaned_parts) < 5: 
                    cleaned_parts.append("0")
                
                # Sadece ilk 5 sütunu al (Fazlasını at)
                final_row = cleaned_parts[:5]
                
                rows_to_add.append(final_row)
        
        if rows_to_add:
            sheet.append_rows(rows_to_add)
            return True, f"✅ {len(rows_to_add)} satır başarıyla eklendi!"
        else:
            return False, "⚠️ Eklenecek satır bulunamadı. Metin formatı '|' içermiyor olabilir."
            
    except Exception as e:
        return False, f"Yazma Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("💸 Mutfak Bedava (2.5 Flash)")

# --- YAN MENÜ ---
with st.sidebar:
    st.header("🛠️ Ayarlar")
    if st.button("⚠️ Test Et"):
        c, _ = get_gspread_client()
        if c: 
            try:
                c.open(SHEET_NAME).sheet1.append_row([str(datetime.now()), "TEST", "OK"])
                st.success("Test Başarılı!")
            except: st.error("Dosya Hatası")
        else: st.error("Bağlantı Hatası")

    # Manuel Model Girişi (Senin 2.5 Flash için)
    selected_model = st.text_input("Model Adı", "models/gemini-2.5-flash")

# --- ANA EKRAN ---
uploaded_file = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Bıldırcınlar aranıyor..."):
            succ, txt = analyze_receipt(image, selected_model)
            
            # SESSION STATE KULLANALIM Kİ KAYBOLMASIN
            st.session_state['ocr_result'] = txt
            
    # Eğer sonuç varsa göster (Butona basılmasa bile sayfada kalsın)
    if 'ocr_result' in st.session_state:
        with st.form("save_form"):
            st.info("Aşağıdaki veriler Google Sheets'e gidecek:")
            edited = st.text_area("Veriler", st.session_state['ocr_result'], height=100)
            
            if st.form_submit_button("💾 Bıldırcını Kaydet"):
                s_save, msg = save_to_sheet(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                else:
                    st.error(msg)
