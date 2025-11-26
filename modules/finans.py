import streamlit as st
import pandas as pd
import google.generativeai as genai
import json
import difflib
import re 
import os
from modules.utils import (
    get_gspread_client, 
    get_drive_service, # Drive servisi geri geldi
    find_folder_id,    # Klasör bulucu geri geldi
    FILE_FINANS, 
    SHEET_YATILI, 
    SHEET_GUNDUZLU, 
    SHEET_FINANS_AYARLAR
)

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

# --- DRIVE İŞLEMLERİ (Sadece Finans Modülü İçin) ---
def download_file_from_drive(service, file_id):
    try:
        request = service.files().get_media(fileId=file_id)
        return request.execute()
    except Exception as e:
        st.error(f"Drive İndirme Hatası: {e}")
        return None

def sanitize_filename(name):
    # Dosya ismindeki geçersiz karakterleri temizler
    safe = str(name).replace("/", "-").replace(":", "-")
    safe = re.sub(r'[^\w\s.-]', '', safe)
    return safe.strip()

def move_and_rename_file_in_drive(service, file_id, source_folder_id, destination_folder_id, new_name=None):
    try:
        file_metadata = {}
        if new_name:
            file_metadata['name'] = sanitize_filename(new_name)
            
        # Dosyayı taşı ve ismini güncelle
        service.files().update(
            fileId=file_id,
            addParents=destination_folder_id, 
            removeParents=source_folder_id,
            body=file_metadata,
            fields='id, parents, name'
        ).execute()
        return True
    except Exception as e:
        st.error(f"Dosya taşıma hatası: {e}")
        return False

# --- DATABASE İŞLEMLERİ ---
def get_data(sheet_name):
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS) 
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except: return pd.DataFrame()

def get_current_unit_price():
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS)
        ws = sh.worksheet(SHEET_FINANS_AYARLAR)
        all_rows = ws.get_all_values()
        if len(all_rows) > 0:
            last_row = all_rows[-1] 
            s_price = str(last_row[1]).replace("₺", "").replace("TL", "").strip()
            if "," in s_price and "." not in s_price: s_price = s_price.replace(",", ".")
            elif "," in s_price and "." in s_price: s_price = s_price.replace(".", "").replace(",", ".")
            return float(s_price)
        return 0.0
    except: return 0.0

def update_unit_price(new_price, year):
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS)
        ws = sh.worksheet(SHEET_FINANS_AYARLAR)
        ws.append_row([year, f"{new_price:.2f}".replace('.', ','), ''], value_input_option='USER_ENTERED') 
        return True
    except: return False

def distribute_yatili_installments(total_fee, year):
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS)
        ws = sh.worksheet(SHEET_YATILI) 
        all_values = ws.get_all_values()
        if not all_values: return False, "Sayfa boş."

        student_names = []
        start_index = 1 if all_values and ("ad" in str(all_values[0][0]).lower()) else 0
        for row in all_values[start_index:]:
            if row and row[0].strip(): student_names.append(row[0].strip())
        
        if not student_names: return False, "Öğrenci bulunamadı."
        inst_amt = total_fee / 4.0
        new_data = [["Ad_Soyad", "Sinif", "Toplam_Yillik_Ucret", "Odenen_Toplam", "Kalan_Borc", "Taksit1_Tutar", "Taksit2_Tutar", "Taksit3_Tutar", "Taksit4_Tutar"]]
        for name in student_names:
            new_data.append([name, "", total_fee, 0, total_fee, inst_amt, inst_amt, inst_amt, inst_amt])
        ws.clear(); ws.update(values=new_data, range_name="A1")
        
        # Ayarlar sayfasına da yıllık toplamı işle
        ws_set = sh.worksheet(SHEET_FINANS_AYARLAR)
        ws_set.append_row([year, '', total_fee], value_input_option='USER_ENTERED')
        
        return True, f"{len(student_names)} öğrenci güncellendi."
    except Exception as e: return False, str(e)

# --- İŞ MANTIĞI ---
def find_best_match(name, name_list):
    matches = difflib.get_close_matches(name, name_list, n=1, cutoff=0.6)
    return matches[0] if matches else None

def process_yatili_payment(analiz, dekont_link):
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS)
        ws = sh.worksheet(SHEET_YATILI)
        df = pd.DataFrame(ws.get_all_records())
        
        aranan = analiz.get('ogrenci_ad', '')
        bulunan = find_best_match(aranan, df['Ad_Soyad'].tolist())
        if not bulunan: return False, f"'{aranan}' bulunamadı.", 0
            
        row_idx = df[df['Ad_Soyad'] == bulunan].index[0]
        sh_row = row_idx + 2 
        
        cur_paid = float(str(df.at[row_idx, 'Odenen_Toplam']).replace(',', '') or 0)
        tot_fee = float(str(df.at[row_idx, 'Toplam_Yillik_Ucret']).replace(',', '') or 0)
        amt = float(analiz.get('tutar', 0))
        
        new_paid = cur_paid + amt
        new_rem = tot_fee - new_paid
        
        ws.update_cell(sh_row, df.columns.get_loc('Odenen_Toplam')+1, new_paid)
        ws.update_cell(sh_row, df.columns.get_loc('Kalan_Borc')+1, new_rem)
        
        taksit_tutari = tot_fee / 4.0 if tot_fee > 0 else 1
        taksit_no = int(cur_paid / taksit_tutari) + 1
        return True, f"{bulunan}: {amt} TL işlendi.", taksit_no
    except Exception as e: return False, f"Hata: {e}", 0

def write_to_gunduzlu_sheet(analiz, dekont_link):
    try:
        client = get_gspread_client()
        sh = client.open(FILE_FINANS)
        ws = sh.worksheet(SHEET_GUNDUZLU)
        new_row = [analiz.get('ogrenci_tc', ''), analiz.get('ogrenci_ad', 'Bilinmiyor'), '', analiz.get('tarih', ''), '', '', analiz.get('tutar', 0), 'Ödendi', dekont_link]
        ws.append_row(new_row, value_input_option='USER_ENTERED')
        return True
    except: return False

def analyze_receipt_with_gemini(file_data, mime_type, model_name):
    model = genai.GenerativeModel(model_name)
    prompt = """Sen muhasebe asistanısın. Banka dekontunu oku. JSON ver: { "tarih": "YYYY-MM-DD", "gonderen_ad_soyad": "", "tutar": 0.0, "aciklama": "", "ogrenci_tc": "", "ogrenci_ad": "", "tur_tahmini": "'YEMEK' veya 'TAKSİT'" }"""
    try:
        response = model.generate_content([prompt, {"mime_type": mime_type, "data": file_data}])
        return json.loads(response.text.strip().replace("```json", "").replace("```", ""))
    except: return None

# --- ARAYÜZ ---
def render_page(selected_model):
    st.header("💰 Finans Yönetimi")
    tab1, tab2, tab3, tab4 = st.tabs(["🏫 Yatılı", "🍽️ Gündüzlü", "🤖 Dekont İşle (Drive)", "⚙️ Ayarlar"])

    with tab1:
        df = get_data(SHEET_YATILI)
        if not df.empty: st.dataframe(df, use_container_width=True)
        else: st.warning("Veri yok.")

    with tab2:
        df = get_data(SHEET_GUNDUZLU)
        if not df.empty: st.dataframe(df, use_container_width=True)
        else: st.warning("Veri yok.")

    # --- DRIVE DEKONT İŞLEME MODÜLÜ ---
    with tab3:
        st.subheader("🤖 Drive Dekont Analizi")
        service = get_drive_service()
        
        if not service:
            st.error("Drive bağlantısı kurulamadı. secrets ayarlarını kontrol et.")
            st.stop()
            
        # Drive Klasörlerini Bul
        # Dikkat: Bu isimlerin Drive'ında birebir aynı olması lazım.
        root_id = find_folder_id(service, "Mutfak_ERP_Drive")
        finans_id = find_folder_id(service, "Finans", parent_id=root_id)
        gelen_id = find_folder_id(service, "Gelen_Dekontlar", parent_id=finans_id)
        arsiv_yatili_id = find_folder_id(service, "Arsiv_Yatili", parent_id=finans_id)
        arsiv_gunduzlu_id = find_folder_id(service, "Arsiv_Gunduzlu", parent_id=finans_id)
        
        if not gelen_id:
            st.warning("⚠️ 'Gelen_Dekontlar' klasörü bulunamadı. Lütfen 'Mutfak_ERP_Drive/Finans/Gelen_Dekontlar' yolunu kontrol et.")
        else:
            # Dosyaları Listele
            results = service.files().list(q=f"'{gelen_id}' in parents and trashed=false", fields="files(id, name, mimeType)").execute()
            files = results.get('files', [])
            
            st.info(f"📂 İşlenmeyi Bekleyen: **{len(files)}** Dekont")
            
            if files:
                # Dosya Seçimi
                file_map = {f['id']: f['name'] for f in files}
                sel_id = st.selectbox("İşlenecek Dekontu Seç:", list(file_map.keys()), format_func=lambda x: file_map[x])
                sel_meta = next(f for f in files if f['id'] == sel_id)
                
                if st.button("🚀 Dekontu Analiz Et"):
                    with st.spinner("Drive'dan indiriliyor ve analiz ediliyor..."):
                        file_data = download_file_from_drive(service, sel_id)
                        res = analyze_receipt_with_gemini(file_data, sel_meta['mimeType'], selected_model)
                        if res:
                            st.session_state['last_analysis'] = res
                            st.session_state['last_file_id'] = sel_id
                            st.success("Analiz Başarılı! Aşağıdan kontrol et 👇")
                        else: st.error("Analiz başarısız oldu.")
                
                # --- MANUEL DÜZELTME VE KAYIT EKRANI ---
                if st.session_state.get('last_analysis') and st.session_state.get('last_file_id') == sel_id:
                    analiz = st.session_state['last_analysis']
                    st.divider()
                    st.subheader("✍️ Sonucu Doğrula ve İşle")
                    
                    with st.form("dekont_onay"):
                        c1, c2 = st.columns(2)
                        y_ad = c1.text_input("Öğrenci Adı Soyadı", value=analiz.get('ogrenci_ad', ''))
                        y_tc = c2.text_input("TC No", value=analiz.get('ogrenci_tc', ''))
                        
                        c3, c4 = st.columns(2)
                        y_tut = c3.number_input("Tutar (TL)", value=float(analiz.get('tutar', 0)))
                        tur_idx = 1 if analiz.get('tur_tahmini') == 'TAKSİT' else 0
                        y_tur = c4.selectbox("Ödeme Türü", ["YEMEK", "TAKSİT"], index=tur_idx)
                        
                        if st.form_submit_button("✅ Onayla, Kaydet ve Taşı"):
                            # Verileri Güncelle
                            analiz.update({'ogrenci_ad': y_ad, 'ogrenci_tc': y_tc, 'tutar': y_tut, 'tur_tahmini': y_tur})
                            
                            # Link Oluştur
                            link = f"https://drive.google.com/file/d/{sel_id}/view"
                            
                            basari = False
                            msg = ""
                            hedef_klasor = None
                            yeni_isim_kok = sanitize_filename(y_ad) if y_ad else "Bilinmiyor"
                            tarih = analiz.get('tarih', 'Tarihsiz')
                            ext = os.path.splitext(sel_meta['name'])[1]
                            
                            if y_tur == 'YEMEK':
                                if write_to_gunduzlu_sheet(analiz, link):
                                    basari = True
                                    msg = "Gündüzlü listesine işlendi."
                                    hedef_klasor = arsiv_gunduzlu_id
                                    yeni_isim = f"{yeni_isim_kok}_Yemek_{tarih}{ext}"
                                else: msg = "Veritabanı hatası."
                            else:
                                ok, txt, taksit_no = process_yatili_payment(analiz, link)
                                if ok:
                                    basari = True
                                    msg = txt
                                    hedef_klasor = arsiv_yatili_id
                                    yeni_isim = f"{yeni_isim_kok}_Taksit{taksit_no}{ext}"
                                else: msg = txt
                            
                            if basari and hedef_klasor:
                                # Dosyayı Taşı
                                if move_and_rename_file_in_drive(service, sel_id, gelen_id, hedef_klasor, yeni_isim):
                                    st.success(f"✅ {msg}")
                                    st.info(f"📂 Dosya **{yeni_isim}** olarak arşivlendi.")
                                    # Temizlik
                                    del st.session_state['last_analysis']
                                    del st.session_state['last_file_id']
                                    st.rerun()
                                else: st.error("Veri işlendi ama dosya taşınamadı.")
                            elif not basari: st.error(f"Başarısız: {msg}")

    with tab4:
        st.subheader("Ayarlar")
        curr = get_current_unit_price()
        st.write(f"Birim Fiyat: {curr} TL")
        if st.button("Fiyat Güncelle"): update_unit_price(st.number_input("Yeni Fiyat"), 2025)
        st.divider()
        with st.form("taksit"):
            if st.form_submit_button("Taksitleri Dağıt"):
                ok, m = distribute_yatili_installments(st.number_input("Yıllık", 20000.0), 2025)
                if ok: st.success(m)
                else: st.error(m)
