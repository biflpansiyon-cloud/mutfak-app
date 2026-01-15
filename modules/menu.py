import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

# --- MODÜL IMPORTLARI ---
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

def clean_dish_name(name):
    """İsimdeki zorunlu takısını temizler."""
    return name.replace(" (ZORUNLU)", "").strip()

def get_unique_key(dish):
    cat = safe_str(dish.get('KATEGORİ'))
    name = clean_dish_name(safe_str(dish.get('YEMEK ADI')))
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
            # Limiti 0 olanları baştan eliyoruz (Gurme kuralı: 0 limitli yemek yok hükmündedir)
            try:
                l_val = float(item.get('LIMIT', 99) or 99)
                if l_val > 0: pool.append(item)
            except: pool.append(item)
        return pool
    except Exception as e:
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 🍳 GURME PUANLAMA VE ESNEK SEÇİM MOTORU
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

    # --- KADEMELİ FİLTRELEME (RELAXATION) ---
    def evaluate(strict_level):
        """
        Level 3: Full Gourmet (Taste, Texture, Color, Carb Balance)
        Level 2: Relaxed Taste/Texture/Color (Keep Carb Balance & Limits)
        Level 1: Minimal (Only Limits & Oven)
        """
        results = []
        for dish in candidates:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            name = clean_dish_name(safe_str(dish.get('YEMEK ADI')))
            score = meta['puan']

            # --- ASLA ESNETİLEMEYENLER (HARD) ---
            if oven_banned and meta['equip'] == 'FIRIN': continue
            
            used_days = usage_history.get(u_key, [])
            try: limit_val = int(float(dish.get('LIMIT') or 99))
            except: limit_val = 99
            if len(used_days) >= limit_val: continue
            
            try: ara_val = int(float(dish.get('ARA') or 0))
            except: ara_val = 0
            if used_days and (current_day_obj.day - used_days[-1]) <= ara_val: continue

            if constraints.get('exclude_names') and name in constraints['exclude_names']: continue

            # --- SEVİYE BAZLI FİLTRELER ---
            if strict_level >= 1:
                # Protein Kısıtı
                if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']: continue
                if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']: continue
                # İçerik Çakışması (Yoğurt)
                if meta['tag'] and constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']: continue

            if strict_level >= 2:
                # Karbonhidrat Dengesi (Pilav-Makarna-Patates engeli)
                if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']: continue
                # Bakliyat Arası
                if meta['alt_tur'] == 'BAKLIYAT' and global_history:
                    if (current_day_obj.day - global_history.get('last_legume', -99)) < 3: continue

            if strict_level >= 3:
                # Renk Dengesi
                if constraints.get('current_meal_colors') and meta['renk'] == 'KIRMIZI':
                    if constraints['current_meal_colors'].count('KIRMIZI') >= 2: continue
                
                # --- PUANLAMA (Sadece Level 3'te full puanlama yapalım) ---
                if constraints.get('perfect_match_name') and name.upper() in constraints['perfect_match_name'].upper():
                    score += 50
                if constraints.get('meal_textures'):
                    if "SULU" in constraints['meal_textures'] and meta['doku'] == "KURU": score += 15
                    if meta['doku'] in constraints['meal_textures']: score -= 10
                if constraints.get('meal_flavors'):
                    if meta['tat'] in constraints['meal_flavors']: score -= 15
                    if "SALÇALI" in constraints['meal_flavors'] and meta['tat'] in ["SADE", "KREMALI"]: score += 10

            results.append((dish, score))
        return results

    # Kademeleri Dene
    final_scored = evaluate(3) # İdeal
    if not final_scored: final_scored = evaluate(2) # Esnek
    if not final_scored: final_scored = evaluate(1) # Minimal

    # Eğer hala yoksa: Acil Durum
    if not final_scored:
        emergency = [d for d in candidates if not (oven_banned and safe_str(d.get('PISIRME_EKIPMAN')) == 'FIRIN')]
        if emergency:
            chosen = random.choice(emergency).copy()
            c_name = safe_str(chosen.get('YEMEK ADI'))
            if "(ZORUNLU)" not in c_name: chosen['YEMEK ADI'] = c_name + " (ZORUNLU)"
            return chosen
        return {"YEMEK ADI": "---", "PISIRME_EKIPMAN": "YOK"}

    # En iyileri seç
    final_scored.sort(key=lambda x: x[1], reverse=True)
    # Puanı en yüksek olan ilk 3 arasından seç ki çeşitlilik olsun
    top_candidates = [x[0] for x in final_scored[:3]]
    return random.choice(top_candidates)

# =========================================================
# 📅 GURME PLANLAMA DÖNGÜSÜ
# =========================================================

def record_usage(dish, usage_history, day, global_history):
    if not dish or dish.get('YEMEK ADI') == "---": return
    u_key = get_unique_key(dish)
    if u_key not in usage_history: usage_history[u_key] = []
    usage_history[u_key].append(day)
    if get_dish_meta(dish)['alt_tur'] == 'BAKLIYAT': global_history['last_legume'] = day

def generate_gourmet_menu(month, year, pool, holidays, ready_snack_indices, fish_pref, target_meatless):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    global_history = {'last_legume': -99}
    
    # Balık Günü Ayarı
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
        d_str, w_idx = curr_date.strftime("%d.%m.%Y"), curr_date.weekday()
        w_name = GUNLER_TR[w_idx]
        
        if any(h[0] <= curr_date.date() <= h[1] for h in holidays):
            menu_log.append({"TARİH": d_str, "GÜN": f"{w_name} (TATİL)", "KAHVALTI": "-", "ÖĞLE ANA": "-", "GECE": "-"})
            prev_dishes = []
            continue

        OVEN_LOCKED, daily_exclude = False, prev_dishes.copy()
        
        # 1. KAHVALTI
        k_str = "-"
        if w_idx in [1, 3, 5, 6]:
            kahv = score_and_select_dish(pool, "KAHVALTI EKSTRA", usage_history, curr_date, OVEN_LOCKED, {"exclude_names": daily_exclude})
            record_usage(kahv, usage_history, day, global_history)
            k_str = kahv.get('YEMEK ADI')
            if get_dish_meta(kahv)['equip'] == 'FIRIN': OVEN_LOCKED = True

        # Hedef Takibi
        days_left = num_days - day + 1
        force_veg = (target_meatless - meatless_cnt) >= days_left - 1
        
        def plan_meal_set(prefix, is_fish_meal=False):
            nonlocal OVEN_LOCKED, meatless_cnt
            
            # Ana Yemek
            a_cons = {"exclude_names": daily_exclude}
            if is_fish_meal: a_cons['force_fish'] = True
            elif force_veg: a_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_cnt >= target_meatless: a_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            ana = score_and_select_dish(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, a_cons)
            record_usage(ana, usage_history, day, global_history)
            
            a_m = get_dish_meta(ana)
            if a_m['equip'] == 'FIRIN': OVEN_LOCKED = True
            if a_m['p_type'] == 'ETSIZ' and not is_fish_meal: meatless_cnt += 1
            
            # Ortak Filtreler
            meal_cons = {
                "exclude_names": daily_exclude + [ana.get('YEMEK ADI')],
                "perfect_match_name": a_m['yakisan'],
                "meal_textures": [a_m['doku']],
                "meal_flavors": [a_m['tat']],
                "current_meal_colors": [a_m['renk']],
                "block_content_tags": [a_m['tag']] if a_m['tag'] else []
            }
            if a_m['alt_tur'] in ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']:
                meal_cons['block_alt_types'] = ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']
            if a_m['p_type'] in ['KIRMIZI', 'BEYAZ']:
                meal_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']

            corba = score_and_select_dish(pool, "ÇORBA", usage_history, curr_date, OVEN_LOCKED, meal_cons)
            record_usage(corba, usage_history, day, global_history)
            if get_dish_meta(corba)['equip'] == 'FIRIN': OVEN_LOCKED = True
            
            side = score_and_select_dish(pool, "YAN YEMEK", usage_history, curr_date, OVEN_LOCKED, meal_cons)
            record_usage(side, usage_history, day, global_history)
            if get_dish_meta(side)['equip'] == 'FIRIN': OVEN_LOCKED = True

            tamm = score_and_select_dish(pool, "TAMAMLAYICI", usage_history, curr_date, OVEN_LOCKED, meal_cons)
            record_usage(tamm, usage_history, day, global_history)
            
            return corba, ana, side, tamm

        # Hafta İçi / Sonu Ayrımı
        if w_idx >= 5: # Hafta sonu
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set("ÖĞLE")
            a_corba, a_ana, a_yan, a_tamm = plan_meal_set("AKŞAM")
        else: # Hafta içi
            is_f = (day == fish_day)
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set("ÖĞLE", is_f)
            
            # Akşam ana yemeği farklı olsun
            a_cons = {"exclude_names": daily_exclude + [o_ana.get('YEMEK ADI')]}
            if not is_f and get_dish_meta(o_ana)['p_type'] in ['KIRMIZI', 'BEYAZ']:
                a_cons['block_protein_list'] = [get_dish_meta(o_ana)['p_type']]
            a_ana = score_and_select_dish(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, a_cons)
            record_usage(a_ana, usage_history, day, global_history)
            
            a_corba, a_yan, a_tamm = o_corba, o_yan, o_tamm

        # Gece Atıştırmalık
        s_cons = {"exclude_names": daily_exclude}
        if w_idx in ready_snack_indices: s_cons['force_equipment'] = 'HAZIR'
        snack = score_and_select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, curr_date, OVEN_LOCKED, s_cons)
        record_usage(snack, usage_history, day, global_history)

        menu_log.append({
            "TARİH": d_str, "GÜN": w_name, "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": o_corba.get('YEMEK ADI'), "ÖĞLE ANA": o_ana.get('YEMEK ADI'), "ÖĞLE YAN": o_yan.get('YEMEK ADI'), "ÖĞLE TAMM": o_tamm.get('YEMEK ADI'),
            "AKŞAM ÇORBA": a_corba.get('YEMEK ADI'), "AKŞAM ANA": a_ana.get('YEMEK ADI'), "AKŞAM YAN": a_yan.get('YEMEK ADI'), "AKŞAM TAMM": a_tamm.get('YEMEK ADI'),
            "GECE": f"Çay/Kahve + {snack.get('YEMEK ADI')}"
        })
        prev_dishes = [o_corba.get('YEMEK ADI'), o_ana.get('YEMEK ADI'), a_ana.get('YEMEK ADI'), o_yan.get('YEMEK ADI'), snack.get('YEMEK ADI')]

    return pd.DataFrame(menu_log)

# =========================================================
# 🖥️ ARAYÜZ (GURME UI)
# =========================================================

def render_page(sel_model):
    st.header("👨‍🍳 Gurme Menü Şefi (v5.1 - Esnek Mod)")
    st.info("Kurallar esnetilerek planlama yapılır, 'Zorunlu' etiketini minimuma indirir.")
    
    client = get_gspread_client()
    if not client: st.error("Bağlantı hatası!"); st.stop()

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
    with c1: fish_pref = st.selectbox("Balık Günü", ["Otomatik", "Yok"] + GUNLER_TR)
    with c2: target_meatless = st.slider("Etsiz Öğün Hedefi", 0, 30, 12)

    if st.button("🚀 Gurme Menü Oluştur", type="primary"):
        with st.spinner("Şef mutfakta, menü esnetilerek oluşturuluyor..."):
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
                    st.success("Menü Hazır! ✅")
                    st.rerun()

    if 'generated_menu' in st.session_state:
        st.divider()
        edited = st.data_editor(st.session_state['generated_menu'], use_container_width=True, height=600)
        if st.button("💾 Kaydet"):
            if save_menu_to_sheet(client, edited): st.success("Kaydedildi.")
