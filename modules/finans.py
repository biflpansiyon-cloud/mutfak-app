import streamlit as st
import pandas as pd
import google.generativeai as genai
import io
import json
import datetime
import difflib
import os # Dosya uzantısı için
from modules.utils import get_gspread_client, get_drive_service, find_folder_id, SHEET_YATILI, SHEET_GUNDUZLU, SHEET_SETTINGS

# --- GEMINI AYARLARI ---
genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# =========================================================================
# 1. ORTAK VERİ YÖNETİMİ
# =========================================================================

def get_data(sheet_name):
    """Google Sheets'ten veriyi çeker."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip") 
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        return pd.DataFrame()

def get_current_unit_price():
    """Birim fiyatı çeker."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        all_rows = ws.get_all_values()
        if len(all_rows) > 1:
            last_row = all_rows[-1] 
            raw_price = last_row[1] 
            s_price = str(raw_price).replace("₺", "").replace("TL", "").strip()
            if not s_price: return 0.0
            if "." in s_price and "," not in s_price:
                 temp_val = float(s_price.replace(".", ""))
                 if temp_val > 1000: return temp_val / 100
            if "," in s_price:
                s_price = s_price.replace(".", "").replace(",", ".") 
            return float(s_price)
        return 0.0
    except: return 0.0

def update_unit_price(new_price, year):
    """Birim fiyatı günceller."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        price_tr_string = f"{new_price:.2f}".replace('.', ',')
        ws.append_row([year, price_tr_string, ''], value_input_option='USER_ENTERED') 
        return True
    except Exception as e:
        st.error(f"Hata: {e}")
        return False

def update_annual_taksit(total_fee, year):
    """Yıllık taksit tutarını günceller."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_SETTINGS)
        ws.append_row([year, '', total_fee], value_input_option='USER_ENTERED') 
        return True
    except Exception as e:
        st.error(f"Hata: {e}")
        return False

# =========================================================================
# 2. İŞ MANTIĞI VE ÖDEME İŞLEME
# =========================================================================

def generate_monthly_accrual(selected_month, days_eaten, unit_price):
    """Gündüzlü tahakkuk oluşturur."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_GUNDUZLU)
        df_gunduzlu_all = get_data(SHEET_GUNDUZLU)
        unique_students = df_gunduzlu_all[['TC_No', 'Ad_Soyad', 'Sinif']].drop_duplicates()
        tahakkuk_tutar = days_eaten * unit_price
        new_rows = []
        for index, row in unique_students.iterrows():
            if row.get('Ad_Soyad'): 
                new_row = [
                    row.get('TC_No', ''), row.get('Ad_Soyad', 'Bilinmiyor'), row.get('Sinif', ''),
                    selected_month, days_eaten, unit_price, tahakkuk_tutar, 'Bekliyor', '' 
                ]
                new_rows.append(new_row)
        if new_rows:
            ws.append_rows(new_rows, value_input_option='USER_ENTERED')
            return len(new_rows)
        return 0
    except Exception as e:
        st.error(f"Hata: {e}")
        return -1

def distribute_yatili_installments(total_fee, year):
    """Yatılı taksitlerini sıfırdan oluşturur."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet("OGRENCI_YATILI") 
        all_values = ws.get_all_values()
        
        if not all_values:
            headers = ["Ad_Soyad", "Sinif", "Toplam_Yillik_Ucret", "Odenen_Toplam", "Kalan_Borc", "Taksit1_Tutar", "Taksit2_Tutar", "Taksit3_Tutar", "Taksit4_Tutar"]
            ws.append_row(headers)
            return False, "Sayfa boştu, başlıklar eklendi."

        student_names = []
        existing_classes = [] 
        start_index = 0
        first_cell = all_values[0][0].lower() if all_values[0] else ""
        if "ad" in first_cell or "isim" in first_cell or "name" in first_cell: start_index = 1
            
        for row in all_values[start_index:]:
            if row and row[0].strip():
                student_names.append(row[0].strip())
                cls = row[1].strip() if len(row) > 1 else ""
                existing_classes.append(cls)
        
        if not student_names: return False, "Öğrenci bulunamadı."

        installment_amount = total_fee / 4.0
        new_data = [["Ad_Soyad", "Sinif", "Toplam_Yillik_Ucret", "Odenen_Toplam", "Kalan_Borc", "Taksit1_Tutar", "Taksit2_Tutar", "Taksit3_Tutar", "Taksit4_Tutar"]]
        
        for i, name in enumerate(student_names):
            sinif = existing_classes[i] if i < len(existing_classes) else ""
            row = [name, sinif, total_fee, 0, total_fee, installment_amount, installment_amount, installment_amount, installment_amount]
            new_data.append(row)
            
        ws.clear()
        ws.update(values=new_data, range_name="A1")
        update_annual_taksit(total_fee, year)
        return True, f"{len(student_names)} öğrencinin tablosu güncellendi."
    except Exception as e:
        return False, f"Hata: {e}"

def find_best_match(name, name_list):
    matches = difflib.get_close_matches(name, name_list, n=1, cutoff=0.6)
    return matches[0] if matches else None

def process_yatili_payment(analiz, dekont_link):
    """
    Yatılı öğrenci ödemesini işler ve KAÇINCI TAKSİT olduğunu döndürür.
    Return: (Success, Message, Taksit_No)
    """
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_YATILI)
        
        all_data = ws.get_all_records()
        df = pd.DataFrame(all_data)
        
        aranan_isim = analiz.get('ogrenci_ad', '')
        if not aranan_isim: return False, "İsim bulunamadı.", 0
            
        mevcut_isimler = df['Ad_Soyad'].tolist()
        bulunan_isim = find_best_match(aranan_isim, mevcut_isimler)
        if not bulunan_isim: return False, f"'{aranan_isim}' bulunamadı.", 0
            
        row_index = df[df['Ad_Soyad'] == bulunan_isim].index[0]
        sheet_row_num = row_index + 2 
        
        # Mevcut durumu al
        current_paid = df.at[row_index, 'Odenen_Toplam']
        if current_paid == '' or current_paid is None: current_paid = 0
        current_paid = float(str(current_paid).replace(',', '').strip() or 0)
        
        total_fee = df.at[row_index, 'Toplam_Yillik_Ucret']
        total_fee = float(str(total_fee).replace(',', '').strip() or 0)
        
        # Taksit Sayısını Tahmin Et (Ödeme yapılmadan önceki duruma göre)
        # Örn: Yıllık 20.000, Taksit 5.000.
        # Şu an ödenen 0 ise -> Taksit 1 ödeniyor.
        # Şu an ödenen 5000 ise -> Taksit 2 ödeniyor.
        taksit_tutari = total_fee / 4.0 if total_fee > 0 else 1
        tahmini_taksit_no = int(current_paid / taksit_tutari) + 1
        if tahmini_taksit_no > 4: tahmini_taksit_no = "Ekstra"
        
        # Hesaplama
        payment_amount = float(analiz.get('tutar', 0))
        new_total_paid = current_paid + payment_amount
        new_remaining = total_fee - new_total_paid
        
        # Güncelleme
        headers = df.columns.tolist()
        col_odenen = headers.index('Odenen_Toplam') + 1
        col_kalan = headers.index('Kalan_Borc') + 1
        
        ws.update_cell(sheet_row_num, col_odenen, new_total_paid)
        ws.update_cell(sheet_row_num, col_kalan, new_remaining)
        
        return True, f"{bulunan_isim}: {payment_amount} TL işlendi. Kalan: {new_remaining} TL", tahmini_taksit_no
        
    except Exception as e:
        return False, f"Hata: {e}", 0

def write_to_gunduzlu_sheet(analiz_sonucu, dekont_link):
    """Gündüzlü ödemesini kaydeder."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip")
        ws = sh.worksheet(SHEET_GUNDUZLU)
        new_row = [
            analiz_sonucu.get('ogrenci_tc', ''), analiz_sonucu.get('ogrenci_ad', 'Bilinmiyor'), '', 
            analiz_sonucu.get('tarih', ''), '', '', analiz_sonucu.get('tutar', 0), 'Ödendi', dekont_link
        ]
        ws.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except Exception as e:
        st.error(f"Hata: {e}")
        return False

# =========================================================================
# 3. DRIVE VE GEMINI ENTEGRASYONU
# =========================================================================

def download_file_from_drive(service, file_id):
    try:
        request = service.files().get_media(fileId=file_id)
        return request.execute()
    except Exception as e:
        st.error(f"Hata: {e}")
        return None

def analyze_receipt_with_gemini(file_data, mime_type, model_name):
    model = genai.GenerativeModel(model_name)
    prompt = """
    Sen uzman bir muhasebe asistanısın. Bu bir banka dekontu.
    JSON olarak ver:
    { "tarih": "YYYY-MM-DD", "gonderen_ad_soyad": "", "tutar": 0.0, "aciklama": "", "ogrenci_tc": "", "ogrenci_ad": "", "tur_tahmini": "'YEMEK' veya 'TAKSİT'" }
    """
    try:
        doc_part = {"mime_type": mime_type, "data": file_data}
        response = model.generate_content([prompt, doc_part])
        text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(text)
    except Exception as e:
        st.error(f"Hata: {e}")
        return None

def move_and_rename_file_in_drive(service, file_id, source_folder_id, destination_folder_id, new_name=None):
    """
    Dosyayı taşır ve opsiyonel olarak YENİDEN ADLANDIRIR.
    """
    try:
        # Önce meta veriyi güncelle (isim değişikliği için)
        file_metadata = {'addParents': destination_folder_id, 'removeParents': source_folder_id}
        
        if new_name:
            file_metadata['name'] = new_name
            
        service.files().update(
            fileId=file_id,
            body=file_metadata,
            fields='id, parents, name'
        ).execute()
        return True
    except Exception as e:
        st.error(f"Dosya taşıma/adlandırma hatası: {e}")
        return False

# =========================================================================
# 4. ARAYÜZ (RENDER)
# =========================================================================

def render_page(selected_model):
    st.header("💰 Finans Yönetimi")
    st.caption(f"Aktif Zeka: {selected_model}")

    tab1, tab2, tab3, tab4 = st.tabs(["🏫 Yatılı", "🍽️ Gündüzlü", "🤖 Dekont İşle", "⚙️ Ayarlar"])

    # --- TAB 1: YATILI ---
    with tab1:
        df_yatili = get_data(SHEET_YATILI)
        if not df_yatili.empty:
            # Temizlik
            for col in ['Toplam_Yillik_Ucret', 'Odenen_Toplam', 'Kalan_Borc']:
                if col in df_yatili.columns:
                     df_yatili[col] = pd.to_numeric(df_yatili[col], errors='coerce').fillna(0).astype(float)
            
            toplam_borc = df_yatili['Toplam_Yillik_Ucret'].sum()
            toplam_odenen = df_yatili['Odenen_Toplam'].sum()
            
            c1, c2 = st.columns(2)
            c1.metric("Toplam Yıllık Beklenti", f"{toplam_borc:,.2f} ₺")
            c2.metric("Tahsilat", f"{toplam_odenen:,.2f} ₺", delta=f"{toplam_odenen - toplam_borc:,.2f} ₺")
            st.dataframe(df_yatili, use_container_width=True)
        else: st.warning("Veri yok.")

    # --- TAB 2: GÜNDÜZLÜ ---
    with tab2:
        df_g = get_data(SHEET_GUNDUZLU)
        if not df_g.empty:
            if 'Ay' in df_g.columns:
                aylar = sorted(df_g['Ay'].unique(), reverse=True)
                secilen = st.selectbox("Dönem:", aylar) if aylar else None
                if secilen: df_g = df_g[df_g['Ay'] == secilen]
            st.dataframe(df_g, use_container_width=True)
        else: st.warning("Veri yok.")

    # --- TAB 3: DEKONT İŞLEME (GÜNCELLENDİ) ---
    with tab3:
        st.subheader("🤖 Dekont Analiz & Arşivleme")
        service = get_drive_service()
        if not service: st.stop()

        root_id = find_folder_id(service, "Mutfak_ERP_Drive")
        finans_id = find_folder_id(service, "Finans", parent_id=root_id)
        gelen_id = find_folder_id(service, "Gelen_Dekontlar", parent_id=finans_id)
        
        # YENİ KLASÖRLER (Eğer yoksa hata verebilir, manuel oluşturulmalı)
        arsiv_yatili_id = find_folder_id(service, "Arsiv_Yatili", parent_id=finans_id)
        arsiv_gunduzlu_id = find_folder_id(service, "Arsiv_Gunduzlu", parent_id=finans_id)
        
        if not (gelen_id): st.error("Gelen_Dekontlar klasörü bulunamadı!"); st.stop()

        # Dosyaları Listele
        results = service.files().list(q=f"'{gelen_id}' in parents and trashed=false", fields="files(id, name, mimeType)").execute()
        files = results.get('files', [])
        
        st.info(f"📂 Bekleyen Dekont Sayısı: **{len(files)}**")
        
        if files:
            sel_id = st.selectbox("Dosya Seç:", [f['id'] for f in files], format_func=lambda x: next((f['name'] for f in files if f['id'] == x), x))
            sel_meta = next((f for f in files if f['id'] == sel_id), None)
            
            if st.button("🚀 Analiz Et"):
                with st.spinner("Analiz ediliyor..."):
                    data = download_file_from_drive(service, sel_id)
                    res = analyze_receipt_with_gemini(data, sel_meta['mimeType'], selected_model)
                    if res:
                        st.session_state['last_analysis'] = res
                        st.session_state['last_file_id'] = sel_id
                        st.success("Analiz Bitti!")
                        st.json(res)
                    else: st.error("Analiz başarısız.")

            # KAYDETME VE TAŞIMA
            if st.session_state.get('last_analysis') and st.session_state.get('last_file_id') == sel_id:
                analiz = st.session_state['last_analysis']
                tur = analiz['tur_tahmini']
                st.info(f"Tür Tahmini: **{tur}**")
                
                # Manuel Düzenleme
                with st.expander("Sonuçları Düzenle"):
                    with st.form("edit"):
                        y_ad = st.text_input("Ad Soyad", analiz.get('ogrenci_ad', ''))
                        y_tut = st.number_input("Tutar", float(analiz.get('tutar', 0)))
                        y_tur = st.selectbox("Tür", ["YEMEK", "TAKSİT"], 0 if tur=='YEMEK' else 1)
                        if st.form_submit_button("Güncelle"):
                            st.session_state['last_analysis'].update({'ogrenci_ad': y_ad, 'tutar': y_tut, 'tur_tahmini': y_tur})
                            st.rerun()

                if st.button("💾 Kaydet ve Arşivle"):
                    link = f"https://drive.google.com/file/d/{sel_id}/view"
                    basari = False
                    msg = ""
                    hedef_klasor = None
                    yeni_isim = sel_meta['name'] # Varsayılan eski isim

                    with st.spinner("İşleniyor..."):
                        # Dosya uzantısını koru (.jpg, .pdf)
                        ext = os.path.splitext(sel_meta['name'])[1]
                        temiz_ad = analiz.get('ogrenci_ad', 'Bilinmiyor').replace(" ", "_")
                        tarih = analiz.get('tarih', 'Tarihsiz')

                        # SENARYO 1: YEMEK (GÜNDÜZLÜ)
                        if analiz['tur_tahmini'] == 'YEMEK':
                            if write_to_gunduzlu_sheet(analiz, link):
                                basari = True
                                msg = "Gündüzlü'ye işlendi."
                                hedef_klasor = arsiv_gunduzlu_id
                                # İsim Formatı: Ad_Soyad_Yemek_Tarih.jpg
                                yeni_isim = f"{temiz_ad}_Yemek_{tarih}{ext}"
                            else: msg = "Sheet hatası."

                        # SENARYO 2: TAKSİT (YATILI)
                        elif analiz['tur_tahmini'] == 'TAKSİT':
                            is_ok, txt, taksit_no = process_yatili_payment(analiz, link)
                            if is_ok:
                                basari = True
                                msg = txt
                                hedef_klasor = arsiv_yatili_id
                                # İsim Formatı: Ad_Soyad_TaksitX.jpg
                                yeni_isim = f"{temiz_ad}_Taksit{taksit_no}{ext}"
                            else: msg = txt

                        # DRIVE TAŞIMA VE ADLANDIRMA
                        if basari:
                            if hedef_klasor:
                                if move_and_rename_file_in_drive(service, sel_id, gelen_id, hedef_klasor, new_name=yeni_isim):
                                    st.success(f"✅ {msg}")
                                    st.info(f"📂 Dosya **{yeni_isim}** olarak arşivlendi.")
                                    del st.session_state['last_analysis']
                                    del st.session_state['last_file_id']
                                    st.rerun()
                                else: st.error("Veri işlendi ama dosya taşınamadı.")
                            else: st.error("Arşiv klasörü (Arsiv_Yatili/Gunduzlu) bulunamadı! Lütfen Drive'da oluşturun.")
                        else: st.error(f"Başarısız: {msg}")

    # --- TAB 4: AYARLAR ---
    with tab4:
        st.subheader("⚙️ Ayarlar")
        curr_p = get_current_unit_price()
        st.info(f"Birim Fiyat: {curr_p} ₺")
        with st.form("fiyat"):
            np = st.number_input("Yeni Fiyat", value=curr_p)
            yil = st.number_input("Yıl", value=2025)
            if st.form_submit_button("Güncelle"):
                update_unit_price(np, yil); st.rerun()
        
        st.divider()
        st.write("Gündüzlü Tahakkuk")
        aylar = ["2025-Ekim", "2025-Kasım", "2025-Aralık"]
        col1, col2 = st.columns(2)
        sec_ay = col1.selectbox("Ay", aylar)
        gun = col2.number_input("Gün", 20)
        tutar = gun * curr_p
        st.write(f"Tahmini Ciro: {tutar} x Öğrenci Sayısı")
        if st.button("Tahakkuk Başlat"):
            n = generate_monthly_accrual(sec_ay, gun, curr_p)
            if n > 0: st.success(f"{n} kayıt eklendi."); st.rerun()

        st.divider()
        st.write("Yatılı Taksit Sıfırlama")
        with st.form("taksit"):
            toplam = st.number_input("Yıllık Ücret", 20000.0)
            if st.form_submit_button("Dağıt"):
                ok, m = distribute_yatili_installments(toplam, yil)
                if ok: st.success(m); st.rerun()
                else: st.error(m)
