import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import random
import io
import calendar

# Utils'den gerekli bağlantıları çekiyoruz
from modules.utils import (
    get_gspread_client, 
    FILE_MENU, 
    MENU_POOL_SHEET_NAME
)

# --- SABİT LİSTELER (Kodun içine gömülü) ---
SABIT_KAHVALTI = "Peynir, Zeytin, Reçel, Bal, Tereyağı, Domates, Salatalık"

def get_full_menu_pool(client):
    """
    Google Sheets'ten yemek havuzunu çeker.
    Hata durumunda secrets dosyasındaki maili ifşa eder.
    """
    # 1. Dosya URL'si (Ekran görüntüsünden aldığımız)
    sheet_url = "https://docs.google.com/spreadsheets/d/1FyxQ6Vue3sp16uxD8r-1hBiICED5dkpgXQlEM_q1rll/edit"
    
    # 2. Mail Adresini Direkt Secrets Dosyasından Okuyalım (Kaçarı yok)
    try:
        # Secrets yapısına göre değişebilir ama genelde bu şekildedir
        creds_dict = dict(st.secrets["gcp_service_account"])
        robot_email = creds_dict.get("client_email", "Mail adresi secrets içinde bulunamadı!")
    except:
        robot_email = "Secrets dosyası okunamadı!"

    try:
        # Bağlanmayı dene
        sh = client.open_by_url(sheet_url)
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        
        data = ws.get_all_values()
        
        if not data: 
            st.error("Dosyaya bağlandım ama içi boş!")
            return []
        
        # --- Veri İşleme Kısmı ---
        header = [h.strip().upper() for h in data[0]]
        pool = []
        for row in data[1:]:
            item = {}
            while len(row) < len(header): row.append("")
            for i, col_name in enumerate(header): item[col_name] = row[i].strip()
            try: item['LIMIT'] = int(item['LIMIT']) if item.get('LIMIT') else 99
            except: item['LIMIT'] = 99
            try: item['ARA'] = int(item['ARA']) if item.get('ARA') else 0
            except: item['ARA'] = 0
            pool.append(item)
            
        return pool
        
    except Exception as e:
        # HATA EKRANI
        st.error("🚨 ERİŞİM HATASI DETAYLARI")
        st.write("Kodun şu an kullanmaya çalıştığı Robot Maili:")
        st.code(robot_email, language="text") # Maili kopyalanabilir şekilde gösterir
        
        st.warning("Lütfen yukarıdaki mail adresini kopyalayıp Google Sheets'teki 'Paylaş' kısmıyla harfiyen kıyasla.")
        st.error(f"Teknik Hata: {e}")
        return []


def select_dish(pool, category, usage_history, current_day_obj, constraints=None):
    """
    Verilen kategori ve kısıtlamalara göre havuzdan BİR yemek seçer.
    """
    if constraints is None: constraints = {}
    
    # 1. KATEGORİ FİLTRESİ
    candidates = [d for d in pool if d.get('KATEGORİ') == category]
    
    # 2. BALIK FİLTRESİ (Normal seçimlerde BALIK gelmesin)
    # Eğer özel olarak 'force_fish' denmediyse, balıkları ele.
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if d.get('PROTEIN_TURU') != 'BALIK']
    
    # 3. KISITLAMALAR
    valid_options = []
    for dish in candidates:
        name = dish['YEMEK ADI']
        
        # A) LIMIT Kontrolü (Ayda kaç kez?)
        count_used = len(usage_history.get(name, []))
        if count_used >= dish['LIMIT']: 
            continue
            
        # B) ARA Kontrolü (Üst üste gelmesin)
        if count_used > 0:
            last_seen_day = usage_history[name][-1]
            days_passed = current_day_obj.day - last_seen_day
            if days_passed <= dish['ARA']: 
                continue
                
        # C) EKİPMAN Kontrolü (İki fırın yemeği olmasın)
        if constraints.get('block_equipment'):
            if dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']:
                continue
                
        # D) PROTEIN Kontrolü (Öğlen Etsiz yendiyse Akşam Etli olsun)
        if constraints.get('force_protein_types'):
            p_type = dish.get('PROTEIN_TURU', '')
            if p_type not in constraints['force_protein_types']:
                continue
        
        # E) ZORUNLU HARİÇ TUTMA (Exclude)
        if constraints.get('exclude_names'):
            if name in constraints['exclude_names']:
                continue

        valid_options.append(dish)
    
    # Eğer uygun yemek kalmadıysa, kuralları esnetip tekrar deneriz (Fallback)
    if not valid_options:
        # En azından kategorisi tutan herhangi birini getir (Sonsuz döngü olmasın)
        if candidates: return random.choice(candidates)
        return {"YEMEK ADI": f"YOK: {category}", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": ""}
    
    # 4. SEÇİM
    chosen = random.choice(valid_options)
    
    # Kullanım geçmişine işle
    if chosen['YEMEK ADI'] not in usage_history: usage_history[chosen['YEMEK ADI']] = []
    usage_history[chosen['YEMEK ADI']].append(current_day_obj.day)
    
    return chosen

def generate_smart_menu(month, year, pool, holidays):
    """
    Ana Algoritma: Ayın tüm günlerini planlar.
    """
    # Takvim Hazırlığı
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} # { "Kuru Fasulye": [1, 15], ... }
    
    # --- BALIK GÜNÜ BELİRLEME ---
    # Sadece hafta içi (0-4) olan günleri bul
    weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
    fish_day = random.choice(weekdays) if weekdays else None
    
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%d.%m.%Y")
        weekday_name = current_date.strftime("%A") # Gün adı (Locale ayarına göre değişir ama görsel için)
        
        # TATİL KONTROLÜ
        is_holiday = False
        for h_start, h_end in holidays:
            if h_start <= current_date.date() <= h_end: 
                is_holiday = True
                break
        
        if is_holiday:
            menu_log.append({
                "TARİH": date_str, "GÜN": "TATİL", 
                "KAHVALTI": "---", "ÖĞLE ÇORBA": "---", "ÖĞLE ANA": "---", "ÖĞLE YAN": "---", "ÖĞLE TAMM": "---",
                "AKŞAM ÇORBA": "---", "AKŞAM ANA": "---", "AKŞAM YAN": "---", "AKŞAM TAMM": "---",
                "GECE": "---"
            })
            continue

        # ==========================
        # 1. KAHVALTI (Sabit + Ekstra)
        # ==========================
        kahvalti_ekstra = select_dish(pool, "KAHVALTI EKSTRA", usage_history, current_date)
        kahvalti_full = f"{SABIT_KAHVALTI} + {kahvalti_ekstra['YEMEK ADI']}"
        
        # ==========================
        # 2. ÖĞLE YEMEĞİ
        # ==========================
        is_today_fish = (day == fish_day)
        
        if is_today_fish:
            # --- FIX BALIK MENÜSÜ ---
            ogle_corba = {"YEMEK ADI": "Mercimek Çorbası", "PISIRME_EKIPMAN": "TENCERE"}
            
            # Havuzdan 'BALIK' türündeki yemekleri bul ve birini seç
            fish_candidates = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            if fish_candidates:
                 ogle_ana = random.choice(fish_candidates)
                 # Kullanım geçmişine ekle
                 nm = ogle_ana['YEMEK ADI']
                 if nm not in usage_history: usage_history[nm] = []
                 usage_history[nm].append(day)
            else:
                 ogle_ana = {"YEMEK ADI": "BALIK (Havuzda Yok)", "PISIRME_EKIPMAN": "OCAK", "PROTEIN_TURU": "BALIK"}
            
            ogle_yan = {"YEMEK ADI": "Salata", "PISIRME_EKIPMAN": "HAZIR"}
            ogle_tamm = {"YEMEK ADI": "Tahin Helvası", "PISIRME_EKIPMAN": "HAZIR"}
            
        else:
            # --- NORMAL ÖĞLE MENÜSÜ ---
            ogle_corba = select_dish(pool, "ÇORBA", usage_history, current_date)
            ogle_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date)
            
            # Yan yemek kısıtlamaları (Ana yemek Fırın ise, Yan yemek Fırın olmasın)
            side_constraints = {}
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': 
                side_constraints['block_equipment'] = 'FIRIN'
            
            # Zorunlu Eşleşme (Kuru Fasulye -> Pilav)
            if ogle_ana.get('ZORUNLU_ES'):
                ogle_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_ES'], "PISIRME_EKIPMAN": "TENCERE"} # Basit varsayım
            else:
                ogle_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, side_constraints)
                
            ogle_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date)

        # ==========================
        # 3. AKŞAM YEMEĞİ
        # ==========================
        aksam_corba = select_dish(pool, "ÇORBA", usage_history, current_date, constraints={"exclude_names": [ogle_corba['YEMEK ADI']]})
        
        # Akşam Ana Yemek Kısıtlamaları
        dinner_main_constraints = {"exclude_names": [ogle_ana['YEMEK ADI']]}
        
        # Kural: Öğlen ETSIZ ise, Akşam ET olsun
        if ogle_ana.get('PROTEIN_TURU') == 'ETSIZ':
            dinner_main_constraints['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
        aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_main_constraints)
        
        # Akşam Yan Yemek
        aksam_side_constraints = {}
        if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': 
            aksam_side_constraints['block_equipment'] = 'FIRIN'
            
        if aksam_ana.get('ZORUNLU_ES'):
             aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_ES']}
        else:
             aksam_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, aksam_side_constraints)
             
        aksam_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date)

        # ==========================
        # 4. GECE (21:15)
        # ==========================
        gece = select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, current_date)

        # ==========================
        # KAYIT
        # ==========================
        row_data = {
            "TARİH": date_str,
            "GÜN": weekday_name,
            "KAHVALTI": kahvalti_full,
            "ÖĞLE ÇORBA": ogle_corba['YEMEK ADI'],
            "ÖĞLE ANA": ogle_ana['YEMEK ADI'],
            "ÖĞLE YAN": ogle_yan['YEMEK ADI'],
            "ÖĞLE TAMM": ogle_tamm['YEMEK ADI'],
            "AKŞAM ÇORBA": aksam_corba['YEMEK ADI'],
            "AKŞAM ANA": aksam_ana['YEMEK ADI'],
            "AKŞAM YAN": aksam_yan['YEMEK ADI'],
            "AKŞAM TAMM": aksam_tamm['YEMEK ADI'],
            "GECE": f"Çay/Kahve + {gece['YEMEK ADI']}"
        }
        menu_log.append(row_data)

    return pd.DataFrame(menu_log)

def render_page(sel_model):
    st.header("👨‍🍳 Akıllı Menü Planlayıcı")
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    with col1:
        # Ay Seçimi
        months = {i: datetime(2000, i, 1).strftime('%B') for i in range(1, 13)} # Basit ay isimleri (Türkçe locale yoksa İngilizce çıkabilir, idare eder)
        # Manuel Türkçe Ay Listesi
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        
        current_month = datetime.now().month
        sel_month_idx = st.selectbox("Ay Seçin", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=current_month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)

    with col2:
        st.info("🏖️ **Tatil Günleri:** Aşağıdan tarih aralığı seçerek o günleri boş geçebilirsiniz.")
        holiday_start = st.date_input("Tatil Başlangıç", value=None)
        holiday_end = st.date_input("Tatil Bitiş", value=None)
        
    if st.button("🚀 Menüyü Oluştur", type="primary"):
        client = get_gspread_client()
        if not client:
            st.error("Google Sheets Bağlantısı Yok!")
            st.stop()
            
        with st.spinner("Yemek havuzu taranıyor, kurallar işleniyor..."):
            pool = get_full_menu_pool(client)
            
            if not pool:
                st.error("Yemek havuzu boş! Lütfen 'Mutfak_Menu_Planlama' dosyasını kontrol et.")
            else:
                holidays = []
                if holiday_start and holiday_end:
                    holidays.append((holiday_start, holiday_end))
                
                df_menu = generate_smart_menu(sel_month_idx, sel_year, pool, holidays)
                st.session_state['generated_menu'] = df_menu
                st.success("Menü Hazır!")

    # --- SONUÇ EKRANI ---
    if 'generated_menu' in st.session_state:
        st.subheader(f"📅 {tr_aylar[sel_month_idx]} {sel_year} Menüsü")
        
        # Excel İndirme Butonu
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['generated_menu'].to_excel(writer, index=False, sheet_name='Menu')
        
        st.download_button(
            label="📥 Excel Olarak İndir",
            data=output.getvalue(),
            file_name=f"Menu_{tr_aylar[sel_month_idx]}_{sel_year}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # Düzenlenebilir Tablo
        edited_menu = st.data_editor(
            st.session_state['generated_menu'],
            num_rows="fixed",
            use_container_width=True,
            height=600
        )
