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

# Yoğurtlu Olduğu Varsayılan İsimler (Etiketi boş olsa bile yakalar)
YOGURT_KEYWORDS = ["YAYLA", "YOĞURT", "DÜĞÜN", "ERİŞTE", "CACIK", "AYRAN", "HAYDARİ", "MANTI"] 

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
        p_type = dish.get('PROTEIN_TURU', '').strip()
        content_tag = dish.get('ICERIK_TURU', '').strip() 
        
        # Otomatik Etiket Algılama (İsimde Yoğurt geçiyorsa etiketi varmış gibi davran)
        if any(k in name_upper for k in YOGURT_KEYWORDS):
            if not content_tag: content_tag = "YOGURT" # Geçici etiket ata

        # 1. LIMIT ve ARA
        count_used = len(usage_history.get(name, []))
        if count_used >= dish['LIMIT']: continue
        if count_used > 0:
            last_seen_day = usage_history[name][-1]
            if (current_day_obj.day - last_seen_day) <= dish['ARA']: continue
        
        # 2. EKİPMAN KONTROLÜ
        if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']: continue
        if constraints.get('force_equipment') and dish.get('PISIRME_EKIPMAN') != constraints['force_equipment']: continue

        # 3. PROTEIN KONTROLÜ
        if constraints.get('block_protein_list') and p_type in constraints['block_protein_list']: continue
        if constraints.get('force_protein_types') and p_type not in constraints['force_protein_types']: continue
        
        # 4. İÇERİK KONTROLÜ (YOGURT vb.)
        # Eğer bu yemeğin etiketi (örn: YOGURT), yasaklı etiketler listesindeyse -> SEÇME
        if constraints.get('block_content_tags') and content_tag:
            if content_tag in constraints['block_content_tags']: continue

        # 5. YASAKLI İSİMLER
        if constraints.get('exclude_names') and name in constraints['exclude_names']: continue

        valid_options.append(dish)
    
    if not valid_options:
        if candidates: 
            chosen = random.choice(candidates)
            chosen['YEMEK ADI'] = f"{chosen['YEMEK ADI']} (!)" 
            return chosen
        return {"YEMEK ADI": f"---", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": "", "ICERIK_TURU": ""}
    
    chosen = random.choice(valid_options)
    return chosen

def record_usage(dish, usage_history, day):
    name = dish['YEMEK ADI'].replace(" (!)", "")
    if name == "---": return
    if name not in usage_history: usage_history[name] = []
    usage_history[name].append(day)

def get_content_tag(dish):
    """Bir yemeğin içerik etiketini (Varsa Excel'den, yoksa İsimden) döndürür."""
    tag = dish.get('ICERIK_TURU', '').strip()
    name_upper = dish.get('YEMEK ADI', '').upper()
    if not tag and any(k in name_upper for k in YOGURT_KEYWORDS):
        tag = "YOGURT"
    return tag

# =========================================================
# 🧠 ANA ALGORİTMA
# =========================================================

def generate_smart_menu(month, year, pool, holidays, ready_snack_days_indices):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    
    weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
    fish_day = random.choice(weekdays) if weekdays else None
    
    previous_day_dishes = [] 
    meatless_main_count = 0 
    MAX_MEATLESS_MAINS = 4
    
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%d.%m.%Y")
        weekday_idx = current_date.weekday()
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
        is_wednesday = (weekday_idx == 2)
        
        daily_oven_used = False
        daily_banned_tags = [] # Günlük Yasaklı Etiketler (YOGURT vb.)
        
        if is_weekend:
            # === HAFTA SONU ===
            
            # 1. ANA YEMEK
            ana_cons = {"exclude_names": daily_exclude}
            if meatless_main_count >= MAX_MEATLESS_MAINS: ana_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints=ana_cons)
            
            ana_p_type = ana.get('PROTEIN_TURU', '').strip()
            if ana_p_type == 'ETSIZ': meatless_main_count += 1
            if ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            if get_content_tag(ana): daily_banned_tags.append(get_content_tag(ana))
            
            # 2. YAN ÜRÜNLER (Ortak)
            side_cons = {"exclude_names": daily_exclude, "block_content_tags": daily_banned_tags}
            if daily_oven_used: side_cons['block_equipment'] = 'FIRIN'
            
            # Telafi Kuralı: Ana yemek Etsiz ise, Yan/Çorba Etli olsun
            if ana_p_type == 'ETSIZ': 
                # Burada zorunluluk koymuyoruz ama tercih ediyoruz, yoksa kilitlenir.
                # Sistem havuzda bulamazsa rastgele seçer.
                pass 
            elif ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: 
                side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            # Çorba
            corba = select_dish(pool, "ÇORBA", usage_history, current_date, constraints=side_cons)
            if corba.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            if get_content_tag(corba): daily_banned_tags.append(get_content_tag(corba))
            side_cons['block_content_tags'] = daily_banned_tags # Listeyi güncelle
            
            # Yan Yemek
            if ana.get('ZORUNLU_YAN'): yan = {"YEMEK ADI": ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": ""}
            else: yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, side_cons)
            
            if yan.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            if get_content_tag(yan): daily_banned_tags.append(get_content_tag(yan))
            side_cons['block_content_tags'] = daily_banned_tags
            
            # Tamamlayıcı
            tamm_cons = side_cons.copy()
            # Ana yemek etliyse, tamamlayıcı da etsiz olsun (Ayran olabilir ama Etli Dolma olmasın)
            if ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ana.get('ZORUNLU_TAMM'): tamm = {"YEMEK ADI": ana['ZORUNLU_TAMM'], "ICERIK_TURU": ""}
            else: tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons)
            
            if get_content_tag(tamm): daily_banned_tags.append(get_content_tag(tamm))
            
            ogle_corba = aksam_corba = corba
            ogle_ana = aksam_ana = ana
            ogle_yan = aksam_yan = yan
            ogle_tamm = aksam_tamm = tamm
            
            for d in [corba, ana, yan, tamm]: record_usage(d, usage_history, day)

        elif is_today_fish:
            # === BALIK GÜNÜ ===
            ogle_corba = {"YEMEK ADI": "Mercimek Çorbası", "ICERIK_TURU": ""}
            fish_cands = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            ogle_ana = random.choice(fish_cands) if fish_cands else {"YEMEK ADI": "BALIK YOK"}
            record_usage(ogle_ana, usage_history, day)
            ogle_yan = {"YEMEK ADI": "Salata", "ICERIK_TURU": ""}
            ogle_tamm = {"YEMEK ADI": "Tahin Helvası", "ICERIK_TURU": ""}
            
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Akşam
            aksam_corba = ogle_corba
            
            dinner_cons = {"exclude_names": daily_exclude, "block_protein_list": ['BALIK']}
            if is_wednesday: dinner_cons['force_protein_types'] = ['ETSIZ']
            if daily_oven_used: dinner_cons['block_equipment'] = 'FIRIN'
            if not is_wednesday and meatless_main_count >= MAX_MEATLESS_MAINS: dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_cons)
            record_usage(aksam_ana, usage_history, day)
            
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ' and not is_wednesday: meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            aksam_side_cons = {"exclude_names": daily_exclude, "block_content_tags": []} # Akşam için temiz tag listesi gerekebilir ama günlük limit dolduysa fırın yasak
            if daily_oven_used: aksam_side_cons['block_equipment'] = 'FIRIN'
            if a_p_type in ['KIRMIZI', 'BEYAZ']: aksam_side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if aksam_ana.get('ZORUNLU_YAN'): aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN']}
            else: aksam_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, aksam_side_cons)
            record_usage(aksam_yan, usage_history, day)
            
            if aksam_ana.get('ZORUNLU_TAMM'): aksam_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM']}
            else: aksam_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, aksam_side_cons)
            record_usage(aksam_tamm, usage_history, day)

        else:
            # === NORMAL HAFTA İÇİ ===
            
            # 1. ÖĞLE ANA YEMEK
            lunch_cons = {"exclude_names": daily_exclude}
            if is_wednesday: lunch_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            if not is_wednesday and meatless_main_count >= MAX_MEATLESS_MAINS: lunch_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            ogle_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints=lunch_cons)
            record_usage(ogle_ana, usage_history, day)
            
            o_p_type = ogle_ana.get('PROTEIN_TURU', '').strip()
            if o_p_type == 'ETSIZ' and not is_wednesday: meatless_main_count += 1
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            if get_content_tag(ogle_ana): daily_banned_tags.append(get_content_tag(ogle_ana))
            
            # 2. AKŞAM ANA YEMEK
            dinner_cons = {"exclude_names": daily_exclude + [ogle_ana['YEMEK ADI']]}
            if daily_oven_used: dinner_cons['block_equipment'] = 'FIRIN'
            
            if is_wednesday:
                dinner_cons['force_protein_types'] = ['ETSIZ']
            else:
                if o_p_type in ['KIRMIZI', 'BEYAZ']: dinner_cons['block_protein_list'] = [o_p_type] 
                if o_p_type == 'ETSIZ': dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
                if meatless_main_count >= MAX_MEATLESS_MAINS and 'force_protein_types' not in dinner_cons:
                     dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_cons)
            record_usage(aksam_ana, usage_history, day)
            
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ' and not is_wednesday: meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            if get_content_tag(aksam_ana): daily_banned_tags.append(get_content_tag(aksam_ana))
            
            # 3. ORTAK YAN & ÇORBA & TAMM
            shared_cons = {"exclude_names": daily_exclude, "block_content_tags": daily_banned_tags}
            if daily_oven_used: shared_cons['block_equipment'] = 'FIRIN'
            
            # Protein kuralı: Herhangi biri etliyse, yanlar etsiz olsun
            is_any_meat = (o_p_type in ['KIRMIZI', 'BEYAZ']) or (a_p_type in ['KIRMIZI', 'BEYAZ'])
            # Ancak Ana yemek Etsiz ise (Telafi), yanlar Etli OLABİLİR (Block koymuyoruz)
            if is_any_meat: shared_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            # Çorba
            shared_corba = select_dish(pool, "ÇORBA", usage_history, current_date, shared_cons)
            record_usage(shared_corba, usage_history, day)
            if get_content_tag(shared_corba): daily_banned_tags.append(get_content_tag(shared_corba))
            shared_cons['block_content_tags'] = daily_banned_tags
            
            # Yan Yemek (Kilit Kontrolü)
            if ogle_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": ""}
            elif aksam_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": ""}
            else: shared_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, shared_cons)
            
            record_usage(shared_yan, usage_history, day)
            if get_content_tag(shared_yan): daily_banned_tags.append(get_content_tag(shared_yan))
            shared_cons['block_content_tags'] = daily_banned_tags
            
            # Tamamlayıcı
            tamm_cons = shared_cons.copy()
            if is_any_meat: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ogle_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": ogle_ana['ZORUNLU_TAMM'], "ICERIK_TURU": ""}
            elif aksam_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM'], "ICERIK_TURU": ""}
            else: shared_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons)
            
            record_usage(shared_tamm, usage_history, day)
            if get_content_tag(shared_tamm): daily_banned_tags.append(get_content_tag(shared_tamm))
            
            ogle_corba = aksam_corba = shared_corba
            ogle_yan = aksam_yan = shared_yan
            ogle_tamm = aksam_tamm = shared_tamm

        # --- GECE ---
        gece_cons = {"exclude_names": daily_exclude, "block_content_tags": daily_banned_tags}
        if weekday_idx in ready_snack_days_indices: gece_cons['force_equipment'] = 'HAZIR'
        if daily_oven_used: gece_cons['block_equipment'] = 'FIRIN' 
        
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

# =========================================================
# 🖥️ ARAYÜZ
# =========================================================

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
