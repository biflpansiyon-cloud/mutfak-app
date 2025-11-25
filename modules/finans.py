import streamlit as st
import pandas as pd
import google.generativeai as genai
import io
import json
from modules.utils import get_gspread_client, get_drive_service, find_folder_id, SHEET_YATILI, SHEET_GUNDUZLU
# modules/finans.py içine, üstteki importların hemen altına ekle

def move_file_in_drive(service, file_id, source_folder_id, destination_folder_id):
    """Bir dosyayı Drive içinde bir klasörden diğerine taşır."""
    try:
        file = service.files().update(
            fileId=file_id,
            addParents=destination_folder_id, # Yeni klasöre ekle
            removeParents=source_folder_id,   # Eski klasörden çıkar
            fields='id, parents'
        ).execute()
        return True
    except Exception as e:
        st.error(f"Dosya taşıma hatası: {e}")
        return False

def write_to_gunduzlu_sheet(analiz_sonucu, dekont_link):
    """Gündüzlü öğrencilerin yemek ödeme dekontunu Sheets'e kaydeder."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_GUNDUZLU)
        
        # Sütun sırasına göre veri satırını oluştur
        new_row = [
            analiz_sonucu.get('ogrenci_tc', ''),
            analiz_sonucu.get('ogrenci_ad', 'Bilinmiyor'),
            '', # Sinif (Bu veriyi henüz Geminiden istemedik, şimdilik boş)
            '2025-Ekim', # Ay (analiz_sonucu['tarih']'ten ay çekimi karmaşık, şimdilik sabit)
            '', # Yenen_Yemek_Sayisi (Bu ödeme, tahakkuk değil)
            '', # Birim_Fiyat
            analiz_sonucu.get('tutar', 0),
            'Ödendi', # Odenen_Durum
            dekont_link
        ]
        
        ws.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"Sheets'e yazma hatası (Gündüzlü): {e}")
        return False
        
# --- GEMINI AYARLARI ---
# API Key'i secrets dosyasından alıyoruz
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

def get_data(sheet_name):
    """Google Sheets'ten veriyi çeker (Hata önleyici mod)."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"Veri çekme hatası ({sheet_name}): {e}")
        return pd.DataFrame()

def download_file_from_drive(service, file_id):
    """Drive'dan dosya verisini (byte olarak) indirir."""
    try:
        request = service.files().get_media(fileId=file_id)
        file_data = request.execute()
        return file_data
    except Exception as e:
        st.error(f"Dosya indirme hatası: {e}")
        return None

def analyze_receipt_with_gemini(file_data, mime_type, model_name):
    """Dosyayı Gemini'ye gönderir ve JSON çıktı ister."""
    
    # Model objesini oluştur
    model = genai.GenerativeModel(model_name)
    
    prompt = """
    Sen uzman bir muhasebe asistanısın. Bu bir banka dekontu (resim veya PDF).
    Lütfen şu bilgileri analiz et ve SADECE saf bir JSON formatında ver (Markdown blokları olmadan):
    
    {
        "tarih": "YYYY-AA-GG formatında işlem tarihi",
        "gonderen_ad_soyad": "Parayı gönderen kişinin adı",
        "tutar": "Sadece sayısal değer (örn: 1500.50)",
        "aciklama": "Dekonttaki açıklama metni",
        "ogrenci_tc": "Açıklamada varsa öğrenci TC'si, yoksa boş string",
        "ogrenci_ad": "Açıklamada varsa öğrenci adı, yoksa boş string",
        "tur_tahmini": "Açıklamaya bakarak bu 'YEMEK' mi yoksa 'TAKSİT' mi tahmin et"
    }
    
    Eğer okuyamadığın bir alan varsa null veya boş bırak.
    """
    
    try:
        # Görüntü/PDF verisi için blob oluştur
        doc_part = {
            "mime_type": mime_type,
            "data": file_data
        }
        
        response = model.generate_content([prompt, doc_part])
        
        # Yanıtı temizle (Bazen ```json ... ``` içinde gelir)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] # İlk satırı at
            if text.endswith("```"):
                text = text.rsplit("\n", 1)[0] # Son satırı at
                
        return json.loads(text)
        
    except Exception as e:
        st.error(f"Gemini Analiz Hatası: {e}")
        return None

def render_page(selected_model):
    st.header("💰 Finans Yönetimi")
    st.caption(f"Aktif Zeka: {selected_model}")

    tab1, tab2, tab3 = st.tabs(["🏫 Paralı Yatılı (Taksit)", "🍽️ Gündüzlü (Yemek)", "🤖 Dekont İşle (AI)"])

    # --- TAB 1 & 2 (GÖRÜNTÜLEME) ---
    # modules/finans.py içinde, tab1 bloğunda GÜNCELLEME:

    # --- TAB 1: PARALI YATILI ---
    with tab1:
        st.subheader("Taksit Takip Çizelgesi")
        df_yatili = get_data(SHEET_YATILI)
        
        if not df_yatili.empty:
            # --- VERİ TEMİZLİĞİ (GÜNCELLEME BURADA) ---
            # Hata veren tüm para sütunlarını temizleme listesine alıyoruz
            para_sutunlari = [
                'Toplam_Yillik_Ucret', 'Odenen_Toplam', 'Kalan_Borc', 
                'Taksit1_Tutar', 'Taksit2_Tutar', 'Taksit3_Tutar', 'Taksit4_Tutar'
            ]
            
            for col in para_sutunlari:
                if col in df_yatili.columns:
                    # Zorla sayıya çevir (hata verirse NaN yap), NaN'ları 0 ile doldur
                    df_yatili[col] = pd.to_numeric(df_yatili[col], errors='coerce').fillna(0).astype(float)
            # --- VERİ TEMİZLİĞİ SONU ---
            
            # Özet Kartlar (toplam_borc artık kesinlikle float/int)
            col1, col2 = st.columns(2)
            toplam_borc = df_yatili['Toplam_Yillik_Ucret'].sum() if 'Toplam_Yillik_Ucret' in df_yatili.columns else 0.0
            toplam_odenen = df_yatili['Odenen_Toplam'].sum() if 'Odenen_Toplam' in df_yatili.columns else 0.0
            
            col1.metric("Toplam Beklenen Gelir", f"{toplam_borc:,.2f} ₺")
            col2.metric("Tahsil Edilen", f"{toplam_odenen:,.2f} ₺", delta=f"{toplam_odenen - toplam_borc:,.2f} ₺")
            
            st.dataframe(df_yatili, use_container_width=True)
        else:
            st.warning(f"'{SHEET_YATILI}' sayfasında veri bulunamadı veya sütun başlıkları hatalı.")
            
    with tab2:
        st.subheader("Yemek Ödemeleri")
        df_gunduzlu = get_data(SHEET_GUNDUZLU)
        if not df_gunduzlu.empty:
            st.dataframe(df_gunduzlu, use_container_width=True)

    # --- TAB 3: SİHİRLİ BÖLÜM ---
    # modules/finans.py içinde, render_page fonksiyonundaki TAB 3 bloğu GÜNCELLENMİŞTİR:

    # --- TAB 3: SİHİRLİ BÖLÜM ---
    with tab3:
        st.subheader("🤖 Otomatik Dekont Analizi")
        
        # ... (Önceki kod: Drive servisini başlatma ve klasör ID'lerini bulma) ...
        # (Bu kısım aynı kalacak, sadece Islenenler klasör ID'sini ekliyoruz)
        
        service = get_drive_service()
        if not service:
            st.warning("Drive servisi başlatılamadı.")
            return

        # Klasörleri bul (Islenenler klasörünü de buluyoruz)
        root_id = find_folder_id(service, "Mutfak_ERP_Drive")
        finans_id = find_folder_id(service, "Finans", parent_id=root_id)
        target_id = find_folder_id(service, "Gelen_Dekontlar", parent_id=finans_id)
        processed_id = find_folder_id(service, "Islenenler", parent_id=finans_id) # YENİ
        
        if not processed_id:
             st.error("❌ 'Islenenler' klasörü bulunamadı. Lütfen 'Finans' içine bu klasörü açın.")
             return
             
        if target_id:
            # ... (Önceki kod: Dosyaları listeleme) ...
            results = service.files().list(
                q=f"'{target_id}' in parents and trashed=false",
                fields="files(id, name, mimeType)"
            ).execute()
            files = results.get('files', [])
            
            st.info(f"📂 İşlenmeyi bekleyen **{len(files)}** dekont bulundu.")
            
            if files:
                selected_file_id = st.selectbox("Analiz edilecek dosyayı seçin:", 
                                              options=[f['id'] for f in files],
                                              format_func=lambda x: next((f['name'] for f in files if f['id'] == x), x))
                
                selected_file_meta = next((f for f in files if f['id'] == selected_file_id), None)
                
                # Sadece analiz yap butonu
                if st.button("🚀 Bu Dekontu Analiz Et"):
                    # ... (Analiz kodu, aynı kalacak) ...
                    # Buraya analiz sonucunu st.session_state'e kaydetme mantığını ekleyelim
                    
                    with st.spinner("Dosya indiriliyor ve Gemini'ye gönderiliyor..."):
                        file_data = download_file_from_drive(service, selected_file_id)
                        if file_data:
                            analiz_sonucu = analyze_receipt_with_gemini(file_data, selected_file_meta['mimeType'], selected_model)
                            if analiz_sonucu:
                                st.session_state['last_analysis'] = analiz_sonucu # Sonucu session'a kaydet
                                st.session_state['last_file_id'] = selected_file_id
                                st.success("✅ Analiz Tamamlandı!")
                                st.json(analiz_sonucu)
                            else:
                                st.error("Analizden sonuç dönmedi.")
                        
                # --- YENİ BÖLÜM: KAYDET VE TAŞI ---
                
                if st.session_state.get('last_analysis') and st.session_state.get('last_file_id') == selected_file_id:
                    st.subheader("İşlem Onayı")
                    analiz = st.session_state['last_analysis']
                    
                    st.warning(f"⚠️ Dekont tahmini **{analiz['tur_tahmini']}** olarak belirlendi. Lütfen kontrol edin.")
                    
                    if st.button("💾 Veritabanına Kaydet ve Drive'da Taşı"):
                        
                        # 1. Kaydetme İşlemi (Şimdilik sadece YEMEK'i Gündüzlü Sheet'e yazıyoruz)
                        if analiz['tur_tahmini'] == 'YEMEK':
                            # Drive'dan dosya linkini al (Kayıt için lazım)
                            dekont_link = f"https://drive.google.com/file/d/{selected_file_id}/view?usp=drivesdk" 
                            
                            if write_to_gunduzlu_sheet(analiz, dekont_link):
                                st.success("1/2: Veri Gündüzlü Sheet'e başarıyla kaydedildi!")
                                
                                # 2. Taşıma İşlemi
                                if move_file_in_drive(service, selected_file_id, target_id, processed_id):
                                    st.success("2/2: Dosya 'Islenenler' klasörüne taşındı. İşlem tamamlandı.")
                                    # Başarılı olunca session state'i temizle ve sayfayı yenile
                                    del st.session_state['last_analysis']
                                    del st.session_state['last_file_id']
                                    st.rerun() 
                                else:
                                    st.error("2/2: Dosya taşıma başarısız oldu.")
                            else:
                                st.error("1/2: Sheets'e kaydetme başarısız oldu.")
                        else:
                            st.error("Bu TAKSİT ödemesidir. Şu an sadece YEMEK ödemeleri otomatik kaydedilmektedir.")
