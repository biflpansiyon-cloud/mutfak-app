import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload
import io

from modules.utils import (
    get_gspread_client, 
    get_or_create_worksheet,
    resolve_company_name, 
    resolve_product_name, 
    clean_number, 
    turkish_lower,
    get_drive_service,
    find_folder_id,
    FILE_STOK, # Yeni Dosya
    PRICE_SHEET_NAME
)

# ... (upload_to_drive, analyze_invoice_file, text_to_dataframe_fatura aynen kalıyor) ...

# Sadece update_price_list_dataframe dosya açma kısmı değişiyor:
def upload_to_drive(file_obj, file_name, mime_type):
    # (Aynı kod)
    try:
        service = get_drive_service()
        if not service: return False
        folder_id = find_folder_id(service, "FATURALAR")
        file_metadata = {'name': file_name}
        if folder_id: file_metadata['parents'] = [folder_id]
        file_obj.seek(0)
        media = MediaIoBaseUpload(file_obj, mimetype=mime_type, resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except: return False

def analyze_invoice_file(uploaded_file, model_name):
    # (Aynı kod)
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    uploaded_file.seek(0)
    base64_data = base64.b64encode(uploaded_file.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """Bu FATURAYI analiz et. 1. Tedarikçi Firmayı bul. 2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar. 3. ÖNEMLİ: Paket (Koli/Teneke) fiyatını paketin içindeki miktara bölerek GERÇEK BİRİM FİYATI bul. ÇIKTI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT | MİKTAR | BİRİM"""
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": uploaded_file.type, "data": base64_data}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code == 200: return True, res.json()['candidates'][0]['content']['parts'][0]['text']
        return False, "API Hatası"
    except Exception as e: return False, str(e)

def text_to_dataframe_fatura(raw_text):
    # (Aynı kod)
    data = []
    for line in raw_text.split('\n'):
        if "---" in line or not line.strip(): continue
        line = line.replace("*", "").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) > 0 and "TEDARİKÇİ" in parts[0].upper(): continue
            if len(parts) < 2: continue
            while len(parts) < 5: parts.append("0")
            data.append({"TEDARİKÇİ": parts[0], "ÜRÜN ADI": parts[1], "BİRİM FİYAT": parts[2], "MİKTAR": parts[3], "BİRİM": parts[4]})
    return pd.DataFrame(data)

def update_price_list_dataframe(df, file_obj=None, mime_type=None):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    
    try:
        # --- DEĞİŞİKLİK BURADA ---
        sh = client.open(FILE_STOK)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        
        existing_data = ws.get_all_values()
        product_map = {}
        existing_companies = set()
        
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                product_map[f"{turkish_lower(row[0])}|{turkish_lower(row[1])}"] = {"row": idx + 1, "quota": clean_number(row[5]) if len(row) >= 6 else 0.0}
                existing_companies.add(row[0])
        
        updates_batch = []
        new_rows_batch = []
        cnt_upd, cnt_new = 0, 0
        first_supplier_name = "Genel"
        
        for index, row in df.iterrows():
            raw_supplier = str(row["TEDARİKÇİ"])
            target_supplier = resolve_company_name(raw_supplier, client, list(existing_companies))
            if index == 0: first_supplier_name = target_supplier
            
            raw_prod = str(row["ÜRÜN ADI"])
            final_prod = resolve_product_name(raw_prod, client)
            
            fiyat = clean_number(row["BİRİM FİYAT"])
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            bugun = datetime.now().strftime("%d.%m.%Y")
            
            if fiyat == 0: continue
            
            key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
            
            if key in product_map:
                item = product_map[key]
                new_quota = item['quota'] + miktar
                updates_batch.append({'range': f'C{item["row"]}', 'values': [[fiyat]]})
                updates_batch.append({'range': f'E{item["row"]}', 'values': [[bugun]]})
                updates_batch.append({'range': f'F{item["row"]}', 'values': [[new_quota]]})
                updates_batch.append({'range': f'G{item["row"]}', 'values': [[birim]]})
                cnt_upd += 1
            else:
                new_rows_batch.append([target_supplier, final_prod, fiyat, "TL", bugun, miktar, birim])
                cnt_new += 1
                
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        
        msg = f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi."
        if file_obj and mime_type:
             date_str = datetime.now().strftime("%Y-%m-%d")
             fname = f"{first_supplier_name}_{date_str}_fatura.{'pdf' if 'pdf' in mime_type else 'jpg'}"
             if upload_to_drive(file_obj, fname, mime_type): msg += " | 📂 Yedeklendi."
        return True, msg
        
    except Exception as e: return False, str(e)

# Render page aynen kalıyor
def render_page(sel_model):
    st.header("🧾 Fatura İşleme (Fiyat & Stok)")
    col1, col2 = st.columns([1, 2])
    with col1:
        uploaded_file = st.file_uploader("Fatura Yükle", type=['pdf', 'jpg', 'png', 'jpeg'])
        if uploaded_file and st.button("🔍 Analiz Et", type="primary"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_invoice_file(uploaded_file, sel_model)
                if s:
                    st.session_state['fatura_df'] = text_to_dataframe_fatura(raw_text)
                    st.session_state['fatura_file'] = uploaded_file
                else: st.error(raw_text)
    with col2:     
        if 'fatura_df' in st.session_state:
            edited_df = st.data_editor(st.session_state['fatura_df'], num_rows="dynamic", use_container_width=True, height=450)
            if st.button("💾 Kaydet", type="primary"):
                with st.spinner("İşleniyor..."):
                    f_obj = st.session_state.get('fatura_file', None)
                    s, m = update_price_list_dataframe(edited_df, f_obj, f_obj.type if f_obj else None)
                    if s:
                        st.balloons(); st.success(m)
                        del st.session_state['fatura_df']; del st.session_state['fatura_file']
                        st.rerun()
                    else: st.error(m)
