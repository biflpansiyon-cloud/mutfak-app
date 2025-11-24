import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Mutfak Devriyesi", page_icon="🍅")

# --- BAŞLANGIÇ AYARLARI ---
def setup_credentials():
    try:
        # Google Sheets
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # Gemini API
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        
        return client
    except Exception as e:
        return None

client = setup_credentials()
SHEET_NAME = "Mutfak_Takip"

# --- ZEKİ ANALİZ FONKSİYONU (SİGORTALI) ---
def analyze_with_fallback(img):
    """
    Bu fonksiyon modelleri sırayla dener. 
    Biri hata verirse diğerine geçer.
    """
    # Denenecek modeller listesi (En iyiden en garantiye)
    models_to_try = [
        'gemini-1.5-flash',       # Hızlı ve Yeni
        'gemini-1.5-flash-latest',
        'gemini-1.5-pro',         # Güçlü
        'gemini-pro-vision'       # ESKİ AMA GARANTİ (Resimler için)
    ]
    
    prompt = """
    Sen bir muhasebe asistanısın. İrsaliye fotoğrafını analiz et.
    SADECE aşağıdaki JSON formatında veri ver. Başka hiçbir metin yazma.
    Okuyamadığın sayısal değerlere 0 yaz.
    
    [
      {"Urun": "Urun Adi", "Miktar": "5 KG", "Fiyat": "20 TL", "Tutar": "100 TL"}
    ]
    """
    
    last_error = ""
    
    # Modelleri döngüye sok
    for model_name in models_to_try:
        try:
            # Modeli hazırla
            model = genai.GenerativeModel(model_name)
            
            # İsteği gönder
            response = model.generate_content([prompt, img])
            
            # Eğer buraya geldiyse çalışmış demektir
            return True, response.text, model_name
            
        except Exception as e:
            # Hata aldıysak kaydet ve diğer modele geç
            last_error = str(e)
            print(f"{model_name} hata verdi, diğerine geçiliyor...")
            continue
            
    # Hiçbiri çalışmazsa
    return False, f"Tüm modeller denendi ama başarısız oldu. Son hata: {last_error}", "Yok"

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
st.caption("Otomatik Model Değiştiricili Sistem")

uploaded_file = st.file_uploader("İrsaliye Fotoğrafı Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="Yüklenen Belge", width=300)
    
    if st.button("Analiz Et ve Kaydet", type="primary"):
        with st.spinner("Yapay zeka modelleri deneniyor..."):
            
            # 1. Analiz (Yedekli Sistem)
            success_ai, ai_result, working_model = analyze_with_fallback(image)
            
            if success_ai:
                st.toast(f"✅ {working_model} modeli başarıyla okudu!", icon="🤖")
                
                # 2. Kayıt
                success_save, msg = save_to_sheet(ai_result)
                
                if success_save:
                    st.balloons()
                    st.success(f"✅ Harika! {msg} kalem ürün Google Sheet'e işlendi.")
                else:
                    st.error(f"Kayıt Hatası: {msg}")
            else:
                st.error("❌ Analiz Başarısız Oldu.")
                with st.expander("Hata Detayı"):
                    st.write(ai_result)
