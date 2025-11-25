import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
from utils import * # Ortak fonksiyonları çek

def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    İrsaliyeyi analiz et. Tedarikçi firmayı bul.
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Sayı) | BİRİM (KG/L/Adet) | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz. Markdown kullanma.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

# ... (Buraya eski app.py'deki save_receipt_smart, resolve_company_name vb. fonksiyonlarını taşıyacağız)
# Ama daha temiz olması için resolve fonksiyonlarını utils'den de çağırabiliriz.
# Özetle: O uzun save_receipt_smart fonksiyonunu buraya yapıştır.

def render_page(sel_model):
    st.header("📝 İrsaliye Girişi")
    f = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])
    if f:
        img = Image.open(f)
        st.image(img, width=300)
        if st.button("Analiz Et"):
            with st.spinner("Okunuyor..."):
                s, r = analyze_receipt_image(img, sel_model)
                st.session_state['res'] = r
        if 'res' in st.session_state:
            with st.form("save"):
                ed = st.text_area("Veriler", st.session_state['res'], height=150)
                # Burada save_receipt_smart fonksiyonunu çağıracaksın (utils.py'den import edilmiş veya buraya taşınmış)
                st.warning("Kayıt fonksiyonu buraya entegre edilecek (Kod kısalığı için özet geçtim)")
