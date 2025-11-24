import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="Mutfak Future", page_icon="🚀")

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

# --- MODEL LİSTESİNİ ÇEK (API'DEN) ---
def fetch_google_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Sadece içerik üretenleri al
            return [m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']]
        return []
    except:
        return []

# --- ANALİZ FONKSİYONU ---
def analyze_receipt(image, selected_model):
    api_key = st.secrets["GOOGLE_API_KEY"]
    # Model adındaki "models/" kısmını temizle (bazı durumlarda çift olmasın diye)
    clean_model = selected_model.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT: EVRENSEL VE ESNEK
    prompt = """
    Sen uzman bir veri giriş elemanısın. Bu mal teslim irsaliyesini analiz et.
    Belge el yazısı veya baskı olabilir.
    
    GÖREVLER:
    1. TARİHİ bul (GG.AA.YYYY). Belgede yoksa bugünü yaz.
    2. Kalemleri listele.
    3. Miktarları ve birimleri (KG, Adet, Tepsi, Teneke) olduğu gibi koru.
    4. El yazısı hatalarını mantık çerçevesinde düzelt (Örn: 'Tepsi' mantıklıysa kalsın, ama 'Teneke'ye benziyorsa düzelt).
    
    ÇIKTI FORMATI:
    TARİH | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
    
    Örnek:
    23.10.2025 | Yeşil Zeytin | 10 KG (1 Teneke) | 0 | 0
    
    Sadece veriyi ver.
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

# --- KAYIT FONKSİYONU ---
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
                
                # Başlıkları atla
                if "TARİH" in parts[0].upper() or "ÜRÜN" in parts[1].upper(): continue
                
                # Boşlukları 0 yap
                cleaned_parts = [p if p != "" else "0" for p in parts]
                # Sütun tamamla
                while len(cleaned_parts) < 5: cleaned_parts.append("0")
                
                rows_to_add.append(cleaned_parts[:5])
        
        if rows_to_add:
            sheet.append_rows(rows_to_add)
            return True, f"✅ {len(rows_to_add)} satır kaydedildi."
        else:
            return False, "⚠️ Eklenecek satır yok."
            
    except Exception as e:
        return False, f"Yazma Hatası: {str(e)}"

# --- ARAYÜZ ---
st.title("🚀 Mutfak Future")

with st.sidebar:
    st.header("🛠️ Model Ayarları")
    
    # 1. Sabit Favori Modellerimiz (Garanti Çalışanlar)
    favorite_models = [
        "models/gemini-2.5-flash",  # <--- KRA (Varsayılan)
        "models/gemini-exp-1206",   # Keskin Göz
        "models/gemini-1.5-flash",  # Yedek Toyota
        "models/gemini-1.5-pro"     # El Yazısı Uzmanı
    ]
    
    # 2. Google'dan Yenileri Çek Butonu
    if st.button("Listeyi Google'dan Güncelle"):
        fetched_models = fetch_google_models()
        if fetched_models:
            # Favorilerle gelenleri birleştir (Tekrarı önle)
            all_models = list(set(favorite_models + fetched_models))
            # Alfabetik sırala ama favorileri başa alabiliriz (karmaşık olmasın diye düz sıraladım)
            st.session_state['model_list'] = sorted(all_models)
            st.success("Liste güncellendi!")
        else:
            st.warning("API'den model çekilemedi, varsayılanlar kullanılıyor.")
    
    # Listeyi belirle: Ya session'daki ya da favoriler
    current_list = st.session_state.get('model_list', favorite_models)
    
    # 3. SEÇİM KUTUSU (Varsayılan 2.5 Flash olacak şekilde ayarla)
    default_index = 0
    target_model = "models/gemini-2.5-flash"
    
    # Eğer listemizde 2.5 flash varsa onun sırasını bul
    if target_model in current_list:
        default_index = current_list.index(target_model)
    
    selected_model = st.selectbox(
        "Kullanılacak Model:", 
        current_list, 
        index=default_index
    )
    
    st.info(f"Seçili: **{selected_model}**")
    st.divider()
    
    # Bağlantı Testi (Her zaman elinin altında olsun)
    if st.button("Google Sheets Test"):
        c, _ = get_gspread_client()
        if c: st.success("Bağlantı OK!")
        else: st.error("Bağlantı Yok!")

# --- ANA EKRAN ---
uploaded_file = st.file_uploader("İrsaliye / Fiş Yükle", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, width=300)
    
    if st.button("Analiz Et", type="primary"):
        with st.spinner(f"{selected_model} okuyor..."):
            succ, txt = analyze_receipt(image, selected_model)
            st.session_state['ocr_result'] = txt
            
    if 'ocr_result' in st.session_state:
        with st.form("edit_save"):
            st.write("▼ **Verileri Kontrol Et & Düzenle:**")
            edited = st.text_area("Sonuçlar", st.session_state['ocr_result'], height=150)
            
            if st.form_submit_button("💾 Kaydet"):
                s_save, msg = save_to_sheet(edited)
                if s_save:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['ocr_result']
                else:
                    st.error(msg)
