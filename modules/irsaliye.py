import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload

from modules.utils import (
    get_gspread_client, 
    get_price_database, 
    get_or_create_worksheet, 
    resolve_company_name, 
    clean_number, 
    find_best_match, 
    turkish_lower,
    get_drive_service, 
    find_folder_id,    
    FILE_STOK, # Yeni Dosya Adı
    PRICE_SHEET_NAME
)

# ... (upload_to_drive, analyze_receipt_image, text_to_dataframe fonksiyonları aynı kalacak) ...
# Sadece save_receipt_dataframe içindeki dosya açma kısmı değişiyor:

def upload_to_drive(image, file_name):
    # (Kodun bu kısmı aynı, utils'den aldığımız fonksiyonları kullanıyor)
    try:
        service = get_drive_service()
        if not service: return False
        folder_id = find_folder_id(service, "IRSALIYELER")
        file_metadata = {'name': file_name}
        if folder_id: file_metadata['parents'] = [folder_id]
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        media = MediaIoBaseUpload(img_byte_arr, mimetype='image/jpeg')
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except: return False

def analyze_receipt_image(image, model_name):
    # (Bu kısım tamamen aynı)
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    Bu irsaliye/fatura görüntüsünü analiz et.
    1. Tedarikçi firma adını en üstten bul.
    2. Tarihi bul (GG.AA.YYYY formatına çevir).
    3. Tablodaki her satırı şu formatta çıkar:
    TEDARİKÇİ | TARİH | ÜRÜN ADI | MİKTAR | BİRİM (KG/ADET/LİTRE/KOLİ) | BİRİM FİYAT | TOPLAM TUTAR
    Kurallar: Fiyat/Tutar boşsa 0 yaz. Markdown yok. Başlık yok. Sadece veri.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, f"API Hatası: {response.text}"
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def text_to_dataframe(raw_text):
    # (Bu kısım tamamen aynı)
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        clean_line = line.replace("*", "").strip()
        if not clean_line or "TEDARİKÇİ" in clean_line: continue
        if "|" in clean_line:
            parts = [p.strip() for p in clean_line.split('|')]
            while len(parts) < 7: parts.append("0")
            data.append({"TEDARİKÇİ": parts[0], "TARİH": parts[1], "ÜRÜN ADI": parts[2], "MİKTAR": parts[3], "BİRİM": parts[4], "BİRİM FİYAT": parts[5], "TOPLAM TUTAR": parts[6]})
    return pd.DataFrame(data)

def save_receipt_dataframe(df, original_image):
    client = get_gspread_client()
    if not client: return False, "Google Sheets Bağlantı Hatası"
    
    price_db = get_price_database(client) # Bu artık STOK dosyasından çekiyor
    known_companies = list(price_db.keys())
    
    try:
        # --- DEĞİŞİKLİK BURADA: FILE_STOK ---
        sh = client.open(FILE_STOK) 
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        
        firm_data = {}
        kota_updates = []
        
        for index, row in df.iterrows():
            ocr_raw_name = str(row["TEDARİKÇİ"])
            final_firma = resolve_company_name(ocr_raw_name, client, known_companies)
            
            tarih, urun_adi, miktar_str = str(row["TARİH"]), str(row["ÜRÜN ADI"]), str(row["MİKTAR"])
            m_val = clean_number(miktar_str)
            birim = str(row["BİRİM"]).upper()
            fiyat_str = str(row["BİRİM FİYAT"])
            f_val = clean_number(fiyat_str)
            tutar_str = str(row["TOPLAM TUTAR"])
            
            final_urun = urun_adi
            
            if final_firma in price_db:
                prods = list(price_db[final_firma].keys())
                match_prod = find_best_match(urun_adi, prods, cutoff=0.7)
                if match_prod:
                    db_item = price_db[final_firma][match_prod]
                    final_urun = match_prod
                    if f_val == 0: f_val = db_item['fiyat']; fiyat_str = str(f_val)
                    if clean_number(tutar_str) == 0: tutar_str = f"{m_val * f_val:.2f}"
                    
                    new_kota = db_item['kota'] - m_val
                    kota_updates.append({'range': f'F{db_item["row"]}', 'values': [[new_kota]]})
            
            if final_firma not in firm_data: firm_data[final_firma] = []
            firm_data[final_firma].append([tarih, final_urun, miktar_str, birim, fiyat_str, "TL", tutar_str])
            
        msg = []
        for firma, rows in firm_data.items():
            fn = turkish_lower(firma)
            ws = existing_sheets.get(fn)
            if not ws:
                try: ws = get_or_create_worksheet(sh, firma, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "PARA BİRİMİ", "TOPLAM TUTAR"])
                except: pass
            if ws: ws.append_rows(rows); msg.append(f"{firma}: {len(rows)} kalem")
        
        if kota_updates: price_ws.batch_update(kota_updates); msg.append("(Stoktan Düşüldü)")
            
        if original_image:
            first_firma = list(firm_data.keys())[0] if firm_data else "Genel"
            first_date = str(df.iloc[0]["TARİH"]).replace(".", "-") if not df.empty else datetime.now().strftime("%Y-%m-%d")
            file_name = f"{first_firma}_{first_date}_irsaliye.jpg"
            if upload_to_drive(original_image, file_name): msg.append("✅ Drive'a Yüklendi")
    
        return True, " | ".join(msg)
    except Exception as e: return False, f"Genel Hata: {str(e)}"

# render_page fonksiyonu aynı kalabilir (utils ve analiz fonksiyonları güncel olduğu sürece)
def render_page(sel_model):
    st.header("📝 Tüketim Fişi (İrsaliye) Girişi")
    st.markdown("---")
    f = st.file_uploader("Fiş/İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])
    if f:
        img = Image.open(f)
        st.image(img, caption="Yüklenen Belge", width=300)
        if st.button("🔍 Belgeyi Analiz Et", type="primary"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_receipt_image(img, sel_model)
                if s:
                    st.session_state['irsaliye_df'] = text_to_dataframe(raw_text)
                    st.session_state['current_image'] = img
                else: st.error(f"Okuma Hatası: {raw_text}")

    if 'irsaliye_df' in st.session_state:
        edited_df = st.data_editor(st.session_state['irsaliye_df'], num_rows="dynamic", use_container_width=True)
        if st.button("💾 Kaydet ve Stoktan Düş", type="primary"):
            with st.spinner("Kaydediliyor..."):
                success, msg = save_receipt_dataframe(edited_df, st.session_state.get('current_image'))
                if success:
                    st.balloons(); st.success(msg)
                    del st.session_state['irsaliye_df']; del st.session_state['current_image']
                    st.rerun()
                else: st.error(f"Kayıt Hatası: {msg}")
