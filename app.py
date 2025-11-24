import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- MODEL SEÇİM FONKSİYONU ---
def get_model():
    # Model isimlerini sırayla dene. Biri mutlaka çalışacaktır.
    model_names = ['gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-pro-vision']
    
    for name in model_names:
        try:
            model = genai.GenerativeModel(name)
            # Test etmek için boş bir model çağrısı yapmıyoruz, sadece tanımlıyoruz.
            return model, name
        except:
            continue
    return None, "Hiçbir model bulunamadı"

# --- GÜVENLİK VE AYARLAR ---
try:
    # 1. Google Sheets Bağlantısı
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 2. Gemini API Ayarı
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    
    # 3. Modeli Seç
    model, model_name = get_model()
    
except Exception as e:
    st.error(f"Sistem Hatası: {e}")
    st.stop()

# Google Sheet Adı
SHEET_NAME = "Mutfak_Takip"

def analyze_image(img):
    if not model:
        return "Model yüklenemedi."
        
    prompt = """
    Sen bir muhasebe uzmanısın. İrsaliye fotoğrafını analiz et.
    SADECE aşağıdaki JSON formatında çıktı ver. 
    Sayısal değerler dışında metin yazma. Okuyamadığına '0' yaz.
    
    [
      {"Urun": "Urun Adi", "Miktar": "5 KG", "Fiyat": "20 TL", "Tutar": "100 TL"}
    ]
    """
    try:
        response = model.generate_content([prompt, img])
        return response.text
    except Exception as e:
        return f"API Hatası: {str(e)}"

def save_to_sheets(json_text):
    try:
        # Hata mesajı döndüyse işlemi durdur
        if "API Hatası" in json_text or "Model" in json_text:
            return False, json_text

        # JSON Temizliği
        clean_json = json_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        
        # Google Sheet'e Bağlan
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except gspread.SpreadsheetNotFound:
            return False, f"'{SHEET_NAME}' adında bir Google Sheet bulunamadı. Lütfen dosya adını kontrol edin."
        
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
    except json.JSONDecodeError:
        return False, "Yapay zeka anlaşılır bir veri üretemedi. Fiş net mi?"
    except Exception as e:
        return False, str(e)

# --- ARAYÜZ ---
st.set_page_config(page_title="Mutfak Devriyesi", page_icon="🍅")
st.title("🍅 Mutfak İrsaliye Kayıt")

if model_name:
    st.caption(f"Aktif Yapay Zeka Modeli: {model_name}")
else:
    st.error("Yapay Zeka Modeli Başlatılamadı!")

img_file = st.file_uploader("İrsaliye Yükle", type=["jpg", "png", "jpeg"])

if img_file:
    image = Image.open(img_file)
    st.image(image, caption="Analiz edilecek fiş", width=300)
    
    if st.button("Analiz Et ve Tabloya İşle", type="primary"):
        with st.spinner("Fiş okunuyor..."):
            res_text = analyze_image(image)
            success, msg = save_to_sheets(res_text)
            
            if success:
                st.balloons()
                st.success(f"✅ Başarılı! {msg} kalem ürün tabloya eklendi.")
            else:
                st.error(f"Hata: {msg}")
                with st.expander("Teknik Detay (Hata Mesajı)"):
                    st.text(res_text)
