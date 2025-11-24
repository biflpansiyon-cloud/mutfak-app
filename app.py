import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Evrensel", page_icon="🌍")

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

# --- ANALİZ (EVRENSEL PROMPT) ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT: 7 FARKLI FİRMA İÇİN GENEL KOMUT
    prompt = """
    Sen uzman bir veri giriş elemanısın. Bu mal teslim irsaliyesini/fişini analiz et.
    Belge baskı (print) veya el yazısı olabilir. Tablo yapısını çöz.
    
    KURALLAR:
    1. Önce TARİHİ bul (GG.AA.YYYY). Belgede yoksa bugünü baz al.
    2. Sadece ve sadece resimde görünen kalemleri listele. KAFANDAN SATIR EKLEME.
    3. Ürün isimlerini mantıklı bir şekilde okumaya çalış (Örn: 'Fleto'yu 'Fen Ladesi' yapma, metni düzelt).
    4. Fiyat/Tutar sütunu yoksa veya boşsa '0' yaz.
    
    ÇIKTI FORMATI:
    TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek (Sadece formatı anla diye veriyorum, bunu kopyalama):
    25.11.2025 | Tavuk But | 15 KG | 0 | 0
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

# --- KAYIT ---
def save_to_sheet(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    try:
        sheet = client.open(SHEET_NAME).sheet1
        rows_to_add = []
        
        lines = raw_text.split('\n')
        for line in lines:
            clean = line.strip()
            if "|" in clean:
                parts = [p.strip() for p in clean.split('|')]
                
                # Başlıkları ve Örnek satırları atla
                if "TARİH" in parts[0].upper() or "ÜRÜN" in parts[1].upper() or "BUT" in parts[1].upper():
                    continue
                
                cleaned_parts = [p if p != "" else "0" for p in parts]
                while len(cleaned_parts) < 5: cleaned_parts.append("0")
                
                rows_to_add.append(cleaned_parts[:5])
        
        if rows_to_add:
            sheet.append_rows(rows_to_add)
            return True, f"✅ {len(rows_to_add)} satır başarıyla eklendi!"
        else:
            return False, "⚠️ Eklenecek satır bulunamadı."
            
    except Exception as e:
        return False, f"Yazma Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("🌍 Mutfak Evrensel (7 Firma)")

with st.sidebar:
    st.header("⚙️ Ayarlar")
    # Manuel Model Girişi (Hala 2.5 Flash candır)
    selected_model = st.text_input("Model", "models/gemini-2.5-flash")
    st.caption("Farklı formatlardaki irsaliyeleri denerken model şaşırırsa, buradan modeli 'models/gemini-exp-1206' yapabilirsin.")

uploaded_file = st.file_uploader("İrsaliye Yükle (Herhangi bir firma)", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("İrsaliye çözümleniyor..."):
            succ, txt = analyze_receipt(image, selected_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("save_form"):
            st.info("Kontrol Et & Kaydet:")
            # Burada 'Fen Ladesi' gibi hataları elle düzeltebilirsin
            edited = st.text_area("Veriler", st.session_state['ocr_result'], height=100)
            
            if st.form_submit_button("💾 Kaydet"):
                s_save, msg = save_to_sheet(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    # Kayıt bitince hafızayı temizle ki yeni fişe hazır olsun
                    del st.session_state['ocr_result'] 
                else:
                    st.error(msg)
