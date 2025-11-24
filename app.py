import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- GÜVENLİK VE AYARLAR ---
# Bu bilgileri Streamlit Secrets kısmından çekeceğiz
try:
    # Google Sheets Ayarları
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # Secrets'tan gelen JSON verisini kullan
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # Gemini Ayarları
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    model = genai.GenerativeModel('gemini-1.5-flash')
    
except Exception as e:
    st.error(f"Kurulum Hatası: {e}. Lütfen Secrets ayarlarını kontrol et.")
    st.stop()

# Google Sheet Adı (Dosyanın adı birebir aynı olmalı)
SHEET_NAME = "Mutfak_Takip"

def analyze_image(img):
    prompt = """
    Sen bir muhasebe uzmanısın. İrsaliye fotoğrafını analiz et.
    SADECE aşağıdaki JSON formatında çıktı ver. 
    Sayısal değerler dışında metin yazma. Okuyamadığına '0' veya '-' yaz.
    
    [
      {"Urun": "Domates", "Miktar": "5 KG", "Fiyat": "20 TL", "Tutar": "100 TL"}
    ]
    """
    response = model.generate_content([prompt, img])
    return response.text

def save_to_sheets(json_text):
    try:
        # JSON Temizliği
        clean_json = json_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        
        # Google Sheet'e Bağlan
        sheet = client.open(SHEET_NAME).sheet1
        
        added_count = 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        for item in data:
            row = [
                timestamp,
                item.get("Urun", "-"),
                item.get("Miktar", "0"),
                item.get("Fiyat", "0"),
                item.get("Tutar", "0")
            ]
            sheet.append_row(row)
            added_count += 1
            
        return True, added_count
    except Exception as e:
        return False, str(e)

# --- ARAYÜZ ---
st.set_page_config(page_title="Mutfak Devriyesi", page_icon="📝")
st.title("📝 Mutfak İrsaliye Kayıt")

img_file = st.file_uploader("İrsaliye Yükle", type=["jpg", "png", "jpeg"])

if img_file:
    image = Image.open(img_file)
    st.image(image, caption="Yüklenen İrsaliye", width=300)
    
    if st.button("Analiz Et ve Tabloya İşle", type="primary"):
        with st.spinner("Yapay zeka fişi okuyor ve Google Sheets'e yazıyor..."):
            res_text = analyze_image(image)
            success, msg = save_to_sheets(res_text)
            
            if success:
                st.success(f"✅ Başarılı! {msg} kalem ürün Google Sheet'e eklendi.")
            else:
                st.error(f"Hata: {msg}")
                with st.expander("Teknik Detay"):
                    st.code(res_text)