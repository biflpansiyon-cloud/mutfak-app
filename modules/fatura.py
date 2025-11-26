import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload
import io

# Utils'den güvenli importlar
from modules.utils import (
    get_gspread_client, 
    get_or_create_worksheet,
    resolve_company_name, 
    resolve_product_name, 
    clean_number, 
    turkish_lower,
    get_drive_service,
    find_folder_id,
    SHEET_NAME,
    PRICE_SHEET_NAME
)

def upload_to_drive(file_obj, file_name, mime_type):
    """
    Dosyayı (PDF veya Resim) Google Drive'da 'FATURALAR' klasörüne yükler.
    """
    try:
        service = get_drive_service()
        if not service: return False
        
        # 1. Klasör Bul
        folder_id = find_folder_id(service, "FATURALAR")
        
        file_metadata = {'name': file_name}
        if folder_id:
            file_metadata['parents'] = [folder_id]
            
        # Dosyayı baştan oku
        file_obj.seek(0)
        
        media = MediaIoBaseUpload(file_obj, mimetype=mime_type, resumable=True)
        
        service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        return True
    except Exception as e:
        st.warning(f"Drive Yedekleme Uyarısı: {e}")
        return False

def analyze_invoice_file(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    
    # Dosya türünü belirle
    mime_type = uploaded_file.type
    
    # Dosyayı oku ve base64 yap
    uploaded_file.seek(0)
    file_bytes = uploaded_file.getvalue()
    base64_data = base64.b64encode(file_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Bu FATURAYI analiz et.
    1. Tedarikçi Firmayı en üstten bul.
    2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar.
    3. HESAPLAMA: Eğer satırda "5KG", "18L Teneke", "Koli (30lu)" gibi paket bilgisi varsa, 
       Toplam Fiyatı toplama değil, paketin içindeki miktara bölerek GERÇEK BİRİM FİYATI (KG/Litre/Adet başı) hesapla.
       Örnek: "18L Ayçiçek Yağı" fiyatı 900 TL ise, Birim Fiyat (900/18) = 50 TL olmalı.

    ÇIKTI FORMATI (Her satır için):
    TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT (Sayı) | MİKTAR (Stoka Girecek) | BİRİM (KG/LT/Adet)
    
    Markdown kullanma. Başlık satırı yazma. Sadece veriyi ver.
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
        if "---" in line or line.strip() == "": continue
        
        line = line.replace("*", "").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split('|') if p.strip() != ""]
            
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

def update_price_list_dataframe(df, file_obj=None, mime_type=None):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    
    try:
        sh = client.open(SHEET_NAME)
        # Sütun sırası önemli: Tedarikçi, Ürün, Fiyat, Para, Tarih, Kota, Kota Birim
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        
        existing_data = ws.get_all_values()
        product_map = {}
        existing_companies = set()
        
        # Mevcut veritabanını hafızaya al
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                kota = clean_number(row[5]) if len(row) >= 6 else 0.0
                product_map[f"{k_firma}|{k_urun}"] = {"row": idx + 1, "quota": kota}
                existing_companies.add(row[0])
        
        existing_companies_list = list(existing_companies)
        updates_batch = []
        new_rows_batch = []
        cnt_upd, cnt_new = 0, 0
        first_supplier_name = "Genel"
        
        # DataFrame satırlarını işle
        for index, row in df.iterrows():
            raw_supplier = str(row["TEDARİKÇİ"])
            target_supplier = resolve_company_name(raw_supplier, client, existing_companies_list)
            
            if index == 0: first_supplier_name = target_supplier
            
            raw_prod = str(row["ÜRÜN ADI"])
            final_prod = resolve_product_name(raw_prod, client)
            
            fiyat = clean_number(row["BİRİM FİYAT"])
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            bugun = datetime.now().strftime("%d.%m.%Y")
            
            if fiyat == 0: continue
            
            key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
            
            # Güncelleme mi, Yeni Ürün mü?
            if key in product_map:
                item = product_map[key]
                row_idx = item['row']
                # Fatura olduğu için STOK ARTTIR (Mevcut + Yeni Miktar)
                new_quota = item['quota'] + miktar
                
                updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})     # Fiyat
                updates_batch.append({'range': f'E{row_idx}', 'values': [[bugun]]})     # Tarih
                updates_batch.append({'range': f'F{row_idx}', 'values': [[new_quota]]}) # Kota
                updates_batch.append({'range': f'G{row_idx}', 'values': [[birim]]})     # Birim
                cnt_upd += 1
            else:
                # Yeni ürün satırı
                new_rows_batch.append([target_supplier, final_prod, fiyat, "TL", bugun, miktar, birim])
                cnt_new += 1
                
        # Toplu işlemleri yap
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        
        msg = f"✅ {cnt_upd} ürün fiyatı/stoku güncellendi, {cnt_new} yeni ürün eklendi."
        
        # DRIVE YEDEKLEME
        if file_obj and mime_type:
            ext = "pdf" if "pdf" in mime_type else "jpg"
            date_str = datetime.now().strftime("%Y-%m-%d")
            fname = f"{first_supplier_name}_{date_str}_fatura.{ext}"
            if upload_to_drive(file_obj, fname, mime_type):
                msg += " | 📂 Dosya Drive'a yedeklendi."
                
        return True, msg
        
    except Exception as e: return False, str(e)

def render_page(sel_model):
    st.header("🧾 Fatura İşleme (Fiyat & Stok)")
    st.markdown("Bu modül ürünlerin **Birim Fiyatlarını günceller** ve gelen miktarı **Stok Kotasına ekler**.")
    st.markdown("---")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        uploaded_file = st.file_uploader("Fatura Yükle (PDF/Resim)", type=['pdf', 'jpg', 'png', 'jpeg'])
        
        if uploaded_file:
            # Önizleme (Sadece resimler için çalışır, PDF için ikon gösteririz)
            if "image" in uploaded_file.type:
                st.image(uploaded_file, caption="Fatura Önizleme", use_container_width=True)
            else:
                st.info(f"📄 PDF Dosyası Yüklendi: {uploaded_file.name}")
            
            if st.button("🔍 Faturayı Analiz Et", type="primary"):
                with st.spinner("AI faturayı okuyor..."):
                    s, raw_text = analyze_invoice_file(uploaded_file, sel_model)
                    if s:
                        df = text_to_dataframe_fatura(raw_text)
                        st.session_state['fatura_df'] = df
                        st.session_state['fatura_file'] = uploaded_file # Dosyayı sakla
                    else:
                        st.error(f"Hata: {raw_text}")
    
    with col2:     
        if 'fatura_df' in st.session_state:
            st.info("👇 Tabloyu kontrol et. 'BİRİM FİYAT' sütunu paket fiyatı değil, KG/LT başı fiyat olmalıdır.")
            edited_df = st.data_editor(
                st.session_state['fatura_df'],
                num_rows="dynamic",
                use_container_width=True,
                height=450
            )
            
            st.markdown("---")
            col_save, col_cancel = st.columns([1, 4])
            
            with col_save:
                if st.button("💾 Kaydet", type="primary"):
                    with st.spinner("Veritabanı güncelleniyor..."):
                        f_obj = st.session_state.get('fatura_file', None)
                        mime = f_obj.type if f_obj else None
                        
                        s, m = update_price_list_dataframe(edited_df, f_obj, mime)
                        if s:
                            st.balloons()
                            st.success(m)
                            # Temizlik
                            if 'fatura_df' in st.session_state: del st.session_state['fatura_df']
                            if 'fatura_file' in st.session_state: del st.session_state['fatura_file']
                            st.rerun()
                        else:
                            st.error(m)
