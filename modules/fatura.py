import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime
from .utils import *

# --- BUG BUSTER FOR SHEET NAMES --- #
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"

def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    
    # Dosya türünü belirle (PDF mi Resim mi?)
    mime_type = uploaded_file.type
    if not mime_type: 
        mime_type = "application/pdf" # Varsayılan
    
    file_bytes = uploaded_file.getvalue()
    base64_data = base64.b64encode(file_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    FATURAYI analiz et.
    1. Tedarikçi Firmayı Bul.
    2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar.
    3. HESAPLAMA: Eğer satırda "5KG", "Teneke (18L)" gibi paket bilgisi varsa, Toplam Fiyatı miktara bölerek gerçek BİRİM FİYATI (KG/Litre başı) hesapla.
    
    ÇIKTI FORMATI (Her satır için):
    TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT | MİKTAR (Sayı) | BİRİM (KG/LT/Adet)
    
    Örnek: Alp Et | Kıyma | 450.00 | 50 | KG
    Markdown kullanma. Sadece veriyi ver.
    """
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64_data}}
            ]
        }],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, f"API Hatası: {response.text}"
        
        result = response.json()
        if 'candidates' in result and result['candidates']:
            return True, result['candidates'][0]['content']['parts'][0]['text']
        return False, "Yapay zeka boş cevap döndü."
        
    except Exception as e: return False, str(e)

def text_to_dataframe_fatura(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        # Markdown tablolarındaki gereksiz satırları atla
        if "---" in line or line.strip() == "": continue
        
        line = line.replace("*", "").strip()
        if "|" in line:
            # Boşlukları temizle ve sadece dolu olan hücreleri al
            parts = [p.strip() for p in line.split('|') if p.strip() != ""]
            
            # Başlık satırını atla (Büyük/küçük harf duyarlılığını kaldırarak kontrol et)
            if len(parts) > 0 and "TEDARİKÇİ" in parts[0].upper(): continue
            
            # Eğer satırda yeterli veri yoksa atla veya '0' ile doldur
            if len(parts) < 2: continue # Çok kısa satırsa hatalıdır
            
            # Eksik sütun tamamlama (En az 5 sütun olmalı)
            while len(parts) < 5: parts.append("0")
            
            data.append({
                "TEDARİKÇİ": parts[0],
                "ÜRÜN ADI": parts[1],
                "BİRİM FİYAT": parts[2],
                "MİKTAR": parts[3],
                "BİRİM": parts[4]
            })
    return pd.DataFrame(data)

def update_price_list_dataframe(df):
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
        
        # DataFrame üzerinden dön
        for index, row in df.iterrows():
            raw_supplier = str(row["TEDARİKÇİ"])
            target_supplier = resolve_company_name(raw_supplier, client, existing_companies_list)
            
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
        return True, f"✅ {cnt_upd} fiyat güncellendi, {cnt_new} yeni ürün stoka eklendi."
    except Exception as e: return False, str(e)

def render_page(sel_model):
    st.header("🧾 Fiyat & Stok Güncelleme")
    st.info("PDF veya Resim formatındaki faturaları yükleyebilirsiniz.")
    
    # BURASI GÜNCELLENDİ: type=['pdf', 'jpg', 'png', 'jpeg']
    uploaded_file = st.file_uploader("Fatura Yükle", type=['pdf', 'jpg', 'png', 'jpeg'])
    
    if uploaded_file:
        if st.button("Analiz Et"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_invoice_pdf(uploaded_file, sel_model)
                if s:
                    df = text_to_dataframe_fatura(raw_text)
                    st.session_state['fatura_df'] = df
                else:
                    st.error(f"Hata: {raw_text}")
                    
    if 'fatura_df' in st.session_state:
        st.info("👇 Fatura detaylarını kontrol et, gerekirse düzelt.")
        edited_df = st.data_editor(
            st.session_state['fatura_df'],
            num_rows="dynamic",
            use_container_width=True
        )
        
        if st.button("💾 İşle (Fiyatları Güncelle & Stoka Ekle)"):
            with st.spinner("Veritabanına yazılıyor..."):
                s, m = update_price_list_dataframe(edited_df)
                if s:
                    st.balloons()
                    st.success(m)
                    del st.session_state['fatura_df']
                else:
                    st.error(m)
