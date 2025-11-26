import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime
import io

from modules.utils import (
    get_gspread_client, 
    get_or_create_worksheet,
    resolve_company_name, 
    resolve_product_name, 
    clean_number, 
    turkish_lower,
    FILE_STOK, 
    PRICE_SHEET_NAME
)

# --- AI ANALİZ ---
def analyze_invoice_file(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    
    uploaded_file.seek(0)
    file_bytes = uploaded_file.getvalue()
    base64_data = base64.b64encode(file_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Bu FATURAYI analiz et.
    1. Tedarikçi Firmayı bul.
    2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar.
    3. MİKTAR ve FİYATLARI yazarken "Binlik Ayracı" KULLANMA. (Örnek: 1.500 yazma, 1500 yaz). Ondalık için nokta kullan.
    4. Paket (Koli/Teneke) fiyatını paketin içindeki miktara bölerek KG/LT başı BİRİM FİYATI bul.
    
    ÇIKTI FORMATI:
    TEDARİKÇİ | ÜRÜN ADI | BİRİM FİYAT (Sadece Sayı) | MİKTAR (Sadece Sayı) | BİRİM
    """
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": uploaded_file.type, "data": base64_data}}
            ]
        }],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code == 200:
            return True, res.json()['candidates'][0]['content']['parts'][0]['text']
        return False, "API Cevap Vermedi"
    except Exception as e: return False, str(e)

def text_to_dataframe_fatura(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        if "---" in line or not line.strip(): continue
        line = line.replace("*", "").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) > 0 and "TEDARİKÇİ" in parts[0].upper(): continue
            if len(parts) < 2: continue
            while len(parts) < 5: parts.append("0")
            
            data.append({
                "TEDARİKÇİ": parts[0],
                "ÜRÜN ADI": parts[1],
                "BİRİM FİYAT": parts[2],
                "MİKTAR": parts[3],
                "BİRİM": parts[4]
            })
    return pd.DataFrame(data)

# --- VERİTABANI GÜNCELLEME ---
def update_price_list_dataframe(df):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    
    log_messages = []
    try:
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
        
        for index, row in df.iterrows():
            raw_supplier = str(row["TEDARİKÇİ"])
            target_supplier = resolve_company_name(raw_supplier, client, list(existing_companies))
            
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
                log_messages.append(f"🔄 GÜNCELLENDİ: {final_prod} -> +{miktar} {birim} (Stok: {new_quota})")
            else:
                new_rows_batch.append([target_supplier, final_prod, fiyat, "TL", bugun, miktar, birim])
                log_messages.append(f"✨ YENİ ÜRÜN: {final_prod} ({miktar} {birim})")
                
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        
        return True, log_messages
        
    except Exception as e: return False, [str(e)]

def render_page(sel_model):
    st.header("🧾 Fatura İşleme (Fiyat & Stok)")
    st.markdown("---")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        uploaded_file = st.file_uploader("Fatura Yükle (PDF/Resim)", type=['pdf', 'jpg', 'png', 'jpeg'])
        if uploaded_file and st.button("🔍 Faturayı Analiz Et", type="primary"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_invoice_file(uploaded_file, sel_model)
                if s:
                    st.session_state['fatura_df'] = text_to_dataframe_fatura(raw_text)
                else: st.error(f"Hata: {raw_text}")
    
    with col2:     
        if 'fatura_df' in st.session_state:
            st.subheader("Kontrol Tablosu")
            st.warning("⚠️ Miktarları kontrol et (1500 yerine 1.5 görünmemeli).")
            edited_df = st.data_editor(st.session_state['fatura_df'], num_rows="dynamic", use_container_width=True, height=400)
            
            if st.button("💾 Kaydet ve İşle", type="primary"):
                with st.spinner("Veritabanı güncelleniyor..."):
                    success, logs = update_price_list_dataframe(edited_df)
                    if success:
                        st.balloons()
                        st.success("✅ İşlem Başarılı! (Dosya Drive'a Yüklenmedi)")
                        with st.expander("📋 İşlem Raporu", expanded=True):
                            for log in logs: st.text(log)
                        del st.session_state['fatura_df']
                    else: st.error(f"Kayıt Hatası: {logs[0]}")
