import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd
from datetime import datetime

from modules.utils import (
    get_gspread_client, 
    get_company_list,
    resolve_product_name,
    get_or_create_worksheet, 
    clean_number, 
    find_best_match,
    turkish_lower,     # <--- YENİ EKLENDİ
    add_to_mapping,    # <--- YENİ EKLENDİ
    FILE_STOK,
    PRICE_SHEET_NAME
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
            parts = [p.strip() for p in clean_line.split('|')]
            while len(parts) < 3: parts.append("0")
            # İrsaliyede fiyat olmaz genelde, 0 kabul edeceğiz, veritabanından çekeceğiz
            data.append({
                "ÜRÜN ADI": parts[0], 
                "MİKTAR": parts[1], 
                "BİRİM": parts[2],
                "RAW_OCR_ADI": parts[0] # <--- YENİ EKLENDİ (Gizli Sütun)
            })
    return pd.DataFrame(data)

def save_receipt_dataframe(df, company, date_obj):
    client = get_gspread_client()
    if not client: return False, "Google Sheets Bağlantı Hatası", [] # <-- Dönüş değeri güncellendi
    
    date_str = date_obj.strftime("%d.%m.%Y")
    
    try:
        sh = client.open(FILE_STOK) 
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        price_data = price_ws.get_all_values()
        
        # Firma Sayfası
        ws_company = get_or_create_worksheet(sh, company, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "TUTAR", "İŞLEM TÜRÜ"])
        
        # Stok Haritası
        product_map = {}
        for idx, row in enumerate(price_data):
            if idx == 0: continue
            if len(row) >= 2:
                db_comp = row[0].strip()
                db_prod = row[1].strip()
                if db_comp == company:
                    product_map[db_prod.lower()] = {
                        "row": idx + 1, 
                        "quota": clean_number(row[5]) if len(row) >= 6 else 0.0,
                        "price": clean_number(row[2]) 
                    }
        
        quota_updates = []
        company_log_rows = []
        msg = []
        new_mappings_to_suggest = [] # <--- YENİ EKLENDİ
        
        for index, row in df.iterrows():
            raw_prod = str(row["RAW_OCR_ADI"])  # Orijinal OCR Metni
            edited_prod = str(row["ÜRÜN ADI"]) # Kullanıcının düzelttiği veya OCR'den gelen
            
            # resolve_product_name artık mapping sözlüğünü kullanıyor
            final_prod = resolve_product_name(edited_prod, client, company)
            
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            
            # Fiyat bul (DB'den)
            fiyat = 0.0
            key = final_prod.lower()
            
            if key in product_map:
                item = product_map[key]
                fiyat = item['price']
                
                # ... (mevcut kota düşürme mantığı)
                new_quota = item['quota'] - miktar
                
                quota_updates.append({'range': f'F{item["row"]}', 'values': [[new_quota]]})
                msg.append(f"📉 DÜŞÜLDÜ: {final_prod} -> -{miktar} {birim} (Kalan Hak: {new_quota})")
                
                # --- EŞLEŞTİRME ÖNERİSİ KONTROLÜ ---
                # Ham OCR metni ile son çözülen isim farklıysa, bu bir öğrenme fırsatıdır.
                if turkish_lower(raw_prod) != turkish_lower(final_prod):
                    # Sözlüğe eklenmesi için öneri olarak kaydet
                    new_mappings_to_suggest.append({"raw": raw_prod, "std": final_prod})
                # -----------------------------------

            else:
                msg.append(f"⚠️ UYARI: {final_prod} faturası bulunamadı, stoktan düşülemedi.")
            
            tutar = miktar * fiyat
            
            # Firma Log
            company_log_rows.append([
                date_str, 
                final_prod, 
                miktar, 
                birim, 
                fiyat, 
                f"{tutar:.2f}", 
                "Mal Kabul Edildi" 
            ])
        
        if quota_updates: price_ws.batch_update(quota_updates)
        if company_log_rows: ws_company.append_rows(company_log_rows)
    
        return True, " | ".join(msg), new_mappings_to_suggest # <--- Dönüş değeri güncellendi
    except Exception as e: return False, f"Genel Hata: {str(e)}", [] # <-- Hata durumunda da boş liste döndür
        
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
    
    f = st.file_uploader("İrsaliye Fişi Yükle", type=['jpg', 'png', 'jpeg'])
    
    if f:
        img = Image.open(f)
        st.image(img, caption="Belge", width=300)
        if st.button("🔍 İrsaliyeyi Analiz Et", type="primary"):
            with st.spinner("Okunuyor..."):
                s, raw_text = analyze_receipt_image(img, sel_model)
                if s:
                    st.session_state['irsaliye_df'] = text_to_dataframe(raw_text)
                else: st.error(f"Okuma Hatası: {raw_text}")

    if 'irsaliye_df' in st.session_state:
        # RAW_OCR_ADI sütununu kullanıcıdan gizle (kullanıcının düzenleyeceği df)
        temp_df_for_editor = st.session_state['irsaliye_df'].drop(columns=['RAW_OCR_ADI'], errors='ignore')

        edited_df = st.data_editor(temp_df_for_editor, num_rows="dynamic", use_container_width=True)
        
        if st.button("💾 Kaydet ve Stoktan Düş", type="primary"):
            # Orijinal df'i (RAW_OCR_ADI sütunu ile) kopyala
            df_to_save = st.session_state['irsaliye_df'].copy()
            # Kullanıcının yaptığı düzenlemeleri (RAW_OCR_ADI hariç) geri aktar
            for col in edited_df.columns:
                 df_to_save[col] = edited_df[col] 

            with st.spinner("İşleniyor..."):
                # Yeni dönüş değerini yakala: success, msg, suggestions
                success, msg, suggestions = save_receipt_dataframe(df_to_save, selected_company, selected_date)
                
                if success:
                    st.balloons(); st.success("✅ İrsaliye İşlendi!")
                    st.write(msg)
                    # Otomatik eşleştirme önerisi göster
                    if suggestions:
                        st.divider()
                        st.subheader("💡 Otomatik Eşleştirme Önerisi")
                        
                        # Tekil önerileri al (aynı ham metni birden fazla kaydetmemek için)
                        unique_suggestions = {(s['raw'], s['std']) for s in suggestions}
                        
                        st.info(f"Girilen **{len(unique_suggestions)}** farklı OCR metni, standart ürün isimleriyle eşleştirildi (manuel veya bulanık eşleştirme). Bunları bir daha manuel işlememek için **Sözlüğe ekleyelim mi?**")
                        
                        # Önerileri liste olarak göster
                        for raw_name, std_name in unique_suggestions:
                            st.markdown(f"**OCR Metni:** *{raw_name}* $\rightarrow$ **Standart İsim:** **{std_name}**")
                            
                        if st.button("Sözlüğe Ekle ve Öğren (Önerilen)", type="secondary"):
                            mapping_results = []
                            for raw_name, std_name in unique_suggestions:
                                # Sözlüğe ekleme fonksiyonunu çağır
                                if add_to_mapping(client, raw_name, std_name):
                                    mapping_results.append(f"'{raw_name}' -> '{std_name}' başarıyla eklendi.")
                                else:
                                    mapping_results.append(f"'{raw_name}' eklenemedi.")
                            
                            st.success("✅ Tüm eşleştirmeler sözlüğe kaydedildi. Bir dahaki sefere otomatik tanınacaklar.")
                            st.info("Kayıt logları:")
                            st.text("\n".join(mapping_results))

                    # İşlem bitti, session state'i temizle
                    del st.session_state['irsaliye_df']
                    
                else: st.error(f"Kayıt Hatası: {msg}")
