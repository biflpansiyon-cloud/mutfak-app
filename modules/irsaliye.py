import streamlit as st
from PIL import Image
import requests
import json
import io
import base64
import pandas as pd
from datetime import datetime
import re

from modules.utils import (
    get_gspread_client, 
    get_company_list,
    get_price_database, # Kota düşümü için gerekli (mevcuttu)
    resolve_product_name,
    get_or_create_worksheet, 
    clean_number, 
    turkish_lower,     # YENİ
    add_to_mapping,    # YENİ
    add_product_to_price_sheet, # YENİ
    FILE_STOK,
    PRICE_SHEET_NAME
)

# --- AI ANALİZ (Mevcut Fonksiyonunuz) ---
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
    
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inlineData": {"mimeType": "image/jpeg", "data": base64_image}}
    ]}]}
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        raw_text = result['candidates'][0]['content']['parts'][0]['text']
        return True, raw_text
    except Exception as e:
        return False, f"AI Analiz Hatası: {e}"

# --- VERİ İŞLEME VE KAYIT ---

def text_to_dataframe(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        clean_line = line.replace("*", "").strip()
        if not clean_line or "ÜRÜN ADI" in clean_line.upper(): continue
        
        # Ayracın sadece '|' değil, olası diğer ayraçları da düşünerek esnek parse
        parts = [p.strip() for p in re.split(r'\|| - ', clean_line, maxsplit=2)]
        
        if len(parts) >= 3:
            data.append({
                "ÜRÜN ADI": parts[0], 
                "MİKTAR": parts[1], 
                "BİRİM": parts[2],
                "RAW_OCR_ADI": parts[0] # Orijinal OCR metni (gizli)
            })
    return pd.DataFrame(data)

def save_receipt_dataframe(df, company, date_obj):
    client = get_gspread_client()
    # Dönüş değerine suggestions (eşleşme önerileri) ve new_products (yeni ürün önerileri) eklendi
    if not client: return False, "Google Sheets Bağlantı Hatası", [], [] 
    
    date_str = date_obj.strftime("%d.%m.%Y")
    
    try:
        sh = client.open(FILE_STOK) 
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        price_db = get_price_database(client) # Güncel fiyat veritabanını çek
        
        # Firma Sayfası (Cari Ekstresi)
        ws_company = get_or_create_worksheet(sh, company, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "TUTAR", "İŞLEM TÜRÜ"])
        
        # Sadece ilgili firmanın ürünlerini al, anahtarları normalleştirilmiş olsun
        product_map = {turkish_lower(prod): details for prod, details in price_db.get(company, {}).items()}
        
        quota_updates = []
        company_log_rows = []
        msg = []
        new_mappings_to_suggest = [] 
        new_products_to_suggest = [] # <--- YENİ: Fiyat Anahtarına eklenecekler
        
        for index, row in df.iterrows():
            raw_prod = str(row["RAW_OCR_ADI"])  
            edited_prod = str(row["ÜRÜN ADI"]) 
            
            # Sözlük/Fuzzy ile çözülen standart isim
            final_prod = resolve_product_name(edited_prod, client, company) 
            
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            
            fiyat = 0.0
            key = turkish_lower(final_prod) # Karşılaştırma için normalleştirilmiş key kullan
            
            if key in product_map:
                # 1. VAR OLAN ÜRÜN (Kota Düşülür ve Mapping Önerisi yapılır)
                item = product_map[key]
                fiyat = item['price']
                
                # Kota düşürme
                new_quota = item['quota'] - miktar
                
                # Sütun F (index 5)
                quota_updates.append({'range': f'F{item["row_num"]}', 'values': [[new_quota]]}) 
                msg.append(f"📉 DÜŞÜLDÜ: {final_prod} -> -{miktar} {birim} (Kalan Hak: {new_quota})")
                
                # --- EŞLEŞTİRME SÖZLÜĞÜ ÖNERİSİ ---
                # Ham OCR metni ile son çözülen standart isim farklıysa
                if turkish_lower(raw_prod) != turkish_lower(final_prod):
                    new_mappings_to_suggest.append({"raw": raw_prod, "std": final_prod})
                # -----------------------------------

            else:
                # 2. YENİ ÜRÜN (Fiyat Anahtarına Ekleme Önerisi yapılır)
                
                # Eğer resolve_product_name başarısız olduysa, final_prod, edited_prod'a eşit olacaktır.
                # Eğer bu ürün hala fiyat listesinde yoksa (ki bu blokta olduğumuza göre yok), 
                # bu yeni bir ürün demektir.
                
                # Yeni ürün önerisi listesine ekle
                new_products_to_suggest.append({
                    "product": edited_prod, # Kullanıcının girdiği/düzelttiği standart isim
                    "company": company,
                    "unit": birim,
                    "quota": miktar 
                })
                
                msg.append(f"⚠️ UYARI: Yeni Ürün **{edited_prod}** bulundu. Fiyat Anahtarına eklenmeli.")

            tutar = miktar * fiyat
            
            # Firma Log
            company_log_rows.append([
                date_str, final_prod, miktar, birim, fiyat, f"{tutar:.2f}", "Tüketim (İrsaliye)"
            ])
        
        # Toplu Güncelleme
        if quota_updates: price_ws.batch_update(quota_updates)
        if company_log_rows: ws_company.append_rows(company_log_rows)
    
        # Başarılı dönüşte önerileri de gönder
        return True, " | ".join(msg), new_mappings_to_suggest, new_products_to_suggest
    except Exception as e: 
        return False, f"Genel Hata: {str(e)}", [], [] 

# --- SAYFA RENDER FONKSİYONU ---
def render_page(sel_model):
    st.header("📝 Tüketim Fişi (İrsaliye)")
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
        # RAW_OCR_ADI sütununu kullanıcıdan gizle
        temp_df_for_editor = st.session_state['irsaliye_df'].drop(columns=['RAW_OCR_ADI'], errors='ignore')

        st.subheader("Okunan Ürünleri Kontrol Et ve Gerekirse Düzelt")
        edited_df = st.data_editor(temp_df_for_editor, num_rows="dynamic", use_container_width=True)
        
        if st.button("💾 Kaydet ve Stoktan Düş", type="primary"):
            
            # 1. Orijinal df'i (RAW_OCR_ADI sütunu ile) kopyala
            df_to_save = st.session_state['irsaliye_df'].copy()
            
            # 2. Kullanıcının yaptığı düzenlemeleri (RAW_OCR_ADI hariç) geri aktar
            for col in edited_df.columns:
                 df_to_save[col] = edited_df[col] 

            with st.spinner("İşleniyor..."):
                # Yeni dönüş değerleri: success, msg, suggestions, new_products
                success, msg, suggestions, new_products = save_receipt_dataframe(df_to_save, selected_company, selected_date)
                
                if success:
                    st.balloons(); st.success("✅ İrsaliye İşlendi!")
                    st.write(msg)
                    
                    # 1. EŞLEŞTİRME SÖZLÜĞÜ ÖNERİSİ
                    if suggestions:
                        st.divider()
                        st.subheader("💡 Otomatik Eşleştirme Önerisi (Sözlük)")
                        
                        unique_mappings = {}
                        for s in suggestions:
                            norm_raw = turkish_lower(s['raw'])
                            unique_mappings[norm_raw] = s
                        
                        st.info(f"Girilen **{len(unique_mappings)}** farklı OCR metni, standart ürün isimleriyle eşleşti. Bunları **Sözlüğe ekleyip** bir daha manuel işlememeyi öğrenelim mi?")
                        
                        # Önerileri liste olarak göster
                        for s in unique_mappings.values():
                            st.markdown(f"**OCR Metni:** *{s['raw']}* $\rightarrow$ **Standart İsim:** **{s['std']}**")
                            
                        if st.button("Sözlüğe Ekle ve Öğren", type="secondary"):
                            mapping_results = []
                            for s in unique_mappings.values():
                                if add_to_mapping(client, s['raw'], s['std']):
                                    mapping_results.append(f"'{s['raw']}' -> '{s['std']}' başarıyla eklendi.")
                                else:
                                    mapping_results.append(f"'{s['raw']}' eklenemedi.")
                            
                            st.success("✅ Tüm eşleştirmeler sözlüğe kaydedildi. Bir dahaki sefere otomatik tanınacaklar.")
                            st.text("\n".join(mapping_results))
                            st.rerun() # Tekrar tetikleme ile güncel listeyi göster

                    # 2. YENİ FİYAT ANAHTARI ÜRÜNÜ ÖNERİSİ
                    if new_products:
                        st.divider()
                        st.subheader("🆕 Fiyat Anahtarı (Stok) Ekleme Önerisi")
                        
                        # Aynı üründen birden fazla varsa miktarını toplayarak tekil ürün listesi oluştur
                        product_summary = {}
                        for p in new_products:
                            key = turkish_lower(p['product'])
                            if key not in product_summary:
                                product_summary[key] = p.copy()
                            else:
                                product_summary[key]['quota'] += p['quota']
                        
                        unique_new_products = product_summary.values()
                        
                        st.warning(f"Aşağıdaki **{len(unique_new_products)}** ürün Fiyat Anahtarınızda **bulunamadı**. Bu ürünleri borçlanma hakkını kullanmak için eklemek ister misiniz?")
                        
                        for p in unique_new_products:
                            st.markdown(f"**Ürün:** *{p['product']}* | **Toplam Miktar:** {p['quota']} {p['unit']}")
                            
                        if st.button("Fiyat Anahtarına Ekle ve Kota Yükle", key="add_new_price_prod", type="danger"):
                            add_results = []
                            for p in unique_new_products:
                                # Ürünü Fiyat Anahtarına 0 fiyatla, irsaliye miktarıyla ekle
                                if add_product_to_price_sheet(client, p['product'], selected_company, p['unit'], p['quota']):
                                    add_results.append(f"'{p['product']}' ({p['quota']} {p['unit']}) başarıyla Fiyat Anahtarına eklendi.")
                                else:
                                    add_results.append(f"'{p['product']}' eklenemedi.")
                            
                            st.success("✅ Tüm yeni ürünler Fiyat Anahtarına eklendi (Fiyat=0.0₺).")
                            st.text("\n".join(add_results))
                            st.rerun() 
                            
                    # İşlem bitti, session state'i temizle
                    if 'irsaliye_df' in st.session_state:
                         del st.session_state['irsaliye_df']
                    
                else: st.error(f"Kayıt Hatası: {msg}")

# --- SON ---
