import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Dedektif", page_icon="🕵️‍♂️")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"  # Dosya adın Google Drive'da harfi harfine bu olmalı

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

# --- ANALİZ ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Muhasebe asistanı olarak bu fişi analiz et.
    1. TARİHİ bul (GG.AA.YYYY). Yoksa bugünü yaz.
    2. Kalem kalem ürünleri çıkar.
    3. Ürün isimlerini düzgün yaz.
    
    ÇIKTI FORMATI (Aralara | koy):
    TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek:
    24.11.2025 | Domates | 5 KG | 10 TL | 50 TL
    
    Sadece veriyi ver, başlık yazma.
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

# --- KAYIT (GARANTİLİ) ---
def save_to_sheet(raw_text):
    client, email_or_err = get_gspread_client()
    if not client: return False, f"Bağlantı Hatası: {email_or_err}"
    
    try:
        # Dosyayı bulmaya çalış
        try:
            sheet = client.open(SHEET_NAME).sheet1
        except gspread.SpreadsheetNotFound:
            return False, f"DOSYA BULUNAMADI! Lütfen Google Drive'daki dosyanın adının tam olarak '{SHEET_NAME}' olduğundan emin ol."
        except Exception as e:
            return False, f"Dosya Açma Hatası: {str(e)}"

        rows_to_add = []
        for line in raw_text.split('\n'):
            clean = line.strip()
            if "|" in clean and clean.count("|") >= 2:
                parts = [p.strip() for p in clean.split('|')]
                if "TARİH" in parts[0].upper(): continue
                while len(parts) < 5: parts.append("0")
                rows_to_add.append(parts[:5])
        
        if rows_to_add:
            sheet.append_rows(rows_to_add) # Toplu ekleme daha güvenlidir
            return True, f"{len(rows_to_add)} satır eklendi."
        else:
            return False, "Eklenecek geçerli satır bulunamadı."
            
    except Exception as e:
        return False, f"Yazma Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("🕵️‍♂️ Mutfak Dedektif")

# --- YAN MENÜ & TEST ---
with st.sidebar:
    st.header("🛠️ Sorun Giderme")
    
    if st.button("⚠️ Google Sheets Test Et"):
        with st.status("Bağlantı kontrol ediliyor...") as status:
            client, email = get_gspread_client()
            if client:
                st.write(f"✅ Robot Girişi Başarılı: `{email}`")
                try:
                    sh = client.open(SHEET_NAME)
                    st.write(f"✅ Dosya Bulundu: `{SHEET_NAME}`")
                    ws = sh.sheet1
                    st.write("✅ Sayfa Erişimi Tamam")
                    
                    # Test Yazısı
                    ws.append_row([str(datetime.now()), "TEST", "BAĞLANTISI", "BAŞARILI", "OK"])
                    st.success("TEST BAŞARILI! Tablona bir 'TEST' satırı eklendi, kontrol et.")
                except gspread.SpreadsheetNotFound:
                    st.error(f"❌ '{SHEET_NAME}' dosyası bulunamadı!")
                    st.warning("İPUCU: Dosya adının birebir aynı olduğuna ve robot mailine 'Editör' yetkisi verdiğine emin ol.")
                except Exception as e:
                    st.error(f"❌ Hata: {e}")
            else:
                st.error("❌ Robot giriş yapamadı. Secrets ayarlarını kontrol et.")

    st.divider()
    
    # Model Listesi
    if st.button("Modelleri Yenile"):
        api_key = st.secrets["GOOGLE_API_KEY"]
        try:
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}")
            models = sorted([m['name'] for m in r.json().get('models', []) if 'generateContent' in m['supportedGenerationMethods']])
            st.session_state['models'] = models
        except: pass
    
    models = st.session_state.get('models', [])
    # 2.5 Flash yoksa Exp 1206 seçelim
    def_ix = 0
    for i, m in enumerate(models):
        if "2.5-flash" in m: def_ix = i; break
        
    sel_model = st.selectbox("Model", models, index=def_ix) if models else st.text_input("Model", "models/gemini-exp-1206")

# --- ANA EKRAN ---
uploaded_file = st.file_uploader("Fiş Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner("Okunuyor..."):
            succ, txt = analyze_receipt(image, sel_model)
            
            with st.form("save_form"):
                edited = st.text_area("Veriler", txt, height=150)
                if st.form_submit_button("💾 Kaydet"):
                    s_save, msg = save_to_sheet(edited)
                    if s_save:
                        st.balloons()
                        st.success(msg)
                    else:
                        st.error(msg)
