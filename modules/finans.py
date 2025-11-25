import streamlit as st
import pandas as pd
import google.generativeai as genai
import io
import json
import datetime # Yeni
from modules.utils import get_gspread_client, get_drive_service, find_folder_id, SHEET_YATILI, SHEET_GUNDUZLU, SHEET_SETTINGS

# --- GEMINI AYARLARI ---
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# =========================================================================
# 1. ORTAK VERİ YÖNETİMİ FONKSİYONLARI (Sheets)
# =========================================================================

def get_data(sheet_name):
    """Google Sheets'ten veriyi çeker (Hata önleyici mod)."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip") # Ana dosya adınız
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        # st.error(f"Veri çekme hatası ({sheet_name}): {e}") # Hata mesajını gizleyelim
        return pd.DataFrame()

# modules/finans.py içinde değiştirilecek 2 fonksiyon:

def get_current_unit_price():
    """
    FINANS_AYARLAR sayfasından veriyi çeker.
    Sheets'ten gelen veri "73,15" (yazı) de olsa, 73,15 (sayı) da olsa,
    hatta 7.315 (hatalı sayı) da olsa doğru formata (73.15) zorlar.
    """
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        
        # get_all_values() kullanıyoruz ki ham veriyi (string) görelim, yorum katmasın.
        all_rows = ws.get_all_values()
        
        # Başlık hariç veri varsa
        if len(all_rows) > 1:
            last_row = all_rows[-1] # En son satır
            # Sütun sırası: [Yil, Birim_Fiyat, ...] -> 2. eleman (index 1)
            raw_price = last_row[1] 
            
            # Gelen veriyi string'e çevirip temizleyelim
            s_price = str(raw_price).replace("₺", "").replace("TL", "").strip()
            
            # Eğer boşsa
            if not s_price: return 0.0

            # EĞER 7.315 GİBİ BİR SAYI GELDİYSE VE BİZ BUNUN 1000'DEN KÜÇÜK OLMASI GEREKTİĞİNİ BİLİYORSAK
            # (Bu kısım, geçmişte yanlış kaydedilen 7315'leri düzeltmek için bir yamadır)
            if "." in s_price and "," not in s_price:
                 # Noktayı silip sayıya çevirmeyi dene
                 temp_val = float(s_price.replace(".", ""))
                 # Eğer birim fiyat 1000 TL'den büyükse, muhtemelen yanlışlıkla 100 ile çarpılmıştır.
                 if temp_val > 1000:
                     return temp_val / 100
            
            # STANDART DÜZELTME (Virgüllü gelirse)
            # "73,15" -> "73.15" yap
            if "," in s_price:
                s_price = s_price.replace(".", "") # Binlik noktalarını at
                s_price = s_price.replace(",", ".") # Virgülü ondalık yap
            
            return float(s_price)
            
        return 0.0
    except Exception as e:
        # Hata durumunda log basabiliriz ama kullanıcıya 0 dönelim
        print(f"Hata: {e}")
        return 0.0

def update_unit_price(new_price, year):
    """
    Yeni birim fiyatı Sheets'e kaydeder.
    Python'daki 73.15 sayısını, Sheets'e "73,15" (YAZI) olarak zorla gönderir.
    Böylece Sheets bunu 7315 sanmaz.
    """
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        
        # PÜF NOKTASI BURASI:
        # Sayıyı önce virgüle çeviriyoruz: 73.15 -> "73,15"
        price_tr_string = f"{new_price:.2f}".replace('.', ',')
        
        # value_input_option='USER_ENTERED' sayesinde Sheets bunu "Klavyeden 73,15 yazılmış" gibi algılar.
        ws.append_row([year, price_tr_string, ''], value_input_option='USER_ENTERED') 
        return True
    except Exception as e:
        st.error(f"Birim fiyat güncelleme hatası: {e}")
        return False

def update_annual_taksit(total_fee, year):
    """Yeni yıllık taksit tutarını Sheets'e kaydeder (FINANS_AYARLAR)."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        # Sadece Yıllık Taksit Toplamını güncelliyoruz. [Yil, Birim_Fiyat(Boş), Yillik_Taksit_Toplami]
        ws.append_row([year, '', total_fee], value_input_option='USER_ENTERED') 
        return True
    except Exception as e:
        st.error(f"Taksit tutarı güncelleme hatası: {e}")
        return False

def generate_monthly_accrual(selected_month, days_eaten, unit_price):
    """Tüm gündüzlü öğrenciler için aylık tahakkuku hesaplar ve Sheets'e kaydeder."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_GUNDUZLU)
        
        # Öğrenci listesini mevcut Gündüzlü sheet'teki benzersiz kayıtlardan çek
        df_gunduzlu_all = get_data(SHEET_GUNDUZLU)
        unique_students = df_gunduzlu_all[['TC_No', 'Ad_Soyad', 'Sinif']].drop_duplicates()
        
        tahakkuk_tutar = days_eaten * unit_price
        new_rows = []
        
        for index, row in unique_students.iterrows():
            if row.get('Ad_Soyad'): # Adı boş olmayanları al
                # Sütun sırası: TC_No, Ad_Soyad, Sinif, Ay, Yenen_Yemek_Sayisi, Birim_Fiyat, Toplam_Tutar, Odenen_Durum, Dekont_Link
                new_row = [
                    row.get('TC_No', ''),
                    row.get('Ad_Soyad', 'Bilinmiyor'),
                    row.get('Sinif', ''),
                    selected_month,
                    days_eaten,
                    unit_price,
                    tahakkuk_tutar,
                    'Bekliyor', 
                    '' # Dekont_Link
                ]
                new_rows.append(new_row)
            
        if new_rows:
            ws.append_rows(new_rows, value_input_option='USER_ENTERED')
            return len(new_rows)
        return 0
        
    except Exception as e:
        st.error(f"Tahakkuk kaydetme hatası: {e}")
        return -1

# modules/finans.py içine (Diğer fonksiyonların yanına ekle)

# modules/finans.py içinde distribute_yatili_installments fonksiyonunu GÜNCELLE:

def distribute_yatili_installments(total_fee, year):
    """
    Tüm paralı yatılı öğrencilerin yıllık ücretini ve 4 taksit tutarını günceller.
    TC NO KULLANILMAZ. Sadece Ad_Soyad ve Sinif baz alınır.
    """
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_YATILI)
        
        # Mevcut verileri al
        all_data = ws.get_all_records()
        
        # HEDEF SÜTUNLAR (Para ile ilgili olanlar)
        target_columns = [
            'Toplam_Yillik_Ucret', 
            'Taksit1_Tutar', 
            'Taksit2_Tutar', 
            'Taksit3_Tutar', 
            'Taksit4_Tutar'
        ]
        
        # DURUM 1: SAYFA BOŞSA VEYA HİÇ VERİ YOKSA
        if not all_data:
            # Başlıkları kontrol et
            current_headers = ws.row_values(1)
            if not current_headers:
                # Sayfa bomboşsa, TC'siz yeni başlıkları ekle
                # Ad_Soyad zorunlu, Sinif opsiyonel ama dursun, iyidir.
                ws.append_row(["Ad_Soyad", "Sinif"] + target_columns)
                return False, "Sayfa boştu, 'Ad_Soyad' ve Taksit başlıkları eklendi. Lütfen öğrenci isimlerini girip tekrar deneyin."
            
            return False, "Listede hiç öğrenci yok. Lütfen 'Ad_Soyad' sütununa öğrencileri ekleyin."

        # DURUM 2: ÖĞRENCİ VAR, İŞLEM YAPALIM
        df = pd.DataFrame(all_data)
        
        # Eğer yanlışlıkla eski TC sütunu kaldıysa ve pandas onu okuduysa, işlemden düşürebiliriz
        if 'TC_No' in df.columns:
            df = df.drop(columns=['TC_No'])

        # Ad_Soyad sütunu var mı kontrolü (Hayati önem taşır)
        if 'Ad_Soyad' not in df.columns:
             return False, "Hata: Sayfada 'Ad_Soyad' sütunu bulunamadı."

        installment_amount = total_fee / 4.0
        
        # Hesaplama ve Sütun Doldurma
        df['Toplam_Yillik_Ucret'] = total_fee
        for col in target_columns[1:]: # Taksitler
            df[col] = installment_amount

        # --- TEMİZLİK VE KAYDETME ---
        df = df.fillna("")
        
        # Veriyi listeye çevir (Başlıklar + Veri)
        updated_data = [df.columns.tolist()] + df.values.tolist()
        
        # Sheet'i temizle ve yeniden yaz
        ws.clear()
        ws.update(values=updated_data, range_name="A1")
        
        # Ayarlar sayfasına da referans olarak kaydı güncelle
        update_annual_taksit(total_fee, year)
        
        return True, f"{len(df)} öğrencinin (TC'siz) taksit planı güncellendi."
        
    except Exception as e:
        return False, f"Hata: {e}"
        
# =========================================================================
# 2. DRIVE VE GEMINI FONKSİYONLARI (Aynı Kalıyor)
# =========================================================================

def download_file_from_drive(service, file_id):
    """Drive'dan dosya verisini (byte olarak) indirir."""
    # (Kod aynı kalıyor...)
    try:
        request = service.files().get_media(fileId=file_id)
        file_data = request.execute()
        return file_data
    except Exception as e:
        st.error(f"Dosya indirme hatası: {e}")
        return None

def analyze_receipt_with_gemini(file_data, mime_type, model_name):
    """Dosyayı Gemini'ye gönderir ve JSON çıktı ister."""
    # (Kod aynı kalıyor...)
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
        doc_part = {"mime_type": mime_type, "data": file_data}
        response = model.generate_content([prompt, doc_part])
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] 
            if text.endswith("```"):
                text = text.rsplit("\n", 1)[0] 
        return json.loads(text)
        
    except Exception as e:
        st.error(f"Gemini Analiz Hatası: {e}")
        return None

def move_file_in_drive(service, file_id, source_folder_id, destination_folder_id):
    """Bir dosyayı Drive içinde bir klasörden diğerine taşır."""
    # (Kod aynı kalıyor...)
    try:
        file = service.files().update(
            fileId=file_id,
            addParents=destination_folder_id, 
            removeParents=source_folder_id,   
            fields='id, parents'
        ).execute()
        return True
    except Exception as e:
        st.error(f"Dosya taşıma hatası: {e}")
        return False

def write_to_gunduzlu_sheet(analiz_sonucu, dekont_link):
    """Gündüzlü öğrencilerin yemek ödeme dekontunu Sheets'e kaydeder."""
    # (Kod aynı kalıyor...)
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_GUNDUZLU)
        
        # Sütun sırasına göre veri satırını oluştur
        new_row = [
            analiz_sonucu.get('ogrenci_tc', ''),
            analiz_sonucu.get('ogrenci_ad', 'Bilinmiyor'),
            '', 
            analiz_sonucu.get('tarih', ''), # Tarih
            '', # Yenen_Yemek_Sayisi
            '', # Birim_Fiyat
            analiz_sonucu.get('tutar', 0),
            'Ödendi', 
            dekont_link
        ]
        
        ws.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"Sheets'e yazma hatası (Gündüzlü): {e}")
        return False

# =========================================================================
# 3. RENDER FONKSİYONU
# =========================================================================

def render_page(selected_model):
    st.header("💰 Finans Yönetimi")
    st.caption(f"Aktif Zeka: {selected_model}")

    # Sekmeler GÜNCELLENDİ
    tab1, tab2, tab3, tab4 = st.tabs(["🏫 Yatılı", "🍽️ Gündüzlü", "🤖 Dekont İşle", "⚙️ Ayarlar/Tahakkuk"])

    # --- TAB 1: PARALI YATILI GÖRÜNTÜLEME ---
    with tab1:
        st.subheader("Taksit Takip Çizelgesi")
        df_yatili = get_data(SHEET_YATILI)
        
        if not df_yatili.empty:
            # Veri Temizliği (Hata önleme)
            para_sutunlari = ['Toplam_Yillik_Ucret', 'Odenen_Toplam', 'Kalan_Borc', 'Taksit1_Tutar', 'Taksit2_Tutar', 'Taksit3_Tutar', 'Taksit4_Tutar']
            for col in para_sutunlari:
                if col in df_yatili.columns:
                    df_yatili[col] = pd.to_numeric(df_yatili[col], errors='coerce').fillna(0).astype(float)
            
            # Özet Kartlar
            col1, col2 = st.columns(2)
            toplam_borc = df_yatili['Toplam_Yillik_Ucret'].sum() if 'Toplam_Yillik_Ucret' in df_yatili.columns else 0.0
            toplam_odenen = df_yatili['Odenen_Toplam'].sum() if 'Odenen_Toplam' in df_yatili.columns else 0.0
            
            col1.metric("Toplam Beklenen Gelir", f"{toplam_borc:,.2f} ₺")
            col2.metric("Tahsil Edilen", f"{toplam_odenen:,.2f} ₺", delta=f"{toplam_odenen - toplam_borc:,.2f} ₺")
            
            st.dataframe(df_yatili, use_container_width=True)
        else:
            st.warning(f"'{SHEET_YATILI}' sayfasında veri bulunamadı.")
            
    # --- TAB 2: GÜNDÜZLÜ YEMEK GÖRÜNTÜLEME ---
    with tab2:
        st.subheader("Aylık Yemek Ücretleri")
        df_gunduzlu = get_data(SHEET_GUNDUZLU)
        if not df_gunduzlu.empty:
            # Filtreleme (Örnek: Ay seçimi)
            if 'Ay' in df_gunduzlu.columns:
                aylar = df_gunduzlu['Ay'].unique()
                if len(aylar) > 0:
                    secilen_ay = st.selectbox("Dönem Seçiniz:", sorted(aylar, reverse=True))
                    df_goster = df_gunduzlu[df_gunduzlu['Ay'] == secilen_ay]
                else:
                    df_goster = df_gunduzlu
            else:
                df_goster = df_gunduzlu
            st.dataframe(df_goster, use_container_width=True)
        else:
            st.warning(f"'{SHEET_GUNDUZLU}' sayfasında veri bulunamadı.")


    # --- TAB 3: AI DEKONT İŞLEME (AYNI KALIYOR) ---
    with tab3:
        st.subheader("🤖 Otomatik Dekont Analizi")
        
        service = get_drive_service()
        if not service: return

        root_id = find_folder_id(service, "Mutfak_ERP_Drive")
        finans_id = find_folder_id(service, "Finans", parent_id=root_id)
        target_id = find_folder_id(service, "Gelen_Dekontlar", parent_id=finans_id)
        processed_id = find_folder_id(service, "Islenenler", parent_id=finans_id)
        
        if not (target_id and processed_id):
             st.error("❌ Klasör yapısı bulunamadı (Gelen_Dekontlar veya Islenenler).")
             return
             
        # Dosyaları listele
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
            
            if st.button("🚀 Bu Dekontu Analiz Et"):
                with st.spinner("Dosya indiriliyor ve Gemini'ye gönderiliyor..."):
                    file_data = download_file_from_drive(service, selected_file_id)
                    if file_data:
                        analiz_sonucu = analyze_receipt_with_gemini(file_data, selected_file_meta['mimeType'], selected_model)
                        if analiz_sonucu:
                            st.session_state['last_analysis'] = analiz_sonucu 
                            st.session_state['last_file_id'] = selected_file_id
                            st.success("✅ Analiz Tamamlandı!")
                            st.json(analiz_sonucu)
                        else:
                            st.error("Analizden sonuç dönmedi.")
                            
            if st.session_state.get('last_analysis') and st.session_state.get('last_file_id') == selected_file_id:
                st.subheader("İşlem Onayı")
                analiz = st.session_state['last_analysis']
                
                st.warning(f"⚠️ Dekont tahmini **{analiz['tur_tahmini']}** olarak belirlendi. Lütfen kontrol edin.")
                
                if st.button("💾 Veritabanına Kaydet ve Drive'da Taşı"):
                    if analiz['tur_tahmini'] == 'YEMEK':
                        dekont_link = f"https://drive.google.com/file/d/{selected_file_id}/view?usp=drivesdk" 
                        
                        if write_to_gunduzlu_sheet(analiz, dekont_link):
                            st.success("1/2: Veri Gündüzlü Sheet'e başarıyla kaydedildi!")
                            if move_file_in_drive(service, selected_file_id, target_id, processed_id):
                                st.success("2/2: Dosya 'Islenenler' klasörüne taşındı. İşlem tamamlandı.")
                                del st.session_state['last_analysis']
                                del st.session_state['last_file_id']
                                st.rerun() 
                            else:
                                st.error("2/2: Dosya taşıma başarısız oldu.")
                        else:
                            st.error("1/2: Sheets'e kaydetme başarısız oldu.")
                    else:
                        st.error("Bu TAKSİT ödemesidir. Şu an sadece YEMEK ödemeleri otomatik kaydedilmektedir.")


  # --- TAB 4: AYARLAR VE TAHAKKUK (GÜNCELLENMİŞ VERSİYON) ---
    with tab4:
        st.subheader("⚙️ Finans Ayarları ve Aylık Giriş")
        
        # ----------------------------------------
        # BÖLÜM 1: BİRİM FİYAT GÜNCELLEME (Yıllık)
        # ----------------------------------------
        st.markdown("#### 💸 Yemek Birim Fiyatı Ayarları")
        
        current_price = get_current_unit_price()
        st.info(f"Mevcut Güncel Birim Fiyat: **{current_price:,.2f} ₺**")
        
        with st.form("unit_price_form"):
            new_price = st.number_input("Yeni Günlük Birim Fiyat (₺):", min_value=0.0, value=current_price, step=0.01, format="%.2f")
            current_year = st.number_input("Geçerlilik Yılı:", min_value=2024, value=datetime.date.today().year + 1, step=1)
            price_submit = st.form_submit_button("Birim Fiyatı Güncelle ve Kaydet")
            
            if price_submit:
                if update_unit_price(new_price, current_year):
                    st.success(f"Birim fiyat başarıyla {new_price} ₺ olarak güncellendi.")
                    st.rerun()
                else:
                    st.error("Güncelleme hatası.")
        
        st.divider()
        
        # ----------------------------------------
        # BÖLÜM 2: GÜNDÜZLÜ ÖĞRENCİ AYLIK GÜN GİRİŞİ (CANLI HESAPLAMA)
        # ----------------------------------------
        st.markdown("#### 🗓️ Tüm Gündüzlü Öğrenciler İçin Aylık Tahakkuk Girişi")
        
        unique_student_count = get_data(SHEET_GUNDUZLU)[['Ad_Soyad', 'TC_No']].drop_duplicates().shape[0]

        if unique_student_count > 0 and current_price > 0:
            st.info(f"Listedeki **{unique_student_count}** benzersiz öğrenciye tahakkuk yapılacaktır.")

            # --- DÜZELTME 1: TÜRKÇE AY İSİMLERİ ---
            tr_aylar = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", 
                        "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
            
            today = datetime.date.today()
            # Son 3 ay ve Gelecek 3 ayın listesini Türkçe oluştur
            ay_secenekleri = []
            for i in range(-3, 4):
                target_date = today + datetime.timedelta(days=30*i) # Yaklaşık tarih
                yil = target_date.year
                ay_index = target_date.month
                ay_str = f"{yil}-{tr_aylar[ay_index]}"
                ay_secenekleri.append(ay_str)
            
            # Listeyi tersten sırala (En yakın tarih üstte olsun) ve benzersiz yap
            ay_secenekleri = sorted(list(set(ay_secenekleri)), reverse=True)

            # --- DÜZELTME 2: FORM DIŞINA ALINAN GİRİŞLER (CANLI GÜNCELLEME İÇİN) ---
            col_s1, col_s2 = st.columns(2)
            
            # Ay Seçimi
            selected_month = col_s1.selectbox("Tahakkuk Ayı Seçiniz:", ay_secenekleri)
            
            # Gün Sayısı (Değişince anında hesaplasın diye form dışında)
            days_eaten = col_s2.number_input(f"Seçilen Ayda Tahakkuk Edilecek Gün Sayısı:", 
                                             min_value=0, max_value=31, value=20)
            
            # ANLIK HESAPLAMA VE GÖSTERİM
            hesaplanan_tutar = days_eaten * current_price
            
            st.success(f"""
            📊 **HESAPLAMA ÖZETİ:**
            * Günlük Ücret: **{current_price:,.2f} ₺**
            * Gün Sayısı: **{days_eaten}**
            * **Öğrenci Başı Tutar: {hesaplanan_tutar:,.2f} ₺**
            * **Toplam Ciro ({unique_student_count} Öğrenci): {hesaplanan_tutar * unique_student_count:,.2f} ₺**
            """)
            
            # KAYDETME BUTONU
            if st.button(f"✅ {selected_month} Ayı İçin Tahakkukları ONAYLA ve KAYDET"):
                if hesaplanan_tutar > 0:
                    with st.spinner("Tahakkuklar işleniyor..."):
                        count = generate_monthly_accrual(selected_month, days_eaten, current_price)
                        if count > 0:
                            st.success(f"✅ {count} adet kayıt başarıyla oluşturuldu!")
                            st.rerun()
                        else:
                            st.error("Kayıt sırasında hata oluştu.")
                else:
                    st.error("Tutar 0 olamaz.")

        else:
            if current_price == 0: st.error("Lütfen önce Birim Fiyatı güncelleyin.")
            else: st.warning("Gündüzlü öğrenciler için Tahakkuk oluşturulamadı. Öğrenci listesini kontrol edin.")
            
        st.divider() 
        
        # ----------------------------------------
        # BÖLÜM 3: PARALI YATILI TAKSİT AYARLARI (Yıllık)
        # ----------------------------------------
        st.markdown("#### 🏫 Yatılı Öğrenci Taksit Ayarları")
        
        with st.form("taksit_form"):
            st.write("Yıllık Toplam Taksit Ücretini girin (4 eşit taksite bölünür):")
            yillik_taksit_toplam = st.number_input("Toplam Yıllık Ücret (₺):", min_value=0.0, value=20000.0, step=100.0)
            taksit_tutari = yillik_taksit_toplam / 4
            st.info(f"Her Bir Taksit Tutarı: **{yillik_taksit_toplam:,.2f} ₺** / 4 = **{taksit_tutari:,.2f} ₺**")
            taksit_yil = st.number_input("Geçerlilik Yılı:", min_value=2024, value=datetime.date.today().year + 1, step=1)
            
            taksit_submit = st.form_submit_button("Taksit Ayarlarını Kaydet")
            
            if taksit_submit:
                if update_annual_taksit(yillik_taksit_toplam, taksit_yil):
                    st.success(f"Yıllık taksit toplamı {yillik_taksit_toplam:,.2f} ₺ olarak kaydedildi.")
                else:
                    st.error("Hata oluştu.")
