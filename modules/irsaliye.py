import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd
from datetime import datetime
import re # Eklendi (text_to_dataframe için)

from modules.utils import (
    get_gspread_client, 
    get_company_list,
    resolve_product_name,
    get_or_create_worksheet, 
    clean_number, 
    # find_best_match, # resolve_product_name içinde kullanıldığı için burada gerek yok
    FILE_STOK,
    PRICE_SHEET_NAME,
    # YENİ EKLENENLER:
    get_price_database, 
    turkish_lower,
    add_to_mapping,
    add_product_to_price_sheet,
)

def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = """
    Bu İRSALİYEYİ analiz et.
    Sadece kalemleri çıkar. Firma ismine veya tarihe bakma.
    MİKTARLARI yazarken Binlik Ayracı kullanma (1500 yaz).
    
    ÇIKTI FORMATI:
    ÜRÜN ADI | MİKTAR | BİRİM
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, f"API Hatası: {response.text}"
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def text_to_dataframe(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        clean_line = line.replace("*", "").strip()
        if not clean_line or "ÜRÜN ADI" in clean_line.upper(): continue
        if "|" in clean_line:
            parts = [p.strip() for p in re.split(r'\|', clean_line, maxsplit=2)]
            while len(parts) < 3: parts.append("")
            
            # RAW_OCR_ADI sütunu eklendi (Ham metin)
            data.append({
                "ÜRÜN ADI": parts[0], 
                "MİKTAR": parts[1], 
                "BİRİM": parts[2],
                "RAW_OCR_ADI": parts[0] # Orijinal OCR metnini tutuyoruz
            })
    return pd.DataFrame(data)

def save_receipt_dataframe(df, company, date_obj):
    client = get_gspread_client()
    # YENİ DÖNÜŞ DEĞERLERİ: success, msg, mappings, new_products
    if not client: return False, "Google Sheets Bağlantı Hatası", [], [] 
    
    date_str = date_obj.strftime("%d.%m.%Y")
    
    try:
        sh = client.open(FILE_STOK) 
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        # get_price_database'i utils'den çekiyoruz
        price_db = get_price_database(client) 
        
        ws_company = get_or_create_worksheet(sh, company, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "TUTAR", "İŞLEM TÜRÜ"])
        
        product_map = {turkish_lower(prod): details for prod, details in price_db.get(company, {}).items()}
        
        quota_updates = []
        company_log_rows = []
        msg = []
        new_mappings_to_suggest = [] # YENİ: Eşleştirme Sözlüğü önerileri
        new_products_to_suggest = [] # YENİ: Fiyat Anahtarı ürün önerileri
        
        # RAW_OCR_ADI sütunu yoksa, 'ÜRÜN ADI'nı kullan (uyumluluk için)
        df['RAW_OCR_ADI'] = df.get('RAW_OCR_ADI', df['ÜRÜN ADI']) 
        
        for index, row in df.iterrows():
            raw_prod = str(row["RAW_OCR_ADI"])  
            edited_prod = str(row["ÜRÜN ADI"]) # Kullanıcının data_editor'da düzelttiği isim
            
            final_prod = resolve_product_name(edited_prod, client, company)
            
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            
            fiyat = 0.0
            key = turkish_lower(final_prod)
            
            if key in product_map:
                # 1. VAR OLAN ÜRÜN (Kota Düşülür)
                item = product_map[key]
                fiyat = item.get('fiyat', 0.0) 
                current_quota = item.get('kota', 0.0) 
                
                new_quota = current_quota - miktar
                
                quota_updates.append({'range': f'F{item["row"]}', 'values': [[new_quota]]})
                msg.append(f"📉 DÜŞÜLDÜ: {final_prod} -> -{miktar} {birim} (Kalan Hak: {new_quota})")
                
                # --- EŞLEŞTİRME SÖZLÜĞÜ ÖNERİSİ ---
                if turkish_lower(raw_prod) != turkish_lower(final_prod):
                    new_mappings_to_suggest.append({"raw": raw_prod, "std": final_prod})
                # -----------------------------------
            else:
                # 2. YENİ ÜRÜN (Fiyat Anahtarına Ekleme Önerisi yapılır)
                new_products_to_suggest.append({
                    "product": edited_prod, 
                    "company": company,
                    "unit": birim,
                    "quota": miktar 
                })
                
                msg.append(f"⚠️ UYARI: Yeni Ürün **{edited_prod}** bulundu. Fiyat Anahtarına eklenmeli.")
            
            tutar = miktar * fiyat
            
            # Firma Log
            company_log_rows.append([
                date_str, final_prod, miktar, birim, fiyat, f"{tutar:.2f}", "Mal Kabul Edildi"
            ])
        
        if quota_updates: price_ws.batch_update(quota_updates)
        if company_log_rows: ws_company.append_rows(company_log_rows)
    
        # YENİ DÖNÜŞ DEĞERLERİ
        return True, " | ".join(msg), new_mappings_to_suggest, new_products_to_suggest 
    except Exception as e: 
        return False, f"Genel Hata: {str(e)}", [], [] 

def render_page(sel_model):
    st.header("📝 İrsaliye Girişi (Mal Kabul)")
    st.info("ℹ️ İrsaliye girdiğinde firmanın bakiyesi (stok) **AZALIR**.")
    st.markdown("---")
    
    client = get_gspread_client()
    companies = get_company_list(client) if client else []
    
    if not companies:
        st.error("⚠️ Firma listesi boş!")
        st.stop()
        
    c1, c2 = st.columns(2)
    selected_company = c1.selectbox("Firma Seç", companies)
    selected_date = c2.date_input("İrsaliye Tarihi", datetime.now())
    
    # 1. ANALİZ GİRİŞİ
    f = st.file_uploader("İrsaliye Fişi Yükle", type=['jpg', 'png', 'jpeg'])
    if f:
        img = Image.open(f)
        st.image(img, caption="Belge", width=300)
        if st.button("🔍 İrsaliyeyi Analiz Et", key="analyze_btn", type="primary"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_receipt_image(img, sel_model)
                if s:
                    st.session_state['irsaliye_df'] = text_to_dataframe(raw_text)
                    # Yeni analize başlarken eski önerileri temizle
                    if 'suggestions' in st.session_state: del st.session_state['suggestions']
                    if 'new_products' in st.session_state: del st.session_state['new_products']
                    st.rerun() 
                else: st.error(f"Okuma Hatası: {raw_text}")

    # 2. VERİ DÜZENLEME VE KAYIT
    if 'irsaliye_df' in st.session_state:
        # RAW_OCR_ADI sütununu kullanıcıdan gizle
        temp_df_for_editor = st.session_state['irsaliye_df'].drop(columns=['RAW_OCR_ADI'], errors='ignore')

        st.subheader("Okunan Ürünleri Kontrol Et ve Gerekirse Düzelt")
        edited_df = st.data_editor(temp_df_for_editor, num_rows="dynamic", use_container_width=True)
        
        if st.button("💾 Kaydet ve Stoktan Düş", key="save_btn", type="primary"):
            
            df_to_save = st.session_state['irsaliye_df'].copy()
            for col in edited_df.columns:
                 df_to_save[col] = edited_df[col] 
            
            with st.spinner("İşleniyor..."):
                success, msg, suggestions, new_products = save_receipt_dataframe(df_to_save, selected_company, selected_date)
                
                if success:
                    st.balloons(); st.success("✅ İrsaliye İşlendi!")
                    st.write(msg)
                    
                    # Önerileri session_state'e kaydet ve yeniden çalıştır
                    st.session_state['suggestions'] = suggestions
                    st.session_state['new_products'] = new_products
                    del st.session_state['irsaliye_df']
                    st.rerun() 
                else: st.error(f"Kayıt Hatası: {msg}")

    # 3. ÖNERİLERİ GÖSTER VE İŞLE (Kaydetme butonundan sonraki rerunda görünür)
    
    # EŞLEŞTİRME SÖZLÜĞÜ ÖNERİSİ
    if st.session_state.get('suggestions'):
        st.divider()
        st.subheader("💡 Otomatik Eşleştirme Önerisi (Sözlük)")
        
        suggestions = st.session_state['suggestions']
        unique_mappings = {}
        for s in suggestions:
            norm_raw = turkish_lower(s['raw'])
            if norm_raw not in unique_mappings: unique_mappings[norm_raw] = s
        
        st.info(f"Girilen **{len(unique_mappings)}** farklı OCR metni, standart ürün isimleriyle eşleşti. Bunları **Sözlüğe ekleyip** bir daha manuel işlememeyi öğrenelim mi?")
        
        for s in unique_mappings.values():
            st.markdown(f"**OCR Metni:** *{s['raw']}* $\rightarrow$ **Standart İsim:** **{s['std']}**")
            
        if st.button("Sözlüğe Ekle ve Öğren", key="add_mapping_btn", type="secondary"):
            with st.spinner("Eşleştirmeler Sözlüğe Ekleniyor..."):
                for s in unique_mappings.values():
                    add_to_mapping(client, s['raw'], s['std'])
                
                st.success("✅ Tüm eşleştirmeler sözlüğe kaydedildi.")
                del st.session_state['suggestions']
                st.rerun() 
    
    # YENİ FİYAT ANAHTARI ÜRÜNÜ ÖNERİSİ
    if st.session_state.get('new_products'):
        st.divider()
        st.subheader("🆕 Fiyat Anahtarı (Stok) Ekleme Önerisi")
        
        new_products = st.session_state['new_products']
        product_summary = {}
        for p in new_products:
            key = turkish_lower(p['product'])
            if key not in product_summary:
                product_summary[key] = p.copy()
            else:
                product_summary[key]['quota'] += p['quota']
        
        unique_new_products = product_summary.values()
        
        st.warning(f"Aşağıdaki **{len(unique_new_products)}** ürün Fiyat Anahtarınızda **bulunamadı**. Faturası gelmemiş bu ürünleri borçlanma hakkını kullanmak için eklemek ister misiniz?")
        
        for p in unique_new_products:
            st.markdown(f"**Ürün:** *{p['product']}* | **Toplam Miktar:** {p['quota']} {p['unit']}")
            
        if st.button("Fiyat Anahtarına Ekle ve Kota Yükle", key="add_new_price_prod_btn", type="danger"):
            with st.spinner("Yeni Ürünler Fiyat Anahtarına Ekleniyor..."):
                for p in unique_new_products:
                    add_product_to_price_sheet(client, p['product'], selected_company, p['unit'], p['quota'])
                
                st.success("✅ Tüm yeni ürünler Fiyat Anahtarına eklendi (Fiyat=0.0₺).")
                del st.session_state['new_products']
                st.rerun()
