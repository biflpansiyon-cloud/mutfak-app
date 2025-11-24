import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Özgür", page_icon="🗽")

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

# --- MODELLERİ CANLI ÇEK (SENİN LİSTEN NE İSE O) ---
def list_available_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Sadece içerik üretebilen modelleri al ve sırala
            return sorted([m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']])
        return []
    except:
        return []

# --- ANALİZ FONKSİYONU ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    
    # Seçilen modelin başındaki "models/" kısmını temizleyelim
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT: Tarih bul + Ürünleri dök
    prompt = """
    Sen bir muhasebe asistanısın. Bu belgeyi analiz et.
    
    1. Belgenin üzerindeki TARİHİ bul (GG.AA.YYYY formatı). Tarih yoksa bugünü yaz.
    2. Kalem kalem ürünleri çıkar.
    3. Ürün isimlerini mantıklı yaz (Biftek'e Böğürtlen deme).
    
    ÇIKTI FORMATI (Aralara | koy):
    TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek:
    24.11.2025 | Dana Kıyma | 5 KG | 100 TL | 500 TL
    
    Sadece veriyi ver, başlık satırı yazma.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }],
        # Sansürleri kaldır ki boş dönmesin
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        # Hata varsa göster
        if response.status_code != 200:
            return False, f"Model Hatası ({response.status_code}): {response.text}"
            
        result = response.json()
        if 'candidates' in result and len(result['candidates']) > 0:
             candidate = result['candidates'][0]
             if 'content' in candidate and 'parts' in candidate['content']:
                 return True, candidate['content']['parts'][0]['text']
        return False, "Yapay zeka boş cevap döndü."
            
    except Exception as e:
        return False, f"Bağlantı Hatası: {str(e)}"

# --- KAYIT FONKSİYONU ---
def save_lines(raw_text):
    if not client: return False, "Sheets Bağlı Değil"
    try:
        sheet = client.open(SHEET_NAME).sheet1
        count = 0
        
        lines = raw_text.split('\n')
        for line in lines:
            clean_line = line.strip()
            # En az 3 tane ayıraç (|) varsa geçerli satırdır
            if "|" in clean_line and clean_line.count("|") >= 2:
                parts = [p.strip() for p in clean_line.split('|')]
                
                # Başlık satırını atla
                if "TARİH" in parts[0].upper() or "URUN" in parts[1].upper():
                    continue
                
                # Sütun sayısını 5'e tamamla
                while len(parts) < 5: parts.append("0")
                
                try:
                    sheet.append_row(parts[:5])
                    count += 1
                except Exception as inner_e:
                    if "200" in str(inner_e): # Hata değil başarı
                        count += 1
                        continue
                    else:
                        return False, str(inner_e)
                        
        return True, str(count)
    except Exception as e: 
        if "200" in str(e): return True, "Başarılı"
        return False, str(e)

# --- ARAYÜZ ---
st.title("🗽 Mutfak Özgür (Modelini Seç)")

# YAN MENÜ: MODEL SEÇİMİ GERİ GELDİ
with st.sidebar:
    if st.button("Model Listesini Yenile"):
        st.session_state['models'] = list_available_models()
        if not st.session_state['models']:
            st.error("Model bulunamadı veya API hatası.")
    
    models_list = st.session_state.get('models', [])
    
    # Liste boşsa manuel giriş, doluysa seçim kutusu
    if not models_list:
        selected_model = st.text_input("Model Adı (Elle Yaz)", "models/gemini-2.5-flash")
    else:
        # Akıllı varsayılan: Varsa 2.5-flash seç (Yoksa ilkini seç)
        default_ix = 0
        for i, m in enumerate(models_list):
            if "2.5-flash" in m:
                default_ix = i
                break
        selected_model = st.selectbox("Kullanılacak Model:", models_list, index=default_ix)
        
    st.info(f"Seçili: {selected_model}")

uploaded_file = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner(f"{selected_model} fişi okuyor..."):
            
            success, result_text = analyze_receipt(image, selected_model)
            
            if success:
                st.toast("Okuma yapıldı.")
                
                # DÜZELTME FORMU
                with st.form("duzeltme"):
                    st.write("▼ **Sonuçları kontrol et, gerekirse düzelt ve KAYDET:**")
                    edited_text = st.text_area("Veriler", result_text, height=150, help="Tarih | Ürün | Miktar | Fiyat | Tutar")
                    
                    if st.form_submit_button("✅ Google Sheets'e Kaydet"):
                        s_save, msg = save_lines(edited_text)
                        if s_save:
                            st.balloons()
                            st.success(f"İşlem Tamam! {msg} satır kaydedildi.")
                        else:
                            st.error(f"Kayıt Hatası: {msg}")
            else:
                st.error(f"Hata: {result_text}")
