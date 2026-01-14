import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

# --- MODÜL IMPORTLARI ---
# Not: utils modülünüzün var olduğu ve gerekli fonksiyonları sağladığı varsayılmıştır.
from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            
    MENU_POOL_SHEET_NAME  
)

# --- AYARLAR ---
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

# =========================================================
# 🛠️ GURME YARDIMCI FONKSİYONLAR
# =========================================================

def safe_str(val):
    if val is None: return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    return s

def get_unique_key(dish):
    cat = safe_str(dish.get('KATEGORİ'))
    name = safe_str(dish.get('YEMEK ADI'))
    return f"{cat}_{name}"

def get_dish_meta(dish):
    if not dish: return {"tag": "", "alt_tur": "", "renk": "", "equip": "", "p_type": "", "tat": "", "doku": "", "puan": 5, "yakisan": ""}
    
    try: puan = float(dish.get('GURME_PUAN') or 5)
    except: puan = 5

    return {
        "tag": safe_str(dish.get('ICERIK_TURU')),
        "alt_tur": safe_str(dish.get('ALT_TUR')),
        "renk": safe_str(dish.get('RENK')),
        "equip": safe_str(dish.get('PISIRME_EKIPMAN')),
        "p_type": safe_str(dish.get('PROTEIN_TURU')),
        "tat": safe_str(dish.get('TAT_PROFILI')),
        "doku": safe_str(dish.get('DOKU')),
        "puan": puan,
        "yakisan": safe_str(dish.get('EN_YAKISAN_YAN'))
    }

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
            # Limiti 0 olanları baştan eliyoruz
            try:
                if float(item.get('LIMIT', 99) or 99) > 0:
                    pool.append(item)
            except: pool.append(item)
        return pool
    except Exception as e:
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 🍳 GURME PUANLAMA VE SEÇİM MOTORU
# =========================================================

def score_and_select_dish(pool, category, usage_history, current_day_obj, 
                          oven_banned=False, 
                          constraints=None, global_history=None):
    
    if constraints is None: constraints = {}
    
    # 1. Havuzu Süz
    candidates = [d for d in pool if safe_str(d.get('KATEGORİ')) == category]
    
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if safe_str(d.get('PROTEIN_TURU')) != 'BALIK']

    day_name = GUNLER_TR[current_day_obj.weekday()]
    candidates = [d for d in candidates if day_name.upper() not in safe_str(d.get('YASAKLI_GUNLER')).upper()]

    scored_candidates = []

    for dish in candidates:
        meta = get_dish_meta(dish)
        u_key = get_unique_key(dish)
        name = safe_str(dish.get('YEMEK ADI'))
        score = meta['puan'] # Baz puan (GURME_PUAN)

        # --- SERT KISITLAMALAR (Geçemezse Skor -999) ---
        
        # Fırın kuralı (En kutsal kural)
        if oven_banned and meta['equip'] == 'FIRIN': continue
        
        # Limit ve Sıklık
        used_days = usage_history.get(u_key, [])
        try: limit_val = int(float(dish.get('LIMIT') or 99))
        except: limit_val = 99
        if len(used_days) >= limit_val: continue
        
        try: ara_val = int(float(dish.get('ARA') or 0))
        except: ara_val = 0
        if used_days and (current_day_obj.day - used_days[-1]) <= ara_val: continue

        # Protein ve Günlük İsim Engeli
        if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']: continue
        if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']: continue
        if constraints.get('exclude_names') and name in constraints['exclude_names']: continue

        # --- GURME PUANLAMA FAKTÖRLERİ ---

        # A. Gastronomik Eşleşme (EN_YAKISAN_YAN bonusu)
        if constraints.get('perfect_match_name') and name.upper() in constraints['perfect_match_name'].upper():
            score += 50 # Muazzam bir uyum bonusu

        # B. Doku Dengesi (Contrast is King)
        if constraints.get('meal_textures'):
            # Eğer tabakta çok fazla sulu yemek varsa, kuru yemeklere bonus ver
            if "SULU" in constraints['meal_textures'] and meta['doku'] == "KURU": score += 10
            # Aynı dokudaki yemeklere hafif ceza
            if meta['doku'] in constraints['meal_textures']: score -= 5

        # C. Tat Profili Dengesi
        if constraints.get('meal_flavors'):
            # Salçalı yemeğin yanına salçalı yemek gelmesin
            if meta['tat'] in constraints['meal_flavors']: score -= 15
            # Zıt tat profili bonusu (Örn: Salçalı yanına Sade/Kremalı)
            if "SALÇALI" in constraints['meal_flavors'] and meta['tat'] in ["SADE", "KREMALI"]: score += 8

        # D. Karbonhidrat Polisi (Hard check in logic, here adds variety)
        if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']:
            continue # Seviye 0 gibi davran, hamur üstüne hamur olmaz

        # E. İçerik Etiketi (Yoğurt-Yoğurt)
        if meta['tag'] and constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']:
            continue

        scored_candidates.append((dish, score))

    # Eğer hiç aday kalmadıysa (Zorunlu mod)
    if not scored_candidates:
        emergency_pool = [d for d in candidates if not (oven_banned and safe_str(d.get('PISIRME_EKIPMAN')) == 'FIRIN')]
        if emergency_pool:
            chosen = random.choice(emergency_pool).copy()
            if "(ZORUNLU)" not in chosen['YEMEK ADI']:
                chosen['YEMEK ADI'] += " (ZORUNLU)"
            return chosen
        return {"YEMEK ADI": "---", "PISIRME_EKIPMAN": "YOK"}

    # En yüksek puanlılardan birini seç (En yüksek 3 puanlı arasından rastgele ki menü her seferinde aynı olmasın)
    scored_candidates.sort(key=lambda x: x[1], reverse=True)
    top_tier = scored_candidates[:3]
    return random.choice(top_tier)[0]

# =========================================================
# 📅 GURME PLANLAMA DÖNGÜSÜ
# =========================================================

def generate_gourmet_menu(month, year, pool, holidays, ready_snack_indices, fish_pref, target_meatless):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    global_history = {'last_legume': -99}
    
    # Balık Günü Kararı
    fish_day = None
    if fish_pref == "Otomatik":
        weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
        if weekdays: fish_day = random.choice(weekdays)
    elif fish_pref != "Yok":
        try:
            t_idx = GUNLER_TR.index(fish_pref)
            possible = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() == t_idx]
            if possible: fish_day = random.choice(possible)
        except: pass

    meatless_cnt = 0
    prev_dishes = []

    for day in range(1, num_days + 1):
        curr_date = datetime(year, month, day)
        d_str = curr_date.strftime("%d.%m.%Y")
        w_idx = curr_date.weekday()
        w_name = GUNLER_TR[w_idx]
        
        if any(h[0] <= curr_date.date() <= h[1] for h in holidays):
            menu_log.append({"TARİH": d_str, "GÜN": f"{w_name} (TATİL)", "KAHVALTI": "-", "ÖĞLE ANA": "-", "GECE": "-"})
            prev_dishes = []
            continue

        OVEN_LOCKED = False
        daily_exclude = prev_dishes.copy()
        
        # 1. KAHVALTI (Sadece Ekstra)
        k_str = "-"
        if w_idx in [1, 3, 5, 6]:
            kahv = score_and_select_dish(pool, "KAHVALTI EKSTRA", usage_history, curr_date, OVEN_LOCKED, {"exclude_names": daily_exclude})
            record_usage(kahv, usage_history, day, global_history)
            k_str = kahv.get('YEMEK ADI')
            if safe_str(kahv.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        # Hedef Takibi
        days_left = num_days - day + 1
        force_veg = (target_meatless - meatless_cnt) >= days_left - 1
        
        # --- ÖĞÜN OLUŞTURUCU (Gurme Mantığı) ---
        def plan_meal(prefix, is_fish_meal=False):
            nonlocal OVEN_LOCKED, meatless_cnt
            
            # ANA YEMEK
            ana_cons = {"exclude_names": daily_exclude}
            if is_fish_meal: ana_cons['force_fish'] = True
            elif force_veg: ana_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_cnt >= target_meatless: ana_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            ana = score_and_select_dish(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, ana_cons)
            record_usage(ana, usage_history, day, global_history)
            
            ana_meta = get_dish_meta(ana)
            if ana_meta['equip'] == 'FIRIN': OVEN_LOCKED = True
            if ana_meta['p_type'] == 'ETSIZ' and not is_fish_meal: meatless_cnt += 1
            
            # Ortak Kısıtlamalar (Yan yemek ve çorba için)
            shared_cons = {
                "exclude_names": daily_exclude + [ana.get('YEMEK ADI')],
                "perfect_match_name": ana_meta['yakisan'],
                "meal_textures": [ana_meta['doku']],
                "meal_flavors": [ana_meta['tat']],
                "block_content_tags": [ana_meta['tag']] if ana_meta['tag'] else []
            }
            if ana_meta['alt_tur'] in ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']:
                shared_cons['block_alt_types'] = ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']
            if ana_meta['p_type'] in ['KIRMIZI', 'BEYAZ']:
                shared_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']

            # ÇORBA
            corba = score_and_select_dish(pool, "ÇORBA", usage_history, curr_date, OVEN_LOCKED, shared_cons)
            record_usage(corba, usage_history, day, global_history)
            c_meta = get_dish_meta(corba)
            if c_meta['equip'] == 'FIRIN': OVEN_LOCKED = True
            shared_cons['meal_textures'].append(c_meta['doku'])
            shared_cons['meal_flavors'].append(c_meta['tat'])
            if c_meta['tag']: shared_cons['block_content_tags'].append(c_meta['tag'])

            # YAN YEMEK
            side = score_and_select_dish(pool, "YAN YEMEK", usage_history, curr_date, OVEN_LOCKED, shared_cons)
            record_usage(side, usage_history, day, global_history)
            s_meta = get_dish_meta(side)
            if s_meta['equip'] == 'FIRIN': OVEN_LOCKED = True
            if s_meta['tag']: shared_cons['block_content_tags'].append(s_meta['tag'])

            # TAMAMLAYICI
            tamm = score_and_select_dish(pool, "TAMAMLAYICI", usage_history, curr_date, OVEN_LOCKED, shared_cons)
            record_usage(tamm, usage_history, day, global_history)
            
            return corba, ana, side, tamm

        # Hafta içi/sonu öğün ayrımı
        if w_idx >= 5: # Hafta sonu: Öğle ve Akşam tamamen farklı gurme öğünler
            o_corba, o_ana, o_yan, o_tamm = plan_meal("ÖĞLE")
            a_corba, a_ana, a_yan, a_tamm = plan_meal("AKŞAM")
        else: # Hafta içi: Öğle ve Akşam Ana Yemek farklı, diğerleri ortak gurme eşleşme
            is_fish_day = (day == fish_day)
            o_corba, o_ana, o_yan, o_tamm = plan_meal("ÖĞLE", is_fish_day)
            
            # Akşam ana yemeği için özel skorlama
            a_cons = {"exclude_names": daily_exclude + [o_ana.get('YEMEK ADI')]}
            if not is_fish_day:
                if get_dish_meta(o_ana)['p_type'] in ['KIRMIZI', 'BEYAZ']: 
                    a_cons['block_protein_list'] = [get_dish_meta(o_ana)['p_type']]
            
            a_ana = score_and_select_dish(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, a_cons)
            record_usage(a_ana, usage_history, day, global_history)
            if get_dish_meta(a_ana)['equip'] == 'FIRIN': OVEN_LOCKED = True
            
            a_corba, a_yan, a_tamm = o_corba, o_yan, o_tamm

        # GECE
        snack_cons = {"exclude_names": daily_exclude}
        if w_idx in ready_snack_indices: snack_cons['force_equipment'] = 'HAZIR'
        snack = score_and_select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, curr_date, OVEN_LOCKED, snack_cons)
        record_usage(snack, usage_history, day, global_history)

        menu_log.append({
            "TARİH": d_str, "GÜN": w_name, "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": o_corba.get('YEMEK ADI'), "ÖĞLE ANA": o_ana.get('YEMEK ADI'), "ÖĞLE YAN": o_yan.get('YEMEK ADI'), "ÖĞLE TAMM": o_tamm.get('YEMEK ADI'),
            "AKŞAM ÇORBA": a_corba.get('YEMEK ADI'), "AKŞAM ANA": a_ana.get('YEMEK ADI'), "AKŞAM YAN": a_yan.get('YEMEK ADI'), "AKŞAM TAMM": a_tamm.get('YEMEK ADI'),
            "GECE": f"Çay/Kahve + {snack.get('YEMEK ADI')}"
        })
        prev_dishes = [o_corba.get('YEMEK ADI'), o_ana.get('YEMEK ADI'), a_ana.get('YEMEK ADI'), o_yan.get('YEMEK ADI'), snack.get('YEMEK ADI')]

    return pd.DataFrame(menu_log)

def record_usage(dish, usage_history, day, global_history):
    if not dish or dish.get('YEMEK ADI') == "---": return
    clean_name = safe_str(dish.get('YEMEK ADI')).replace(" (ZORUNLU)", "")
    u_key = f"{safe_str(dish.get('KATEGORİ'))}_{clean_name}"
    if u_key not in usage_history: usage_history[u_key] = []
    usage_history[u_key].append(day)
    if safe_str(dish.get('ALT_TUR')) == 'BAKLIYAT': global_history['last_legume'] = day

# =========================================================
# 🖥️ ARAYÜZ (GURME UI)
# =========================================================

def render_page(sel_model):
    st.header("👨‍🍳 Gurme Menü Şefi (v5.0)")
    st.info("Bu algoritma lezzet uyumu, doku dengesi ve gastronomi kurallarına göre planlama yapar.")
    
    client = get_gspread_client()
    if not client: st.error("Google Sheets bağlantısı başarısız!"); st.stop()

    if 'generated_menu' not in st.session_state:
        saved_df = load_last_menu(client)
        if saved_df is not None: st.session_state['generated_menu'] = saved_df

    col1, col2 = st.columns(2)
    with col1:
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        sel_month = st.selectbox("Ay", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=datetime.now().month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)
    with col2:
        h_start = st.date_input("Tatil Başlangıç", value=None)
        h_end = st.date_input("Tatil Bitiş", value=None)
        ready_days = st.multiselect("Gece Hazır Atıştırmalık", options=GUNLER_TR, default=["Pazar", "Pazartesi"])
        
    st.divider()
    c1, c2 = st.columns(2)
    with c1: fish_pref = st.selectbox("Balık Günü Tercihi", ["Otomatik", "Yok"] + GUNLER_TR)
    with c2: target_meatless = st.slider("Aylık Etsiz Öğün Hedefi", 0, 30, 12)

    if st.button("🚀 Gurme Menü Oluştur", type="primary"):
        with st.spinner("Şef mutfakta, lezzet eşleşmeleri hesaplanıyor..."):
            pool = get_full_menu_pool(client)
            if pool:
                holidays = [(h_start, h_end)] if h_start and h_end else []
                df_menu = generate_gourmet_menu(
                    sel_month, sel_year, pool, holidays, 
                    [GUNLER_TR.index(d) for d in ready_days], 
                    fish_pref, target_meatless
                )
                if save_menu_to_sheet(client, df_menu):
                    st.session_state['generated_menu'] = df_menu
                    st.success("Gurme Menü Hazır! ✅")
                    st.rerun()

    if 'generated_menu' in st.session_state:
        st.divider()
        st.subheader("📋 Planlanan Gurme Menü")
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['generated_menu'].to_excel(writer, index=False, sheet_name='Menu')
        st.download_button("📥 Excel Olarak İndir", data=output.getvalue(), file_name=f"Gurme_Menu_{sel_month}.xlsx")
        
        edited = st.data_editor(st.session_state['generated_menu'], use_container_width=True, height=600)
        if st.button("💾 Değişiklikleri Kaydet"):
            if save_menu_to_sheet(client, edited): st.success("Değişiklikler başarıyla kaydedildi.")
