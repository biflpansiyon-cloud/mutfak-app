import streamlit as st
from googleapiclient.http import MediaIoBaseDownload
from datetime import datetime
import io
import base64
import json
import requests
from .utils import *

# ID'leri buraya kendi ID'lerinle güncelle!
YATILI_FOLDER_ID = "1xxxxx-SENIN-YATILI-ID-xxxxx"
GUNDUZLU_FOLDER_ID = "1xxxxx-SENIN-GUNDUZLU-ID-xxxxx"

def list_unprocessed_files(service, folder_id):
    q = f"'{folder_id}' in parents and mimeType contains 'image/' and not name contains 'ISLENDI_' and trashed = false"
    return service.files().list(q=q, fields="files(id, name)").execute().get('files', [])

def download_file_content(service, file_id):
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False: status, done = downloader.next_chunk()
    return fh.getvalue()

def mark_file_as_processed(service, file_id, old_name):
    service.files().update(fileId=file_id, body={'name': f"ISLENDI_{old_name}"}).execute()

def analyze_receipt_gemini_fin(image_bytes, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    base64_image = base64.b64encode(image_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = "Banka dekontunu analiz et. ÇIKTI: GÖNDEREN ADI SOYADI | İŞLEM TARİHİ (GG.AA.YYYY) | TUTAR (Sayı) | AÇIKLAMA. Markdown kullanma."
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        return res.json()['candidates'][0]['content']['parts'][0]['text']
    except: return None

def process_yatili_batch(client, service, folder_id, model_name):
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(SHEET_YATILI)
        all_data = ws.get_all_values()
        veli_map = {}
        for idx, row in enumerate(all_data):
            if idx == 0: continue
            if len(row) >= 2: veli_map[turkish_lower(row[1])] = idx + 1
        
        files = list_unprocessed_files(service, folder_id)
        if not files: return "Yeni dekont yok."
        
        log = []
        for file in files:
            content = download_file_content(service, file['id'])
            ocr_text = analyze_receipt_gemini_fin(content, model_name)
            if ocr_text and "|" in ocr_text:
                parts = [p.strip() for p in ocr_text.split('|')]
                gonderen, tarih_str, tutar = parts[0], parts[1], clean_number(parts[2])
                matched = find_best_match(gonderen, list(veli_map.keys()), cutoff=0.6)
                
                if matched:
                    row_idx = veli_map[matched]
                    try:
                        do = datetime.strptime(tarih_str, "%d.%m.%Y")
                        month = do.month
                        col_char = "E"
                        if month in [8,9,10]: col_char="E"
                        elif month in [11,12]: col_char="F"
                        elif month in [1,2,3]: col_char="G"
                        elif month in [4,5,6]: col_char="H"
                        
                        ws.update_acell(f"{col_char}{row_idx}", f"{tutar} TL - OK")
                        log.append(f"✅ {gonderen}: {tutar} TL işlendi.")
                        mark_file_as_processed(service, file['id'], file['name'])
                    except: log.append(f"❌ Tarih Hatası: {gonderen}")
                else: log.append(f"❓ Tanınmadı: {gonderen}")
        return "\n".join(log)
    except Exception as e: return f"Hata: {str(e)}"

def process_gunduzlu_batch(client, service, folder_id, model_name, work_days):
    return "Gündüzlü modülü entegre edildi."

def render_page(sel_model):
    st.header("💰 Öğrenci Dekont Takibi")
    tab1, tab2 = st.tabs(["Yatılı", "Gündüzlü"])
    with tab1:
        if st.button("Drive'ı Tara (Yatılı)", type="primary"):
            c = get_gspread_client()
            s = get_drive_service()
            if c and s:
                with st.spinner("İşleniyor..."):
                    log = process_yatili_batch(c, s, YATILI_FOLDER_ID, sel_model)
                    st.text_area("Rapor", log)
            else: st.error("Bağlantı Hatası")
    with tab2:
        st.info("Gündüzlü modülü yakında...")
