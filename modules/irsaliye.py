import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
from .utils import *

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
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Sayı) | BİRİM (KG/Adet) | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz. Markdown kullanma.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def save_receipt_smart(raw_text):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    
    price_db = get_price_database(client)
    known_companies = list(price_db.keys())
    
    try:
        sh = client.open(SHEET_NAME)
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        firm_data = {}
        kota_updates = []
        
        for line in raw_text.split('\n'):
            line = line.replace("*", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 7: parts.append("0")
                
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client, known_companies)
                
                tarih = parts[1]
                urun = parts[2]
                miktar = parts[3]
                birim = parts[4].upper()
                fiyat = parts[5]
                tutar = parts[6]
                
                f_val = clean_number(fiyat)
                final_urun = resolve_product_name(urun, client)
                m_val = clean_number(miktar)
                
                if f_val == 0 and final_firma in price_db:
                    prods = list(price_db[final_firma].keys())
                    match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                    if match_prod:
                        db_item = price_db[final_firma][match_prod]
                        f_val = db_item['fiyat']
                        fiyat = str(f_val)
                        final_urun = match_prod 
                        # Miktar x Fiyat hesapla
                        tutar = f"{m_val * f_val:.2f}"
                        
                        # KOTA DÜŞ
                        current_kota = db_item['kota']
                        new_kota = current_kota - m_val
                        row_num = db_item['row']
                        kota_updates.append({'range': f'F{row_num}', 'values': [[new_kota]]})
                
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append([tarih, final_urun, miktar, birim, fiyat, "TL", tutar])
        
        msg = []
        for firma, rows in firm_data.items():
            fn = turkish_lower(firma)
            ws = None
            if fn in existing_sheets: ws = existing_sheets[fn]
            else:
                try: ws = get_or_create_worksheet(sh, firma, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "PARA BİRİMİ", "TOPLAM TUTAR"])
                except: pass
            if ws:
                ws.append_rows(rows)
                msg.append(f"{firma}: {len(rows)}")
        
        if kota_updates:
            price_ws.batch_update(kota_updates)
            msg.append(f"(Stok Güncellendi: {len(kota_updates)})")
            
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)

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
                if st.form_submit_button("Kaydet (Stoktan Düş)"):
                    s, m = save_receipt_smart(ed)
                    if s: st.success(m); del st.session_state['res']
                    else: st.error(m)
