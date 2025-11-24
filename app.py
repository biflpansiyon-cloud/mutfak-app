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

# --- BAŞLANGIÇ AYARLARI ---
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

# --- DOĞRUDAN REST API FONKSİYONU ---
def analyze_image_direct_api(image):
    api_key = st.secrets["GOOGLE_API_KEY"]
    
    # Resmi Base64 formatına çevir (Google'ın anlayacağı dil)
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    img_byte_arr = img_byte_arr.getvalue()
    base64_image = base64.b64encode(img_byte_arr).decode('utf-8')

    # API Adresi (Gemini 1.5 Flash)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    
    # Gönderilecek Paket
    payload = {
        "contents": [{
            "parts": [
                {"text": """
                Sen bir muhasebe asistanısın. İrsaliye fotoğrafını analiz et.
                SADECE aşağıdaki JSON formatında veri ver. Başka hiçbir metin yazma.
                Okuyamadığın sayısal değerlere 0 yaz.
                [{"Urun": "Urun Adi", "Miktar": "5 KG", "Fiyat": "20 TL", "Tutar": "100 TL"}]
                """},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64_image
                    }
                }
            ]
        }]
    }

    try:
        # İsteği Gönder
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 200:
            result = response.json()
            try:
                # Cevabı ayıkla
                text_response = result['candidates'][0]['content']['parts'][0]['text']
                return True, text_response
            except:
                return False, "Cevap formatı bozuk: " + str(result)
        else:
            return False, f"API Hatası ({response.status_code}): {response.text}"
            
    except Exception as e:
        return False, f"Bağlantı Hatası: {str(e)}"

def save_to_sheet(json_text):
    if not client:
        return False, "Google Sheets bağlantısı yok."
        
    try:
        clean_text = json_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except:
            return False, f"'{SHEET_NAME}' dosyası bulunamadı."
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        count = 0
        for item in data:
            row = [timestamp, item.get("Urun", "-"), item.get("Miktar", "0"), item.get("Fiyat", "0"), item.get("Tutar", "0")]
            sheet.append_row(row)
            count += 1
        return True, str(count)
    except Exception as e:
        return False, str(e)

# --- ARAYÜZ ---
st.title("🍅 Mutfak İrsaliye Kayıt")
st.caption("Direct REST API Modu")

uploaded_file = st.file_uploader("İrsaliye Fotoğrafı Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="Yüklenen Belge", width=300)
    
    if st.button("Analiz Et ve Kaydet", type="primary"):
        with st.spinner("Google API'ye bağlanılıyor..."):
            
            # 1. Analiz
            success_ai, result = analyze_image_direct_api(image)
            
            if success_ai:
                st.toast("✅ Yapay Zeka Cevap Verdi!", icon="🤖")
                
                # 2. Kayıt
                success_save, msg = save_to_sheet(result)
                
                if success_save:
                    st.balloons()
                    st.success(f"✅ Harika! {msg} kalem ürün Google Sheet'e işlendi.")
                else:
                    st.error(f"Kayıt Hatası: {msg}")
            else:
                st.error("❌ Analiz Başarısız Oldu.")
                with st.expander("Hata Detayı"):
                    st.write(result)
