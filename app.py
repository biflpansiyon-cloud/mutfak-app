import streamlit as st
from PIL import Image
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

# --- MODELLERİ LİSTELE ---
def list_available_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Sadece işimize yarayan modelleri al
            return [m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']]
        return []
    except:
        return []

# --- ANALİZ (BALYOZ YÖNTEMİ) ---
def analyze_image_simple(image, selected_model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model_name = selected_model_name.replace("models/", "")
    
    # Resmi Hazırla
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT: JSON yerine düz metin istiyoruz (Daha sağlam)
    prompt_text = """
    Sen bir muhasebe asistanısın. Bu irsaliyeyi oku.
    Bana ürünleri SADECE şu formatta ver:
    URUN ADI | MIKTAR | BIRIM FIYAT | TOPLAM TUTAR
    
    Örnek Çıktı:
    Domates | 5 KG | 10 TL | 50 TL
    Salatalık | 3 KG | 5 TL | 15 TL
    
    Başka hiçbir giriş cümlesi veya 'işte sonuçlar' gibi yazılar yazma. Sadece listeyi ver.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        # HATA AYIKLAMA İÇİN:
        if response.status_code != 200:
            return False, f"Google Hatası ({response.status_code}): {response.text}"
            
        # Cevabı al
        result_json = response.json()
        try:
            raw_text = result_json['candidates'][0]['content']['parts'][0]['text']
            return True, raw_text
        except KeyError:
            return False, f"Google boş cevap döndü. Gelen paket: {str(result_json)}"
            
    except Exception as e:
        return False, f"Bağlantı Koptu: {str(e)}"

def save_lines_to_sheet(raw_text):
    if not client: return False, "Sheets Bağlantısı Yok"
    
    try:
        sheet = client.open(SHEET_NAME).sheet1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        added_count = 0
        
        # Satır satır oku
        lines = raw_text.split('\n')
        
        for line in lines:
            # Boş satırları atla
            if not line.strip() or "|" not in line:
                continue
                
            # Çizgilerden böl (Domates | 5 | .. )
            parts = [p.strip() for p in line.split('|')]
            
            # Eğer 4 parça varsa tabloya ekle
            if len(parts) >= 4:
                row = [timestamp] + parts[:4] # Tarih + ilk 4 sütun
                sheet.append_row(row)
                added_count += 1
                
        if added_count == 0:
            return False, "Metin okundu ama tablo formatına ( | ) uymuyor."
            
        return True, str(added_count)
        
    except Exception as e: return False, str(e)

# --- ARAYÜZ ---
st.title("🍅 Mutfak İrsaliye (Balyoz Modu)")

# Model Seçimi
with st.sidebar:
    if st.button("Modelleri Güncelle"):
        st.session_state['models'] = list_available_models()
    
    # Varsayılan olarak Flash modelini en üste koy
    default_list = ['models/gemini-1.5-flash', 'models/gemini-1.5-pro']
    model_options = st.session_state.get('models', default_list)
    selected_model = st.selectbox("Model:", model_options)

uploaded_file = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et ve Kaydet", type="primary"):
        with st.spinner("Google sunucusuyla konuşuluyor..."):
            
            # 1. Adım: Ham Metni Al
            success, result_text = analyze_image_simple(image, selected_model)
            
            # Ham cevabı her durumda göster (Hata ayıklamak için şart)
            with st.expander("Google'dan Gelen Ham Cevap (Kontrol Et)"):
                st.text(result_text)
            
            if success:
                # 2. Adım: Tabloya Çevir ve Kaydet
                save_success, save_msg = save_lines_to_sheet(result_text)
                
                if save_success:
                    st.balloons()
                    st.success(f"✅ Başarılı! {save_msg} satır eklendi.")
                else:
                    st.warning(f"Metin okundu ama Excel'e yazılamadı: {save_msg}")
            else:
                st.error(f"Okuma Hatası: {result_text}")
