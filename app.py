import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Mutfak Devriyesi", page_icon="🍅")

# --- BAŞLANGIÇ ---
def setup_sheets():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        return None

client = setup_sheets()
SHEET_NAME = "Mutfak_Takip"

# --- 1. MODELLERİ LİSTELEME (DEBUGGER) ---
def list_available_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Sadece resim okuyabilen (vision) modelleri ayıkla
            vision_models = [m['name'] for m in data.get('models', []) if 'vision' in m['supportedGenerationMethods'] or 'generateContent' in m['supportedGenerationMethods']]
            return vision_models
        else:
            return [f"Hata: {response.text}"]
    except Exception as e:
        return [f"Bağlantı Hatası: {str(e)}"]

# --- 2. ANALİZ FONKSİYONU ---
def analyze_image_direct(image, selected_model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    
    # Model isminin başındaki 'models/' kısmını temizleyelim ki çift olmasın
    clean_model_name = selected_model_name.replace("models/", "")
    
    # Resmi Hazırla
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    # URL'yi dinamik yapıyoruz (Seçtiğin modele göre değişecek)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{
            "parts": [
                {"text": "Sen bir muhasebe asistanısın. İrsaliye fotoğrafını analiz et. SADECE JSON formatında veri ver: [{\"Urun\": \"Ad\", \"Miktar\": \"kg\", \"Fiyat\": \"TL\", \"Tutar\": \"TL\"}]"},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            return True, response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return False, f"API Hatası ({response.status_code}): {response.text}"
    except Exception as e:
        return False, f"Bağlantı Hatası: {str(e)}"

def save_to_sheet(json_text):
    if not client: return False, "Sheets Bağlantısı Yok"
    try:
        clean = json_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        sheet = client.open(SHEET_NAME).sheet1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        count = 0
        for item in data:
            sheet.append_row([timestamp, item.get("Urun","-"), item.get("Miktar","0"), item.get("Fiyat","0"), item.get("Tutar","0")])
            count += 1
        return True, str(count)
    except Exception as e: return False, str(e)

# --- ARAYÜZ ---
st.title("🍅 Mutfak İrsaliye (Tanı Modu)")

# Yan Menüde Model Seçimi
with st.sidebar:
    st.header("⚙️ Ayarlar")
    if st.button("Mevcut Modelleri Tara"):
        models = list_available_models()
        st.session_state['models'] = models
        st.success("Modeller güncellendi!")

    # Eğer model listesi varsa göster, yoksa varsayılanları koy
    model_options = st.session_state.get('models', ['gemini-1.5-flash', 'gemini-pro-vision', 'gemini-1.5-pro'])
    selected_model = st.selectbox("Kullanılacak Model:", model_options)
    st.caption(f"Seçili: {selected_model}")

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et ve Kaydet", type="primary"):
        with st.spinner(f"{selected_model} ile okunuyor..."):
            success, result = analyze_image_direct(image, selected_model)
            
            if success:
                st.toast("Okuma Başarılı!")
                s_save, msg = save_to_sheet(result)
                if s_save:
                    st.balloons()
                    st.success(f"✅ {msg} kalem eklendi!")
                else:
                    st.error(f"Kayıt Hatası: {msg}")
            else:
                st.error("❌ Analiz Başarısız")
                with st.expander("Hata Detayı"):
                    st.code(result)
