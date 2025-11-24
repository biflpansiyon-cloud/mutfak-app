import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Mutfak Flash", page_icon="⚡")

# --- GOOGLE SHEETS BAĞLANTISI ---
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

# --- ANALİZ (TARİHLİ & FLASH DOSTU) ---
def analyze_receipt(image):
    api_key = st.secrets["GOOGLE_API_KEY"]
    # KOTA DOSTU MODEL: FLASH (Varsayılan)
    model_name = "gemini-1.5-flash"
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT: Tarihi bul ve her satırın başına ekle
    prompt = """
    Sen bir muhasebe asistanısın. Bu irsaliyeyi/fişi oku.
    
    1. Önce belgenin üzerindeki TARİHİ bul (GG.AA.YYYY formatına çevir).
    2. Sonra kalem kalem ürünleri çıkar.
    3. Eğer tarih yoksa bugünün tarihini at.
    
    ÇIKTI FORMATI (Aralara | koy):
    TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek:
    24.11.2025 | Domates | 5 KG | 10 TL | 50 TL
    24.11.2025 | Salatalık | 3 KG | 5 TL | 15 TL
    
    Not: Her satırın başına tarihi tekrar yaz. Başlık satırı yazma. Sadece veriyi ver.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }],
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
            return False, f"Hata: {response.text}"
            
        result = response.json()
        if 'candidates' in result and len(result['candidates']) > 0:
             candidate = result['candidates'][0]
             if 'content' in candidate and 'parts' in candidate['content']:
                 return True, candidate['content']['parts'][0]['text']
        return False, "Boş cevap."
            
    except Exception as e:
        return False, f"Bağlantı Hatası: {str(e)}"

# --- KAYIT (TARİHİ METİNDEN ALAN VERSİYON) ---
def save_lines(raw_text):
    if not client: return False, "Sheets Bağlı Değil"
    try:
        sheet = client.open(SHEET_NAME).sheet1
        count = 0
        
        lines = raw_text.split('\n')
        for line in lines:
            clean_line = line.strip()
            # En az 4 tane | işareti varsa (Tarih|Urun|Miktar|Fiyat|Tutar)
            if "|" in clean_line and clean_line.count("|") >= 3:
                
                parts = [p.strip() for p in clean_line.split('|')]
                
                # Başlık satırıysa atla
                if "TARİH" in parts[0].upper() or "URUN" in parts[1].upper():
                    continue
                
                # Eksik sütunları 0 ile doldur (Toplam 5 sütun olmalı)
                while len(parts) < 5: parts.append("0")
                
                # Sheets'e Yaz (İlk 5 sütunu al: Tarih, Ürün, Miktar, Fiyat, Tutar)
                try:
                    sheet.append_row(parts[:5])
                    count += 1
                except Exception as inner_e:
                    # 200 hatasını yut (Başarıdır)
                    if "200" in str(inner_e):
                        count += 1
                        continue
                    else:
                        return False, str(inner_e)
                        
        return True, str(count)
    except Exception as e: 
        if "200" in str(e): return True, "Başarılı"
        return False, str(e)

# --- ARAYÜZ ---
st.title("⚡ Mutfak Flash (Hızlı & Tarihli)")
st.info("Aktif Model: Gemini 1.5 Flash (Kota Dostu)")

uploaded_file = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Tarih ve ürünler okunuyor..."):
            success, result_text = analyze_receipt(image)
            
            if success:
                st.toast("Okuma yapıldı, lütfen kontrol et.")
                
                # DÜZELTME ALANI (Ana Formun İçine Aldık)
                with st.form("duzeltme_formu"):
                    st.write("▼ **Aşağıdaki kutudan hataları düzeltip KAYDET'e bas:**")
                    edited_text = st.text_area("Düzenle", result_text, height=150, help="Format: Tarih | Ürün | Miktar | Fiyat | Tutar")
                    
                    submit_btn = st.form_submit_button("✅ Google Sheets'e Kaydet")
                    
                    if submit_btn:
                        save_success, msg = save_lines(edited_text)
                        if save_success:
                            st.balloons()
                            st.success(f"Kaydedildi! {msg} satır işlendi.")
                        else:
                            st.error(f"Kayıt Hatası: {msg}")
            else:
                st.error("Okuma Başarısız. Lütfen tekrar dene.")
