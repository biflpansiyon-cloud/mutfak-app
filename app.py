import streamlit as st
from PIL import Image
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Mutfak Devriyesi", page_icon="🍅")

# --- 1. MODEL SEÇİM FONKSİYONU (ZIRHLI KISIM) ---
def get_working_model():
    """
    Bu fonksiyon sırasıyla en yeni modelleri dener.
    Eğer sunucu 1.5-flash'ı tanımazsa, otomatik olarak pro-vision'a geçer.
    Böylece '404 Model Not Found' hatası almazsın.
    """
    model_list = [
        'gemini-1.5-flash',          # En hızlı ve yeni (Hedefimiz bu)
        'gemini-1.5-flash-latest',   # Alternatif isim
        'gemini-1.5-pro',            # Daha güçlü ama yavaş
        'gemini-pro-vision'          # Eski ama sağlam (Yedek lastik)
    ]
    
    active_model = None
    active_name = ""
    
    # API Anahtarını al
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        genai.configure(api_key=api_key)
    except Exception as e:
        return None, f"API Anahtarı Hatası: {e}"

    # Modelleri tek tek dene
    for model_name in model_list:
        try:
            model = genai.GenerativeModel(model_name)
            # Eğer buraya kadar hata vermediyse model çalışıyor demektir
            active_model = model
            active_name = model_name
            break # Çalışanı bulduk, döngüden çık
        except:
            continue # Bu çalışmadı, sıradakine geç
            
    if active_model:
        return active_model, active_name
    else:
        return None, "Hiçbir model yüklenemedi. Kütüphane sürümünü kontrol et."

# --- 2. GOOGLE SHEETS BAĞLANTISI ---
def connect_to_sheets():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # Secrets'tan servis hesabı bilgilerini al
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        return None

# --- BAŞLANGIÇ AYARLARI ---
model, model_name = get_working_model()
client = connect_to_sheets()
SHEET_NAME = "Mutfak_Takip" # Google Sheet dosyanın adı tam olarak bu olmalı

# --- ANA FONKSİYONLAR ---
def analyze_image(img):
    if not model:
        return "HATA: Yapay Zeka Modeli Yüklenemedi."
        
    prompt = """
    Sen uzman bir muhasebe asistanısın. Yüklenen irsaliye fotoğrafını analiz et.
    SADECE ve SADECE aşağıdaki JSON formatında bir liste ver.
    Başka hiçbir açıklama, yorum veya metin yazma.
    Okuyamadığın sayısal değerlere 0 yaz.
    
    [
      {"Urun": "Domates", "Miktar": "5 KG", "Fiyat": "25 TL", "Tutar": "125 TL"}
    ]
    """
    try:
        response = model.generate_content([prompt, img])
        return response.text
    except Exception as e:
        return f"Analiz Hatası: {str(e)}"

def save_to_sheet(json_text):
    if not client:
        return False, "Google Sheets bağlantısı kurulamadı. Secrets ayarlarını kontrol et."
        
    try:
        # Gelen veriyi temizle (Bazen ```json etiketiyle gelir)
        clean_text = json_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_text)
        
        # Dosyayı aç
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except:
            return False, f"'{SHEET_NAME}' isimli Google Sheet dosyası bulunamadı."
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        added_count = 0
        
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
            
        return True, f"{added_count}"
        
    except json.JSONDecodeError:
        return False, "Fiş okunamadı veya yapay zeka bozuk veri gönderdi. Lütfen tekrar dene."
    except Exception as e:
        return False, f"Kayıt Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("🍅 Mutfak İrsaliye Kayıt")

if model:
    st.info(f"✅ Sistem Hazır | Aktif Zeka: {model_name}")
else:
    st.error("❌ Kritik Hata: Yapay Zeka Başlatılamadı!")

uploaded_file = st.file_uploader("İrsaliye Fotoğrafı Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="Yüklenen Belge", width=300)
    
    if st.button("Analiz Et ve Kaydet", type="primary"):
        with st.spinner("Yapay zeka fişi okuyor..."):
            # 1. Analiz
            result_text = analyze_image(image)
            
            # Hata kontrolü
            if "HATA" in result_text or "Hatası" in result_text:
                st.error(result_text)
            else:
                # 2. Kayıt
                success, msg = save_to_sheet(result_text)
                
                if success:
                    st.balloons()
                    st.success(f"✅ İşlem Tamam! {msg} kalem ürün tabloya işlendi.")
                else:
                    st.error(f"Hata: {msg}")
                    with st.expander("Teknik Detay"):
                        st.code(result_text)
