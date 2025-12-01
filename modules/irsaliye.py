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
    # find_best_match artık resolve_product_name içinde kullanılıyor.
    turkish_lower,     # <--- YENİ EKLENDİ
    add_to_mapping,    # <--- YENİ EKLENDİ
    FILE_STOK,
    PRICE_SHEET_NAME
)

# ... (analyze_receipt_image fonksiyonu değişmedi)

def text_to_dataframe(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        clean_line = line.replace("*", "").strip()
        if not clean_line or "ÜRÜN ADI" in clean_line.upper(): continue
        
        # Ayracın sadece '|' değil, olası diğer ayraçları da düşünerek esnek parse
        parts = [p.strip() for p in re.split(r'\|| - ', clean_line, maxsplit=2)]
        
        if len(parts) >= 3:
            # İrsaliyede fiyat olmaz genelde
            data.append({
                "ÜRÜN ADI": parts[0], 
                "MİKTAR": parts[1], 
                "BİRİM": parts[2],
                "RAW_OCR_ADI": parts[0] # <--- YENİ EKLENDİ (Orijinal OCR metni)
            })
    return pd.DataFrame(data)

def save_receipt_dataframe(df, company, date_obj):
    client = get_gspread_client()
    # Dönüş değerine suggestions (öneriler) eklendi
    if not client: return False, "Google Sheets Bağlantı Hatası", [] 
    
    date_str = date_obj.strftime("%d.%m.%Y")
    
    try:
        sh = client.open(FILE_STOK) 
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        price_data = price_ws.get_all_values()
        
        ws_company = get_or_create_worksheet(sh, company, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "TUTAR", "İŞLEM TÜRÜ"])
        
        product_map = {}
        for idx, row in enumerate(price_data):
            if idx == 0: continue
            if len(row) >= 3:
                db_comp = row[0].strip()
                db_prod = row[1].strip()
                if db_comp == company:
                    # Anahtar küçük harfe çevrildi
                    product_map[turkish_lower(db_prod)] = { 
                        "row": idx + 1, 
                        "quota": clean_number(row[5]) if len(row) >= 6 else 0.0,
                        "price": clean_number(row[2]) 
                    }
        
        quota_updates = []
        company_log_rows = []
        msg = []
        new_mappings_to_suggest = [] # Yeni eşleşme önerilerini toplama listesi
        
        for index, row in df.iterrows():
            # Hem ham OCR metnini hem de kullanıcının düzenlediği metni alıyoruz
            raw_prod = str(row["RAW_OCR_ADI"])  
            edited_prod = str(row["ÜRÜN ADI"]) 
            
            # resolve_product_name, sözlük/bulanık eşleşme sırasıyla çalışır
            final_prod = resolve_product_name(edited_prod, client, company)
            
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            
            # Fiyat bul (DB'den)
            fiyat = 0.0
            key = turkish_lower(final_prod) # Karşılaştırma için normalleştirilmiş key kullan
            
            if key in product_map:
                item = product_map[key]
                fiyat = item['price']
                
                # Kota düşürme
                new_quota = item['quota'] - miktar
                
                quota_updates.append({'range': f'F{item["row"]}', 'values': [[new_quota]]})
                msg.append(f"📉 DÜŞÜLDÜ: {final_prod} -> -{miktar} {birim} (Kalan Hak: {new_quota})")
                
                # --- EŞLEŞTİRME ÖNERİSİ KONTROLÜ ---
                # Ham OCR metni ile son çözülen standart isim farklıysa (ve ham metin sözlükte yoksa)
                if turkish_lower(raw_prod) != turkish_lower(final_prod):
                    # Sözlüğe eklenmesi için öneri olarak kaydet
                    new_mappings_to_suggest.append({"raw": raw_prod, "std": final_prod})
                # -----------------------------------

            else:
                msg.append(f"⚠️ UYARI: {final_prod} faturası/fiyatı bulunamadı, stoktan düşülemedi.")
            
            tutar = miktar * fiyat
            
            # Firma Log
            company_log_rows.append([
                date_str, final_prod, miktar, birim, fiyat, f"{tutar:.2f}", "Tüketim (İrsaliye)"
            ])
        
        # Toplu Güncelleme
        if quota_updates: price_ws.batch_update(quota_updates)
        if company_log_rows: ws_company.append_rows(company_log_rows)
    
        # Başarılı dönüşte önerileri de gönder
        return True, " | ".join(msg), new_mappings_to_suggest 
    except Exception as e: 
        # Hata durumunda boş öneri listesi gönder
        return False, f"Genel Hata: {str(e)}", [] 

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
                # Yeni dönüş değeri: success, msg, suggestions
                success, msg, suggestions = save_receipt_dataframe(df_to_save, selected_company, selected_date)
                
                if success:
                    st.balloons(); st.success("✅ İrsaliye İşlendi!")
                    st.write(msg)
                    
                    # --- EŞLEŞTİRME SÖZLÜĞÜ ÖNERİSİ ---
                    if suggestions:
                        st.divider()
                        st.subheader("💡 Otomatik Eşleştirme Önerisi (Sözlük)")
                        
                        # Tekil önerileri al (aynı ham metni birden fazla kaydetmemek için)
                        unique_suggestions = {}
                        for s in suggestions:
                            # Normalleştirilmiş ham metni anahtar olarak kullan
                            norm_raw = turkish_lower(s['raw'])
                            unique_suggestions[norm_raw] = s
                        
                        st.info(f"Girilen **{len(unique_suggestions)}** farklı OCR metni, standart ürün isimleriyle eşleşti (manuel/bulanık eşleşme). Bu eşleşmeleri **Sözlüğe ekleyip** bir daha manuel işlememeyi öğrenelim mi?")
                        
                        # Önerileri liste olarak göster
                        for s in unique_suggestions.values():
                            st.markdown(f"**OCR Metni:** *{s['raw']}* $\rightarrow$ **Standart İsim:** **{s['std']}**")
                            
                        if st.button("Sözlüğe Ekle ve Öğren", type="secondary"):
                            mapping_results = []
                            # Tekil önerileri işleriz
                            for s in unique_suggestions.values():
                                # Sözlüğe ekleme fonksiyonunu çağır
                                if add_to_mapping(client, s['raw'], s['std']):
                                    mapping_results.append(f"'{s['raw']}' -> '{s['std']}' başarıyla eklendi.")
                                else:
                                    mapping_results.append(f"'{s['raw']}' eklenemedi.")
                            
                            st.success("✅ Tüm eşleştirmeler sözlüğe kaydedildi. Bir dahaki sefere otomatik tanınacaklar.")
                            st.text("\n".join(mapping_results))

                    # İşlem bitti, session state'i temizle
                    del st.session_state['irsaliye_df']
                    
                else: st.error(f"Kayıt Hatası: {msg}")
