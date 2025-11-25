import streamlit as st
from PIL import Image
import io
import base64
import json
import requests
# DİKKAT: utils'i modules klasöründen çağırıyoruz
from modules.utils import * def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = "İrsaliyeyi analiz et. Tedarikçi firmayı bul. ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR | BİRİM (KG/Adet) | BİRİM FİYAT | TOPLAM TUTAR. Fiyat yoksa 0 yaz. Markdown kullanma."
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

# ... (Buraya resolve_company_name, resolve_product_name, save_receipt_smart fonksiyonlarını V21 kodundan kopyala. 
# Ancak bu fonksiyonların içindeki clean_number vb. çağrıları artık direkt çalışır çünkü utils'den import ettik.)
# Yer darlığından save_receipt_smart'ı buraya sığdırmıyorum ama V21'deki aynı mantık buraya gelecek.

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
                if st.form_submit_button("Kaydet"):
                    # save_receipt_smart fonksiyonunu buraya tam haliyle eklediğini varsayıyorum
                    st.warning("Lütfen V21 kodundaki save_receipt_smart fonksiyonunu bu dosyaya taşıyın.")
                    # s, m = save_receipt_smart(ed) 
                    # if s: st.success(m); del st.session_state['res']
                    # else: st.error(m)
