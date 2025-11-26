import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            
    MENU_POOL_SHEET_NAME  
)

# --- AYARLAR ---
SABIT_KAHVALTI = "Peynir, Zeytin, Reçel, Bal, Tereyağı, Domates, Salatalık"
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

# Yasaklı Eşleşmeler
YOGURTLU_CORBALAR = ["YAYLA", "YOĞURT", "DÜĞÜN", "ERİŞTE"] 
YOGURT_YAN_URUNLER = ["CACIK", "AYRAN", "YOĞURT", "HAYDARİ"] 

# =========================================================
# 💾 VERİTABANI İŞLEMLERİ
# =========================================================

def save_menu_to_sheet(client, df):
    try:
        sh = client.open(FILE_MENU)
        try: ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        except: ws = sh.add_worksheet(ACTIVE_MENU_SHEET_NAME, 100, 20)
        ws.clear()
        ws.update([df.columns.values.tolist()] + df.astype(str).values.tolist())
        return True
    except Exception as e:
        st.error(f"Kaydetme Hatası: {e}")
        return False

def load_last_menu(client):
    try:
        sh = client.open(FILE_MENU)
        ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        data = ws.get_all_records()
        if data: return pd.DataFrame(data)
        return None
    except: return None

def get_full_menu_pool(client):
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
# 🍳 MENÜ ALGORİTMASI (SEÇİCİ)
# =========================================================

def select_dish(pool, category, usage_history, current_day_obj, constraints=None):
    if constraints is None: constraints = {}
    candidates = [d for d in pool if d.get('KATEGORİ') == category]
    
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if d.get('PROTEIN_TURU') != 'BALIK']
    
    valid_options = []
    for dish in candidates:
        name = dish['YEMEK ADI']
        name_upper = name.upper()
        p_type = dish.get('PROTEIN_TURU', '').strip() # Protein türünü al
        
        # 1. LIMIT ve ARA
        count_used = len(usage_history.get(name, []))
        if count_used >= dish['LIMIT']: continue
        if count_used > 0:
            last_seen_day = usage_history[name][-1]
            if (current_day_obj.day - last_seen_day) <= dish['ARA']: continue
        
        # 2. EKİPMAN
        if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']: continue
        if constraints.get('force_equipment') and dish.get('PISIRME_EKIPMAN') != constraints['force_equipment']: continue

        # 3. PROTEIN KONTROLÜ (GELİŞMİŞ)
        # A) Yasaklı Protein (Örn: Ana yemek etliyse, yan yemek etli olmasın)
        if constraints.get('block_protein_list'):
            if p_type in constraints['block_protein_list']: continue
            
        # B) Zorunlu Protein (Örn: Ana yemek etsizse, yan yemek etli olsun)
        # Not: Bu zorunluluğu esnek tutuyoruz, eğer havuzda yoksa pas geçeriz.
        # Bu filtrelemeyi aşağıda "Soft Filter" olarak yapacağız.
        
        # 4. YASAKLI İSİMLER
        if constraints.get('exclude_names') and name in constraints['exclude_names']: continue
        if constraints.get('exclude_keywords'):
            if any(kw in name_upper for kw in constraints['exclude_keywords']): continue

        valid_options.append(dish)
    
    # --- SOFT FILTER (Tercih Edilen Özellikler) ---
    # Eğer zorunlu protein isteniyorsa ve listede varsa, sadece onları filtrele.
    # Yoksa mecburen diğerlerine razı ol (Sistem kilitlenmesin).
    if constraints.get('force_protein_list') and valid_options:
        preferred = [d for d in valid_options if d.get('PROTEIN_TURU', '').strip() in constraints['force_protein_list']]
        if preferred:
            valid_options = preferred

    if not valid_options:
        if candidates: return random.choice(candidates)
        return {"YEMEK ADI": f"---", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": ""}
    
    chosen = random.choice(valid_options)
    # Kayıt işlemi ana döngüde
    return chosen

def record_usage(dish, usage_history, day):
    name = dish['YEMEK ADI']
    if name == "---": return
    if name not in usage_history: usage_history[name] = []
    usage_history[name].append(day)

# =========================================================
# 🧠 ANA ALGORİTMA (ANA YEMEK ODAKLI)
# =========================================================

def generate_smart_menu(month, year, pool, holidays, ready_snack_days_indices):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    
    weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
    fish_day = random.choice(weekdays) if weekdays else None
    
    previous_day_dishes = [] 
    
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%d.%m.%Y")
        weekday_idx = current_date.weekday() # 0=Pzt
        weekday_name = GUNLER_TR[weekday_idx]
        
        if any(h[0] <= current_date.date() <= h[1] for h in holidays):
            menu_log.append({"TARİH": date_str, "GÜN": f"{weekday_name} (TATİL)", "KAHVALTI": "-", "ÖĞLE ANA": "-", "GECE": "-"})
            previous_day_dishes = [] 
            continue

        # --- KAHVALTI ---
        if weekday_idx in [1, 3, 5, 6]:
            kahvalti_ekstra = select_dish(pool, "KAHVALTI EKSTRA", usage_history, current_date, constraints={"exclude_names": previous_day_dishes})
            record_usage(kahvalti_ekstra, usage_history, day)
            kahvalti_full = f"{SABIT_KAHVALTI} + {kahvalti_ekstra['YEMEK ADI']}"
        else:
            kahvalti_full = SABIT_KAHVALTI 
        
        daily_exclude = previous_day_dishes.copy()
        is_today_fish = (day == fish_day)
        is_weekend = (weekday_idx >= 5)
        
        if is_weekend:
            # === HAFTA SONU (Öğle = Akşam) ===
            # ÖNCE ANA YEMEK SEÇ (Patron o)
            ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints={"exclude_names": daily_exclude})
            ana_protein = ana.get('PROTEIN_TURU', '').strip()
            
            # Yan Yemek Kısıtlamaları
            side_constraints = {"exclude_names": daily_exclude}
            if ana.get('PISIRME_EKIPMAN') == 'FIRIN': side_constraints['block_equipment'] = 'FIRIN'
            
            # PROTEIN DENGESİ: Ana yemek Etliyse -> Yanlar Etsiz olsun
            if ana_protein in ['KIRMIZI', 'BEYAZ']:
                side_constraints['block_protein_list'] = ['KIRMIZI', 'BEYAZ']
            # Ana yemek Etsiz ise -> Yanlar Etli olsun (Tercihen)
            elif ana_protein == 'ETSIZ':
                side_constraints['force_protein_list'] = ['KIRMIZI', 'BEYAZ']

            # Çorba Seçimi
            corba = select_dish(pool, "ÇORBA", usage_history, current_date, constraints=side_constraints)
            if any(x in corba['YEMEK ADI'].upper() for x in YOGURTLU_CORBALAR): side_constraints['exclude_keywords'] = YOGURT_YAN_URUNLER
            
            # Yan Yemek Seçimi
            if ana.get('ZORUNLU_ES'): yan = {"YEMEK ADI": ana['ZORUNLU_ES'], "PISIRME_EKIPMAN": "TENCERE"}
            else: yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, side_constraints)
            
            tamm_constraints = {"exclude_names": daily_exclude}
            if 'exclude_keywords' in side_constraints: tamm_constraints['exclude_keywords'] = side_constraints['exclude_keywords']
            tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_constraints)
            
            ogle_corba = aksam_corba = corba
            ogle_ana = aksam_ana = ana
            ogle_yan = aksam_yan = yan
            ogle_tamm = aksam_tamm = tamm
            
            for d in [corba, ana, yan, tamm]: record_usage(d, usage_history, day)

        elif is_today_fish:
            # === BALIK GÜNÜ ===
            ogle_corba = {"YEMEK ADI": "Mercimek Çorbası"}
            fish_cands = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            ogle_ana = random.choice(fish_cands) if fish_cands else {"YEMEK ADI": "BALIK YOK"}
            record_usage(ogle_ana, usage_history, day)
            ogle_yan = {"YEMEK ADI": "Salata"}
            ogle_tamm = {"YEMEK ADI": "Tahin Helvası"}
            
            # Akşam (Çorba aynı, Ana farklı)
            aksam_corba = ogle_corba
            
            dinner_main_constraints = {"exclude_names": daily_exclude}
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_main_constraints)
            record_usage(aksam_ana, usage_history, day)
            
            # Akşam Yan Ürünler (Protein Dengesine Göre)
            aksam_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            aksam_side_cons = {"exclude_names": daily_exclude}
            
            if aksam_p_type in ['KIRMIZI', 'BEYAZ']: aksam_side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ']
            elif aksam_p_type == 'ETSIZ': aksam_side_cons['force_protein_list'] = ['KIRMIZI', 'BEYAZ']
            
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': aksam_side_cons['block_equipment'] = 'FIRIN'
            
            if aksam_ana.get('ZORUNLU_ES'): aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_ES']}
            else: aksam_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, aksam_side_cons)
            record_usage(aksam_yan, usage_history, day)
            
            aksam_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, {"exclude_names": daily_exclude})
            record_usage(aksam_tamm, usage_history, day)

        else:
            # === NORMAL HAFTA İÇİ ===
            # 1. Öğle Ana Yemek (Patron)
            ogle_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints={"exclude_names": daily_exclude})
            record_usage(ogle_ana, usage_history, day)
            o_p_type = ogle_ana.get('PROTEIN_TURU', '').strip()
            
            # 2. Akşam Ana Yemek (Öğlenin zıttı olsun)
            dinner_main_cons = {"exclude_names": daily_exclude + [ogle_ana['YEMEK ADI']]}
            if o_p_type in ['KIRMIZI', 'BEYAZ']: dinner_main_cons['block_protein_list'] = [o_p_type] # Aynı et olmasın
            if o_p_type == 'ETSIZ': dinner_main_cons['force_protein_list'] = ['KIRMIZI', 'BEYAZ'] # Akşam et olsun
            
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_main_cons)
            record_usage(aksam_ana, usage_history, day)
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            
            # 3. Ortak Çorba & Yan & Tamm (Her iki ana yemeğe de uymalı!)
            shared_cons = {"exclude_names": daily_exclude}
            
            # Eğer İKİ ana yemek de ETLİ ise -> Yan yemekler kesin ETSIZ olsun
            if o_p_type in ['KIRMIZI', 'BEYAZ'] and a_p_type in ['KIRMIZI', 'BEYAZ']:
                shared_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ']
            
            # Ekipman kontrolü (Herhangi biri fırınsa, yan fırın olmasın)
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN' or aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN':
                shared_cons['block_equipment'] = 'FIRIN'
            
            # Çorba Seç
            shared_corba = select_dish(pool, "ÇORBA", usage_history, current_date, shared_cons)
            record_usage(shared_corba, usage_history, day)
            if any(x in shared_corba['YEMEK ADI'].upper() for x in YOGURTLU_CORBALAR): shared_cons['exclude_keywords'] = YOGURT_YAN_URUNLER
            
            # Yan Yemek Seç
            # Öncelik: Öğle'nin zorunlusu varsa o, yoksa Akşam'ınki, yoksa serbest
            if ogle_ana.get('ZORUNLU_ES'): shared_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_ES'], "PISIRME_EKIPMAN": "TENCERE"}
            elif aksam_ana.get('ZORUNLU_ES'): shared_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_ES'], "PISIRME_EKIPMAN": "TENCERE"}
            else: shared_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, shared_cons)
            record_usage(shared_yan, usage_history, day)
            
            # Tamamlayıcı
            tamm_cons = {"exclude_names": daily_exclude}
            if 'exclude_keywords' in shared_cons: tamm_cons['exclude_keywords'] = shared_cons['exclude_keywords']
            shared_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons)
            record_usage(shared_tamm, usage_history, day)
            
            ogle_corba = aksam_corba = shared_corba
            ogle_yan = aksam_yan = shared_yan
            ogle_tamm = aksam_tamm = shared_tamm

        # --- GECE ATIŞTIRMALIK ---
        gece_cons = {"exclude_names": daily_exclude}
        if weekday_idx in ready_snack_days_indices: gece_cons['force_equipment'] = 'HAZIR'
        gece = select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, current_date, gece_cons)
        record_usage(gece, usage_history, day)

        # KAYIT
        menu_log.append({
            "TARİH": date_str, "GÜN": weekday_name, "KAHVALTI": kahvalti_full,
            "ÖĞLE ÇORBA": ogle_corba['YEMEK ADI'], "ÖĞLE ANA": ogle_ana['YEMEK ADI'], "ÖĞLE YAN": ogle_yan['YEMEK ADI'], "ÖĞLE TAMM": ogle_tamm['YEMEK ADI'],
            "AKŞAM ÇORBA": aksam_corba['YEMEK ADI'], "AKŞAM ANA": aksam_ana['YEMEK ADI'], "AKŞAM YAN": aksam_yan['YEMEK ADI'], "AKŞAM TAMM": aksam_tamm['YEMEK ADI'],
            "GECE": f"Çay/Kahve + {gece['YEMEK ADI']}"
        })
        
        previous_day_dishes = [
            ogle_corba['YEMEK ADI'], ogle_ana['YEMEK ADI'], aksam_ana['YEMEK ADI'], 
            ogle_yan['YEMEK ADI'], ogle_tamm['YEMEK ADI'], gece['YEMEK ADI']
        ]

    return pd.DataFrame(menu_log)

def render_page(sel_model):
    st.header("👨‍🍳 Akıllı Menü Planlayıcı")
    st.markdown("---")
    
    client = get_gspread_client()
    if not client: st.error("Bağlantı hatası!"); st.stop()

    if 'generated_menu' not in st.session_state:
        with st.spinner("Kayıtlı menü yükleniyor..."):
            saved_df = load_last_menu(client)
            if saved_df is not None and not saved_df.empty:
                st.session_state['generated_menu'] = saved_df
                st.info("📂 Son kaydedilen menü yüklendi.")

    col1, col2 = st.columns(2)
    with col1:
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        current_month = datetime.now().month
        sel_month_idx = st.selectbox("Ay Seçin", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=current_month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)

    with col2:
        st.info("🛠️ **Özel Ayarlar**")
        holiday_start = st.date_input("Tatil Başlangıç", value=None)
        holiday_end = st.date_input("Tatil Bitiş", value=None)
        ready_snack_days = st.multiselect("Gece 'HAZIR' Atıştırmalık Günleri", options=GUNLER_TR, default=["Pazar", "Pazartesi"])
        
    if st.button("🚀 Yeni Menü Oluştur", type="primary"):
        with st.spinner("Kurallar işleniyor..."):
            pool = get_full_menu_pool(client)
            if pool:
                holidays = []
                if holiday_start and holiday_end: holidays.append((holiday_start, holiday_end))
                ready_indices = [GUNLER_TR.index(d) for d in ready_snack_days]
                df_menu = generate_smart_menu(sel_month_idx, sel_year, pool, holidays, ready_indices)
                if save_menu_to_sheet(client, df_menu):
                    st.session_state['generated_menu'] = df_menu
                    st.success("Menü oluşturuldu ve kaydedildi! ✅")
                    st.rerun()
                else: st.error("Kaydedilemedi.")

    st.divider()

    if 'generated_menu' in st.session_state:
        st.subheader(f"📅 Aktif Menü")
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['generated_menu'].to_excel(writer, index=False, sheet_name='Menu')
        
        st.download_button("📥 Excel İndir", data=output.getvalue(), file_name=f"Menu_{tr_aylar[sel_month_idx]}_{sel_year}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        edited_menu = st.data_editor(st.session_state['generated_menu'], num_rows="fixed", use_container_width=True, height=600)
        if st.button("💾 Değişiklikleri Kaydet"):
            if save_menu_to_sheet(client, edited_menu):
                st.session_state['generated_menu'] = edited_menu
                st.success("✅ Kaydedildi!")
