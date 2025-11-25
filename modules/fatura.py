import streamlit as st
import requests
import json
import base64
from .utils import *

def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    pdf_bytes = uploaded_file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    FATURAYI analiz et.
    1. Tedarikçi Firmayı Bul.
    2. Kalemlerin KDV HARİÇ BİRİM FİYATINI bul.
    3. HESAPLAMA: "5KG", "Teneke" gibi paketse, BİRİM FİYATI (KG/LT) hesapla.
    ÇIKTI FORMATI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT | MİKTAR (Sayı) | BİRİM (KG/LT)
    Örnek: Alp Et | Kıyma | 450.00 | 50 | KG
    Sadece veriyi ver. Markdown kullanma.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "application/pdf", "data": base64_pdf}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def update_price_list(raw_text):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    try:
        sh = client.open(SHEET_NAME)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        
        existing_data = ws.get_all_values()
        product_map = {}
        existing_companies = set()
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                kota = clean_number(row[5]) if len(row) >= 6 else 0.0
                product_map[f"{k_firma}|{k_urun}"] = {"row": idx + 1, "quota": kota}
                existing_companies.add(row[0])
        
        existing_companies_list = list(existing_companies)
        updates_batch, new_rows_batch = [], []
        cnt_upd, cnt_new = 0, 0
        
        lines = raw_text.split('\n')
        for line in lines:
            line = line.replace("*", "").replace("- ", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 5: parts.append("0")
                if clean_number(parts[2]) == 0: continue
                
                raw_supplier = parts[0]
                target_supplier = resolve_company_name(raw_supplier, client, existing_companies_list)
                raw_prod = parts[1].strip()
                final_prod = resolve_product_name(raw_prod, client)
                fiyat = clean_number(parts[2])
                miktar = clean_number(parts[3])
                birim = parts[4].strip().upper()
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
                
                if key in product_map:
                    item = product_map[key]
                    row_idx = item['row']
                    new_quota = item['quota'] + miktar
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})
                    updates_batch.append({'range': f'E{row_idx}', 'values': [[bugun]]})
                    updates_batch.append({'range': f'F{row_idx}', 'values': [[new_quota]]})
                    updates_batch.append({'range': f'G{row_idx}', 'values': [[birim]]})
                    cnt_upd += 1
                else:
                    new_rows_batch.append([target_supplier, final_prod, fiyat, "TL", bugun, miktar, birim])
                    cnt_new += 1
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        return True, f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi."
    except Exception as e: return False, str(e)

def render_page(sel_model):
    st.header("🧾 Fiyat & Stok Güncelleme")
    st.info("PDF Fatura yükle. Fiyatlar güncellenir, Miktarlar stoka eklenir.")
    pdf = st.file_uploader("PDF Fatura", type=['pdf'])
    if pdf:
        if st.button("Analiz Et"):
            with st.spinner("Okunuyor..."):
                s, r = analyze_invoice_pdf(pdf, sel_model)
                st.session_state['inv'] = r
        if 'inv' in st.session_state:
            with st.form("upd"):
                ed = st.text_area("Algılanan", st.session_state['inv'], height=200)
                if st.form_submit_button("İşle (Stoka Ekle)"):
                    s, m = update_price_list(ed)
                    if s: st.success(m); del st.session_state['inv']
                    else: st.error(m)
