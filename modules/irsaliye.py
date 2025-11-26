import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload

# Utils'den gerekli fonksiyonları ve sabitleri çekiyoruz
from modules.utils import (
    get_gspread_client, 
    get_price_database, 
    get_or_create_worksheet, 
    resolve_company_name, 
    resolve_product_name, 
    clean_number, 
    find_best_match, 
    turkish_lower,
    get_drive_service, # Drive servisi
    find_folder_id,    # Klasör bulma
    SHEET_NAME, 
    PRICE_SHEET_NAME
)

def upload_to_drive(image, file_name):
    """Resmi Google Drive'da 'IRSALIYELER' klasörüne yükler."""
    try:
        service = get_drive_service()
        if not service: return False
        
        # 1. Ana klasörü bul veya kök dizine yükle
        # İstersen burada 'IRSALIYELER' diye bir klasör aratabiliriz
        folder_id = find_folder_id(service, "IRSALIYELER")
        
        # Eğer klasör yoksa oluşturmakla uğraşmayalım, ana dizine atsın veya manuel oluşturulsun
        # ya da basitçe None bırakırsak 'My Drive'a atar.
        
        file_metadata = {'name': file_name}
        if folder_id:
            file_metadata['parents'] = [folder_id]
            
        # Resmi byte formatına çevir
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_byte_arr.seek(0)
        
        media = MediaIoBaseUpload(img_byte_arr, mimetype='image/jpeg')
        
        service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        return True
    except Exception as e:
        st.warning(f"Drive Yükleme Hatası (Önemsiz): {e}")
        return False

def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    # Model ismindeki 'models/' öneki varsa temizle, yoksa ekle (API formatına uygunluk)
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
    
    Kurallar:
    - Fiyat veya Tutar boşsa 0 yaz.
    - Markdown tablosu yapma, sadece düz metin (pipe separated) ver.
    - Başlık satırı yazma.
    - Sadece veriyi ver, yorum yapma.
    """
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt}, 
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]
        }], 
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, f"API Hatası: {response.text}"
        
        candidates = response.json().get('candidates', [])
        if not candidates: return False, "Model yanıt döndürmedi."
        
        text = candidates[0]['content']['parts'][0]['text']
        return True, text
    except Exception as e: return False, str(e)

def text_to_dataframe(raw_text):
    """ AI çıktısını düzenlenebilir tabloya çevirir """
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        clean_line = line.replace("*", "").strip()
        if not clean_line: continue
        
        if "|" in clean_line:
            parts = [p.strip() for p in clean_line.split('|')]
            
            # Başlık satırını veya ayırıcıları atla
            if "TEDARİKÇİ" in parts[0].upper() or "---" in parts[0]: continue
            
            # Eksik sütunları tamamla (en az 7 sütun olmalı)
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

def save_receipt_dataframe(df, original_image):
    """ 
    Tabloyu Sheets'e kaydeder, Stoktan düşer ve Resmi Drive'a yükler 
    """
    client = get_gspread_client()
    if not client: return False, "Google Sheets Bağlantı Hatası"
    
    price_db = get_price_database(client)
    known_companies = list(price_db.keys())
    
    try:
        sh = client.open(SHEET_NAME)
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        
        firm_data = {}
        kota_updates = []
        
        # --- 1. VERİ İŞLEME VE STOK GÜNCELLEME ---
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
            
            # Fiyat Veritabanından Çekme ve Stok Düşme
            if final_firma in price_db:
                prods = list(price_db[final_firma].keys())
                match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                
                if match_prod:
                    db_item = price_db[final_firma][match_prod]
                    
                    # Eğer faturada fiyat yoksa DB'den al
                    if f_val == 0:
                        f_val = db_item['fiyat']
                        fiyat = str(f_val)
                    
                    # İsmi standartlaştır
                    final_urun = match_prod 
                    
                    # Tutar hesapla (Eğer eksikse)
                    if clean_number(tutar) == 0:
                        tutar = f"{m_val * f_val:.2f}"
                    
                    # --- KOTA (STOK) DÜŞME MANTIĞI ---
                    current_kota = db_item['kota']
                    new_kota = current_kota - m_val
                    row_num = db_item['row']
                    kota_updates.append({'range': f'F{row_num}', 'values': [[new_kota]]})
            
            # Firmaya göre grupla
            if final_firma not in firm_data: firm_data[final_firma] = []
            firm_data[final_firma].append([tarih, final_urun, miktar, birim, fiyat, "TL", tutar])
            
        # --- 2. SHEETS'E YAZMA ---
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
                msg.append(f"{firma}: {len(rows)} kalem")
        
        # --- 3. STOK GÜNCELLEME (BATCH) ---
        if kota_updates:
            price_ws.batch_update(kota_updates)
            msg.append(f"(Stok Güncellendi)")
            
        # --- 4. DRIVE'A RESİM YÜKLEME ---
        if original_image:
            # Dosya adı oluştur: Firma_Tarih_Rastgele.jpg
            first_firma = list(firm_data.keys())[0] if firm_data else "Genel"
            first_date = str(df.iloc[0]["TARİH"]).replace(".", "-") if not df.empty else datetime.now().strftime("%Y-%m-%d")
            file_name = f"{first_firma}_{first_date}_irsaliye.jpg"
            
            drive_success = upload_to_drive(original_image, file_name)
            if drive_success: msg.append("✅ Resim Drive'a Yüklendi")
            
        return True, " | ".join(msg)
            
    except Exception as e: return False, str(e)

def render_page(sel_model):
    st.header("📝 İrsaliye ve Fatura Girişi")
    st.markdown("---")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        f = st.file_uploader("Fatura/İrsaliye Fotoğrafı Yükle", type=['jpg', 'png', 'jpeg'])
        if f:
            img = Image.open(f)
            st.image(img, caption="Yüklenen Belge", use_container_width=True)
            
            if st.button("🔍 Belgeyi Analiz Et", type="primary"):
                with st.spinner("Yapay Zeka belgeyi okuyor..."):
                    s, raw_text = analyze_receipt_image(img, sel_model)
                    if s:
                        df = text_to_dataframe(raw_text)
                        st.session_state['irsaliye_df'] = df
                        st.session_state['current_image'] = img # Resmi kaydetmek için sakla
                    else:
                        st.error(f"Okuma Hatası: {raw_text}")

    with col2:
        if 'irsaliye_df' in st.session_state:
            st.info("👇 Tabloyu kontrol edin. Ürün isimleri ve miktarlar doğru mu?")
            
            # Data Editor
            edited_df = st.data_editor(
                st.session_state['irsaliye_df'],
                num_rows="dynamic",
                use_container_width=True,
                height=400
            )
            
            st.markdown("---")
            col_save, col_cancel = st.columns([1, 4])
            
            with col_save:
                if st.button("💾 Kaydet ve İşle", type="primary"):
                    with st.spinner("Veriler işleniyor..."):
                        img_to_save = st.session_state.get('current_image', None)
                        success, msg = save_receipt_dataframe(edited_df, img_to_save)
                        
                        if success:
                            st.balloons()
                            st.success(f"Başarılı! {msg}")
                            # Temizlik
                            if 'irsaliye_df' in st.session_state: del st.session_state['irsaliye_df']
                            if 'current_image' in st.session_state: del st.session_state['current_image']
                            st.rerun()
                        else:
                            st.error(f"Kayıt Hatası: {msg}")
