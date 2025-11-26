import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd # Pandas eklendi
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
    ÇIKTI FORMATI (Her satıra): TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR | BİRİM (KG/Adet/Koli) | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz. Markdown kullanma. Sadece veriyi ver.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def text_to_dataframe(raw_text):
    """ AI çıktısını düzenlenebilir tabloya çevirir """
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        line = line.replace("*", "").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split('|')]
            # Başlık satırını atla
            if "TEDARİKÇİ" in parts[0].upper(): continue
            # Eksik sütunları tamamla
            while len(parts) < 7: parts.append("0")
            
            data.append({
                "TEDARİKÇİ": parts[0],
                "TARİH": parts[1],
                "ÜRÜN ADI": parts[2],
                "MİKTAR": parts[3],
                "BİRİM": parts[4],
                "BİRİM FİYAT": parts[5],
                "TOPLAM TUTAR": parts[6]
            })
    return pd.DataFrame(data)

def save_receipt_dataframe(df):
    """ Artık metin değil, DÜZELTİLMİŞ TABLOYU kaydeder """
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
        
        # DataFrame satırlarını dön
        for index, row in df.iterrows():
            ocr_raw_name = str(row["TEDARİKÇİ"])
            final_firma = resolve_company_name(ocr_raw_name, client, known_companies)
            
            tarih = str(row["TARİH"])
            urun = str(row["ÜRÜN ADI"])
            miktar = str(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            fiyat = str(row["BİRİM FİYAT"])
            tutar = str(row["TOPLAM TUTAR"])
            
            f_val = clean_number(fiyat)
            m_val = clean_number(miktar)
            final_urun = resolve_product_name(urun, client)
            
            # Fiyat ve Kota Mantığı
            if f_val == 0 and final_firma in price_db:
                prods = list(price_db[final_firma].keys())
                match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                
                if match_prod:
                    db_item = price_db[final_firma][match_prod]
                    f_val = db_item['fiyat']
                    fiyat = str(f_val)
                    final_urun = match_prod # İsmi veritabanındakiyle eşle
                    
                    # Tutar hesapla (Eğer kullanıcı girmediyse)
                    if clean_number(tutar) == 0:
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
            
        return True, " | ".join(msg) + " satır eklendi."
            
    except Exception as e: return False, str(e)

def render_page(sel_model):
    st.header("📝 İrsaliye Girişi")
    f = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])
    
    if f:
        img = Image.open(f)
        st.image(img, width=300)
        
        if st.button("Analiz Et"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_receipt_image(img, sel_model)
                if s:
                    # Metni Tabloya Çevir ve Kaydet
                    df = text_to_dataframe(raw_text)
                    st.session_state['irsaliye_df'] = df
                else:
                    st.error(f"Hata: {raw_text}")
    
    # EDİTÖR EKRANI
    if 'irsaliye_df' in st.session_state:
        st.info("👇 Tabloyu incele, hataları hücreye tıklayıp düzelt, sonra Kaydet'e bas.")
        
        # Data Editor (Excel gibi düzenleme)
        edited_df = st.data_editor(
            st.session_state['irsaliye_df'],
            num_rows="dynamic", # Satır ekleyip silebilirsin
            use_container_width=True
        )
        
        if st.button("💾 Tabloyu Kaydet (Stoktan Düş)"):
            with st.spinner("Kaydediliyor..."):
                success, msg = save_receipt_dataframe(edited_df)
                if success:
                    st.balloons()
                    st.success(msg)
                    del st.session_state['irsaliye_df'] # Temizle
                else:
                    st.error(f"Kayıt Hatası: {msg}")
