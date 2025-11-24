import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import difflib
import pandas as pd
import random

st.set_page_config(page_title="Mutfak ERP V15", page_icon="🏫", layout="wide")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR"
MENU_POOL_SHEET_NAME = "YEMEK_HAVUZU"

# --- GOOGLE SHEETS BAĞLANTISI ---
def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client, creds_dict.get("client_email")
    except Exception as e:
        return None, str(e)

# --- YARDIMCI FONKSİYONLAR ---
# (Eski yardımcı fonksiyonlar buraya gelecek - yer kaplamaması için kısalttım)
# standardize_name, clean_number, turkish_lower, find_best_match vb. 
# Lütfen önceki kodlardaki bu fonksiyonları buraya dahil et.
# ... [BURAYA ÖNCEKİ YARDIMCI FONKSİYONLARI KOYUN] ...
# Pratiklik adına kritik olanları tekrar yazıyorum:

def clean_number(num_str):
    try:
        clean = ''.join(c for c in num_str if c.isdigit() or c in [',', '.'])
        clean = clean.replace(',', '.')
        return float(clean)
    except: return 0.0

def turkish_lower(text):
    if not text: return ""
    return text.replace('İ', 'i').replace('I', 'ı').lower().strip()

# --- DATA ÇEKME ---
def get_full_menu_pool(client):
    """ 
    Tüm detaylarıyla (Grup, Limit, Ara, Zorunlu Eş, Protein, Ekipman) havuzu çeker.
    Dönüş: List of Dicts
    """
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        data = ws.get_all_values()
        header = [h.strip().upper() for h in data[0]]
        
        pool = []
        for row in data[1:]:
            item = {}
            # Satırı header uzunluğuna tamamla
            while len(row) < len(header): row.append("")
            
            for i, col_name in enumerate(header):
                item[col_name] = row[i].strip()
            
            # Sayısal değerleri düzelt
            try: item['LIMIT'] = int(item['LIMIT']) if item['LIMIT'] else 99
            except: item['LIMIT'] = 99
            try: item['ARA'] = int(item['ARA']) if item['ARA'] else 0
            except: item['ARA'] = 0
            
            pool.append(item)
        return pool
    except Exception as e: return []

# --- ALGORİTMA: AKILLI MENÜ MOTORU ---
def generate_smart_menu(month_index, year, pool, holidays, ready_snack_days):
    """
    Python tabanlı, kural bazlı menü oluşturucu.
    """
    # 1. Ayın günlerini oluştur
    start_date = datetime(year, month_index, 1)
    # Son günü bul
    if month_index == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month_index + 1, 1)
    num_days = (next_month - start_date).days
    
    menu_log = []
    usage_history = {} # { "Yemek Adı": [Gün1, Gün5] }
    
    # Kategorilere ayır
    cats = {}
    for p in pool:
        c = p.get('KATEGORİ', '').upper()
        if c not in cats: cats[c] = []
        cats[c].append(p)
        
    # Hata önleyici: Kategori yoksa boş liste
    def get_candidates(category): return cats.get(category, [])

    # --- GÜN DÖNGÜSÜ ---
    for day in range(1, num_days + 1):
        current_date = datetime(year, month_index, day)
        weekday = current_date.weekday() # 0=Pzt, 5=Cmt, 6=Paz
        date_str = current_date.strftime("%d.%m.%Y")
        
        # 1. TATİL KONTROLÜ
        is_holiday = False
        for h_start, h_end in holidays:
            if h_start <= current_date.date() <= h_end:
                is_holiday = True
                break
        
        if is_holiday:
            menu_log.append({
                "GÜN": date_str, "KAHVALTI": "---", "ÇORBA": "TATİL", 
                "ÖĞLE ANA": "MUTFAK", "YAN": "KAPALI", "AKŞAM ANA": "---", "ARA": "---"
            })
            continue

        # 2. HAFTA SONU KONTROLÜ (Tek Menü)
        is_weekend = (weekday >= 5) # Cmt veya Paz
        
        # --- SEÇİM FONKSİYONU ---
        def pick_dish(category, constraints={}):
            candidates = get_candidates(category)
            valid_options = []
            
            for dish in candidates:
                name = dish['YEMEK ADI']
                
                # Kural 1: Limit Kontrolü
                used_dates = usage_history.get(name, [])
                if len(used_dates) >= dish['LIMIT']: continue
                
                # Kural 2: Soğuma (Ara) Kontrolü
                if used_dates:
                    last_used = used_dates[-1]
                    days_passed = day - last_used
                    if days_passed <= dish['ARA']: continue
                
                # Kural 3: Ekipman Kısıtı (Fırın Doluysa)
                if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']:
                    continue
                    
                # Kural 4: Protein Dengesi (Öğle Kırmızıysa Akşam Beyaz)
                if constraints.get('block_protein') and dish.get('PROTEIN_TURU') == constraints['block_protein']:
                    continue
                
                # Kural 5: Ara Öğün Hazır Kısıtı
                if constraints.get('force_ready') and dish.get('PISIRME_EKIPMAN') != 'HAZIR':
                    continue
                    
                valid_options.append(dish)
            
            if not valid_options: return {"YEMEK ADI": "SEÇENEK KALMADI"}
            
            chosen = random.choice(valid_options)
            
            # Kullanımı Kaydet
            name = chosen['YEMEK ADI']
            if name not in usage_history: usage_history[name] = []
            usage_history[name].append(day)
            
            return chosen

        # --- GÜNLÜK MENÜ OLUŞTUR ---
        
        # Kahvaltı Ekstra
        kahvalti = pick_dish("KAHVALTI EKSTRA")
        
        # Öğle Yemeği
        corba = pick_dish("ÇORBA")
        ogle_ana = pick_dish("ANA YEMEK") # Kategori adın tam böyle olmalı
        
        # Yan Yemek (Zorunlu Eş Kontrolü)
        if ogle_ana.get('ZORUNLU_ES'):
            yan = {"YEMEK ADI": ogle_ana['ZORUNLU_ES']} # Direkt ata
        else:
            # Yan yemek için de protein/ekipman bakılabilir ama şimdilik basit tutalım
            yan = pick_dish("YAN YEMEK")
            
        # Akşam Yemeği
        if is_weekend:
            # Hafta sonu kuralı: Öğlenin aynısı
            aksam_ana = ogle_ana 
        else:
            # Hafta içi: Öğleden farklı, dengeli seçim
            constraints = {}
            # Eğer öğlen Fırın kullandıysa, akşam kullanmasın
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN' or yan.get('PISIRME_EKIPMAN') == 'FIRIN':
                constraints['block_equipment'] = 'FIRIN'
            
            # Protein Dengesi (Kırmızı -> Beyaz)
            p_type = ogle_ana.get('PROTEIN_TURU')
            if p_type == 'KIRMIZI': constraints['block_protein'] = 'KIRMIZI'
            elif p_type == 'BEYAZ': constraints['block_protein'] = 'BEYAZ'
            
            aksam_ana = pick_dish("ANA YEMEK", constraints)
            
            # Eğer akşam ana yemeğinin zorunlu eşi varsa ve öğle yan yemeği ile uyuşmuyorsa
            # Bu karmaşık bir durum, şimdilik "Öğle yan yemeği akşam da verilir" kuralını eziyoruz
            # Eğer akşamın zorunlusu varsa yanına onu koyacağız, yoksa öğleninkini yiyecekler.
            if aksam_ana.get('ZORUNLU_ES'):
                 # Burada bir karar: Akşam yan değişsin mi? 
                 # Senin kuralın: "Yan yemekler aynı kalsın". 
                 # Ama İskender çıktıysa yanına pilav gitmez, yoğurt lazım.
                 # Şimdilik senin kuralın baskın: YAN YEMEK DEĞİŞMEZ.
                 pass 

        # Ara Öğün
        is_ready_snack_day = (current_date.strftime("%A") in ready_snack_days) # Gün adı kontrolü (İngilizce döner dikkat)
        # Gün adlarını Türkçe/İngilizce eşleştirmemiz lazım, basit yapalım:
        # Hafta sonu mu? Veya seçili gün mü?
        
        snack_constraints = {}
        # Eğer bugün "Hazır Snack" günüyse
        if weekday in ready_snack_days: # 0=Pzt ... 6=Paz
             snack_constraints['force_ready'] = True
        
        # Fırın doluysa fırın keki çıkmasın
        if (ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN') or (not is_weekend and aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN'):
             snack_constraints['block_equipment'] = 'FIRIN'
             
        ara = pick_dish("ARA ÖĞÜN", snack_constraints)

        menu_log.append({
            "GÜN": date_str,
            "KAHVALTI": kahvalti['YEMEK ADI'],
            "ÇORBA": corba['YEMEK ADI'],
            "ÖĞLE ANA": ogle_ana['YEMEK ADI'],
            "YAN": yan['YEMEK ADI'],
            "AKŞAM ANA": aksam_ana['YEMEK ADI'],
            "ARA": ara['YEMEK ADI']
        })
        
    return pd.DataFrame(menu_log)

# ==========================================
# UI NAVIGASYON
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP V15")
        page = st.radio("Menü", ["📝 Günlük İrsaliye", "🧾 Fatura & Fiyatlar", "📅 Menü Planlayıcı"])

    # ... (İrsaliye ve Fatura Modülleri V11 ile Aynı, Buraya Kopyalamadım Yer Kaplamasın Diye) ...
    # Sen ana kodda burayı V11'deki gibi doldurursun.
    # Biz sadece yeni Menü Modülünü yazalım:

    if page == "📅 Menü Planlayıcı":
        st.header("👨‍🍳 Şefin Akıllı Defteri")
        
        # --- AYARLAR PANELİ ---
        col1, col2 = st.columns(2)
        with col1:
            aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 
                     7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
            secilen_ay_index = st.selectbox("Ay", list(aylar.keys()), format_func=lambda x: aylar[x], index=datetime.now().month - 1)
            year = datetime.now().year
            
        with col2:
            # TATİL SEÇİCİ
            st.write("🏖️ **Tatil Günleri (Mutfak Kapalı)**")
            holiday_range = st.date_input("Tatil Aralığı Seç", [], min_value=datetime(year, 1, 1), max_value=datetime(year, 12, 31))
            # date_input bir liste döner (başlangıç, bitiş). Bazen tek seçilirse tek döner.
            holidays = []
            if len(holiday_range) == 2:
                holidays.append((holiday_range[0], holiday_range[1]))
                st.caption(f"{holiday_range[0]} - {holiday_range[1]} arası kapalı.")
        
        st.divider()
        
        # HAZIR ARA ÖĞÜN GÜNLERİ
        st.write("🍪 **Hazır Ara Öğün (Paket) Günleri**")
        days_map = {0:"Pazartesi", 1:"Salı", 2:"Çarşamba", 3:"Perşembe", 4:"Cuma", 5:"Cumartesi", 6:"Pazar"}
        # Varsayılan olarak Cmt, Paz seçili olsun
        selected_snack_days = st.multiselect("Hangi günler hazır ürün verilsin?", list(days_map.keys()), format_func=lambda x: days_map[x], default=[5, 6])
        
        if st.button("🚀 Algoritmayı Çalıştır ve Menüyü Kur", type="primary"):
            client, _ = get_gspread_client()
            if client:
                pool = get_full_menu_pool(client)
                if pool:
                    with st.spinner("Kurallar işleniyor: Tek Fırın, Protein Dengesi, Hafta Sonu Tek Kazan..."):
                        df_menu = generate_smart_menu(secilen_ay_index, year, pool, holidays, selected_snack_days)
                        st.session_state['menu_df'] = df_menu
                        st.balloons()
                else:
                    st.error("YEMEK_HAVUZU sekmesi okunamadı! Sütun isimlerini kontrol et.")
            else:
                st.error("Bağlantı yok.")
                
        if 'menu_df' in st.session_state:
            st.success("Menü Hazır! Müdahale etmek istersen tablodan değiştirebilirsin.")
            
            # EDİTÖR
            edited_df = st.data_editor(st.session_state['menu_df'], num_rows="fixed", use_container_width=True, height=600)
            
            # İNDİR
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                edited_df.to_excel(writer, sheet_name='Menu', index=False)
                
                # Excel Formatlama (Sütun genişlikleri vs. opsiyonel ama şık olur)
                workbook = writer.book
                worksheet = writer.sheets['Menu']
                format1 = workbook.add_format({'num_format': '@'})
                worksheet.set_column('A:G', 20, format1)
                
            st.download_button("📥 Excel İndir (Aşçı İçin)", output.getvalue(), f"Menü_{aylar[secilen_ay_index]}.xlsx")

if __name__ == "__main__":
    main()
