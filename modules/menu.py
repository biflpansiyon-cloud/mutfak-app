import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar
import gspread

# Utils'den dosya adını ve bağlantı fonksiyonunu çekiyoruz
from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            # Mutfak_Menu_Planlama
    MENU_POOL_SHEET_NAME  # YEMEK_HAVUZU
)

# --- AYARLAR ---
SABIT_KAHVALTI = "Peynir, Zeytin, Reçel, Bal, Tereyağı, Domates, Salatalık"
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU" # Oluşturulan menünün saklanacağı sayfa

# =========================================================
# 💾 VERİTABANI İŞLEMLERİ (KAYDETME & YÜKLEME)
# =========================================================

def save_menu_to_sheet(client, df):
    """Oluşturulan veya düzenlenen menüyü Sheets'e kaydeder."""
    try:
        sh = client.open(FILE_MENU)
        try: 
            ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        except: 
            # Sayfa yoksa oluştur
            ws = sh.add_worksheet(ACTIVE_MENU_SHEET_NAME, 100, 20)
            
        ws.clear() # Eski menüyü sil
        # DataFrame'i listeye çevirip yaz (Başlıklar dahil)
        ws.update([df.columns.values.tolist()] + df.astype(str).values.tolist())
        return True
    except Exception as e:
        st.error(f"Kaydetme Hatası: {e}")
        return False

def load_last_menu(client):
    """Varsa kayıtlı son menüyü getirir."""
    try:
        sh = client.open(FILE_MENU)
        ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        data = ws.get_all_records()
        if data:
            return pd.DataFrame(data)
        return None
    except:
        return None # Sayfa yoksa veya boşsa None döner

def get_full_menu_pool(client):
    """Google Sheets'ten yemek havuzunu çeker."""
    try:
        sh = client.open(FILE_MENU)
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        data = ws.get_all_values()
        
        if not data: return []
        
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
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 🍳 MENÜ ALGORİTMASI
# =========================================================

def select_dish(pool, category, usage_history, current_day_obj, constraints=None):
    if constraints is None: constraints = {}
    candidates = [d for d in pool if d.get('KATEGORİ') == category]
    
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if d.get('PROTEIN_TURU') != 'BALIK']
    
    valid_options = []
    for dish in candidates:
        name = dish['YEMEK ADI']
        count_used = len(usage_history.get(name, []))
        if count_used >= dish['LIMIT']: continue
        if count_used > 0:
            last_seen_day = usage_history[name][-1]
            if (current_day_obj.day - last_seen_day) <= dish['ARA']: continue
        if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']: continue
        if constraints.get('force_protein_types') and dish.get('PROTEIN_TURU') not in constraints['force_protein_types']: continue
        if constraints.get('exclude_names') and name in constraints['exclude_names']: continue
        valid_options.append(dish)
    
    if not valid_options:
        if candidates: return random.choice(candidates)
        return {"YEMEK ADI": f"---", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": ""}
    
    chosen = random.choice(valid_options)
    if chosen['YEMEK ADI'] not in usage_history: usage_history[chosen['YEMEK ADI']] = []
    usage_history[chosen['YEMEK ADI']].append(current_day_obj.day)
    return chosen

def generate_smart_menu(month, year, pool, holidays):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    
    weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
    fish_day = random.choice(weekdays) if weekdays else None
    
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%d.%m.%Y")
        weekday_name = current_date.strftime("%A")
        
        is_holiday = False
        for h_start, h_end in holidays:
            if h_start <= current_date.date() <= h_end: is_holiday = True; break
        
        if is_holiday:
            menu_log.append({"TARİH": date_str, "GÜN": "TATİL", "KAHVALTI": "---", "ÖĞLE ÇORBA": "---", "ÖĞLE ANA": "---", "ÖĞLE YAN": "---", "ÖĞLE TAMM": "---", "AKŞAM ÇORBA": "---", "AKŞAM ANA": "---", "AKŞAM YAN": "---", "AKŞAM TAMM": "---", "GECE": "---"})
            continue

        kahvalti_ekstra = select_dish(pool, "KAHVALTI EKSTRA", usage_history, current_date)
        kahvalti_full = f"{SABIT_KAHVALTI} + {kahvalti_ekstra['YEMEK ADI']}"
        
        is_today_fish = (day == fish_day)
        if is_today_fish:
            ogle_corba = {"YEMEK ADI": "Mercimek Çorbası", "PISIRME_EKIPMAN": "TENCERE"}
            fish_candidates = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            ogle_ana = random.choice(fish_candidates) if fish_candidates else {"YEMEK ADI": "BALIK BULUNAMADI", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": "BALIK"}
            if ogle_ana['YEMEK ADI'] not in usage_history: usage_history[ogle_ana['YEMEK ADI']] = []
            usage_history[ogle_ana['YEMEK ADI']].append(day)
            ogle_yan = {"YEMEK ADI": "Salata", "PISIRME_EKIPMAN": "HAZIR"}
            ogle_tamm = {"YEMEK ADI": "Tahin Helvası", "PISIRME_EKIPMAN": "HAZIR"}
        else:
            ogle_corba = select_dish(pool, "ÇORBA", usage_history, current_date)
            ogle_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date)
            side_constraints = {}
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': side_constraints['block_equipment'] = 'FIRIN'
            if ogle_ana.get('ZORUNLU_ES'): ogle_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_ES'], "PISIRME_EKIPMAN": "TENCERE"}
            else: ogle_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, side_constraints)
            ogle_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date)

        aksam_corba = select_dish(pool, "ÇORBA", usage_history, current_date, constraints={"exclude_names": [ogle_corba['YEMEK ADI']]})
        dinner_main_constraints = {"exclude_names": [ogle_ana['YEMEK ADI']]}
        if ogle_ana.get('PROTEIN_TURU') == 'ETSIZ': dinner_main_constraints['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
        aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_main_constraints)
        aksam_side_constraints = {}
        if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': aksam_side_constraints['block_equipment'] = 'FIRIN'
        if aksam_ana.get('ZORUNLU_ES'): aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_ES']}
        else: aksam_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, aksam_side_constraints)
        aksam_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date)
        gece = select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, current_date)

        menu_log.append({
            "TARİH": date_str, "GÜN": weekday_name, "KAHVALTI": kahvalti_full,
            "ÖĞLE ÇORBA": ogle_corba['YEMEK ADI'], "ÖĞLE ANA": ogle_ana['YEMEK ADI'], "ÖĞLE YAN": ogle_yan['YEMEK ADI'], "ÖĞLE TAMM": ogle_tamm['YEMEK ADI'],
            "AKŞAM ÇORBA": aksam_corba['YEMEK ADI'], "AKŞAM ANA": aksam_ana['YEMEK ADI'], "AKŞAM YAN": aksam_yan['YEMEK ADI'], "AKŞAM TAMM": aksam_tamm['YEMEK ADI'],
            "GECE": f"Çay/Kahve + {gece['YEMEK ADI']}"
        })

    return pd.DataFrame(menu_log)

# =========================================================
# 🖥️ ARAYÜZ (RENDER)
# =========================================================

def render_page(sel_model):
    st.header("👨‍🍳 Akıllı Menü Planlayıcı")
    st.markdown("---")
    
    # 1. BAĞLANTIYI KUR
    client = get_gspread_client()
    if not client:
        st.error("Bağlantı hatası!")
        st.stop()

    # 2. VARSA ESKİ MENÜYÜ YÜKLE (Sayfa ilk açıldığında)
    if 'generated_menu' not in st.session_state:
        with st.spinner("Kayıtlı menü kontrol ediliyor..."):
            saved_df = load_last_menu(client)
            if saved_df is not None and not saved_df.empty:
                st.session_state['generated_menu'] = saved_df
                st.info("📂 En son kaydedilen menü yüklendi.")

    # 3. YENİ MENÜ OLUŞTURMA FORMU
    col1, col2 = st.columns(2)
    with col1:
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        current_month = datetime.now().month
        sel_month_idx = st.selectbox("Ay Seçin", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=current_month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)

    with col2:
        st.info("🏖️ Tatil Aralığı")
        holiday_start = st.date_input("Başlangıç", value=None)
        holiday_end = st.date_input("Bitiş", value=None)
        
    if st.button("🚀 Yeni Menü Oluştur (Eskisini Siler)", type="primary"):
        with st.spinner("Algoritma çalışıyor..."):
            pool = get_full_menu_pool(client)
            if pool:
                holidays = []
                if holiday_start and holiday_end: holidays.append((holiday_start, holiday_end))
                
                df_menu = generate_smart_menu(sel_month_idx, sel_year, pool, holidays)
                
                # OLUŞUR OLUŞMAZ KAYDET
                if save_menu_to_sheet(client, df_menu):
                    st.session_state['generated_menu'] = df_menu
                    st.success("Yeni menü oluşturuldu ve buluta kaydedildi! ✅")
                    st.rerun() # Sayfayı yenile ki tablo güncellensin
                else:
                    st.error("Menü oluştu ama kaydedilemedi.")

    st.divider()

    # 4. MENÜYÜ GÖSTER VE DÜZENLE
    if 'generated_menu' in st.session_state:
        st.subheader(f"📅 Aktif Menü")
        
        # Excel İndir
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['generated_menu'].to_excel(writer, index=False, sheet_name='Menu')
        
        c1, c2 = st.columns([1, 4])
        with c1:
            st.download_button(
                label="📥 Excel İndir",
                data=output.getvalue(),
                file_name=f"Menu_{tr_aylar[sel_month_idx]}_{sel_year}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        # Tablo Editörü
        edited_menu = st.data_editor(
            st.session_state['generated_menu'],
            num_rows="fixed",
            use_container_width=True,
            height=600,
            key="menu_editor"
        )
        
        # Değişiklikleri Kaydet Butonu
        if st.button("💾 Yaptığım Değişiklikleri Buluta Kaydet"):
            with st.spinner("Kaydediliyor..."):
                if save_menu_to_sheet(client, edited_menu):
                    st.session_state['generated_menu'] = edited_menu # Session'ı güncelle
                    st.success("✅ Değişiklikler başarıyla kaydedildi! Sayfayı yenilesen de gitmez.")
                else:
                    st.error("Kaydedilemedi.")
