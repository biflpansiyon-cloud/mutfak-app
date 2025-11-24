import streamlit as st
from PIL import Image
from datetime import datetime
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Mutfak Geleceği", page_icon="🍌")

# --- GOOGLE SHEETS ---
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

# --- MODELLERİ ÇEK ---
def list_available_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Listeyi alfabetik sırala ki bulması kolay olsun
            models = sorted([m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']])
            return models
        return []
    except:
        return []

# --- ANALİZ (SANSÜR KIRICI EKLENDİ) ---
def analyze_future(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    # Model ismindeki "models/" kısmını temizle
    clean_model = selected_model.replace("models/", "")
    
    # Resmi Hazırla
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Sen uzman bir muhasebecisin. Bu mal teslim fişini analiz et.
    Görevin: Ürün, Miktar, Fiyat ve Tutar bilgilerini çıkarmak.
    Metin EL YAZISI olabilir, rakamlara dikkat et.
    Eğer fiyat/tutar yoksa '0' yaz.
    
    Çıktı Formatı (Aralara | koy):
    URUN ADI | MIKTAR | BIRIM FIYAT | TOPLAM TUTAR
    
    Örnek:
    Dana Biftek | 2,5 KG | 0 | 0
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }],
        # İŞTE BURASI ÇOK ÖNEMLİ: GÜVENLİK FİLTRELERİNİ KAPATIYORUZ
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code != 200:
            return False, f"Hata ({response.status_code}): {response.text}"
            
        result = response.json()
        
        # Cevabı al
        if 'candidates' in result and len(result['candidates']) > 0:
            candidate = result['candidates'][0]
            # Eğer filtreye takıldıysa 'finishReason' farklı döner
            if candidate.get('finishReason') == 'SAFETY':
                return False, "Google Güvenlik Filtresine Takıldı! (Yine de sansür ayarını deldi)"
                
            if 'content' in candidate and 'parts' in candidate['content']:
                return True, candidate['content']['parts'][0]['text']
        
        return False, f"Boş Cevap: {str(result)}"
            
    except Exception as e:
        return False, f"Bağlantı Hatası: {str(e)}"

def save_lines(raw_text):
    if not client: return False, "Google Sheets Bağlı Değil"
    try:
        sheet = client.open(SHEET_NAME).sheet1
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        count = 0
        
        for line in raw_text.split('\n'):
            if "|" in line and len(line) > 5:
                parts = [p.strip() for p in line.split('|')]
                # Eksikleri tamamla
                while len(parts) < 4: parts.append("0")
                
                sheet.append_row([timestamp] + parts[:4])
                count += 1
        return True, str(count)
    except Exception as e: return False, str(e)

# --- ARAYÜZ ---
st.title("🍌 Mutfak Geleceği (Pro)")

with st.sidebar:
    if st.button("Modelleri Tara"):
        st.session_state['models'] = list_available_models()
    
    # Listeyi session'dan al
    models_list = st.session_state.get('models', [])
    
    # Eğer liste boşsa manuel giriş kutusu koy (Garanti olsun)
    if not models_list:
        selected_model = st.text_input("Model Adı (Elle Yaz)", "gemini-exp-1206")
    else:
        # En iyi modeli varsayılan yapmaya çalış
        default_index = 0
        if 'models/gemini-exp-1206' in models_list:
            default_index = models_list.index('models/gemini-exp-1206')
            
        selected_model = st.selectbox("Model Seç", models_list, index=default_index)

uploaded_file = st.file_uploader("İrsaliye", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et (Filtresiz)", type="primary"):
        with st.spinner(f"{selected_model} çalışıyor..."):
            success, result = analyze_future(image, selected_model)
            
            with st.expander("Sonuç Metni"):
                if success: st.success(result)
                else: st.error(result)
            
            if success:
                s_save, msg = save_lines(result)
                if s_save:
                    st.balloons()
                    st.success(f"✅ {msg} satır kaydedildi!")
                else:
                    st.error(f"Kayıt Hatası: {msg}")
