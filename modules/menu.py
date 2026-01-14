import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

# --- GEREKLİ MODÜLLER ---
from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            
    MENU_POOL_SHEET_NAME  
)

# --- AYARLAR ---
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
YOGURT_KEYWORDS = ["YAYLA", "YOĞURT", "DÜĞÜN", "CACIK", "AYRAN", "HAYDARİ", "MANTI"] 

# =========================================================
# 🛠️ YARDIMCI FONKSİYONLAR
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
    if not dish: return {"tag": "", "alt_tur": "", "renk": "", "equip": "", "p_type": ""}
    name = safe_str(dish.get('YEMEK ADI'))
    tag = safe_str(dish.get('ICERIK_TURU'))
    if not tag and any(k in name.upper() for k in YOGURT_KEYWORDS): tag = "YOGURT"
    return {
        "tag": tag,
        "alt_tur": safe_str(dish.get('ALT_TUR')),
        "renk": safe_str(dish.get('RENK')),
        "equip": safe_str(dish.get('PISIRME_EKIPMAN')),
        "p_type": safe_str(dish.get('PROTEIN_TURU'))
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
            
            # KRİTİK: Limiti 0 olan yemeği daha havuzdayken imha et
            try:
                limit_val = float(item.get('LIMIT', 99) or 99)
            except:
                limit_val = 99
            
            if limit_val > 0:
                pool.append(item)
        
        random.shuffle(pool)
        return pool
    except Exception as e:
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 🛡️ GELİŞMİŞ SEÇİCİ (V5 - LİMİT VE ETİKET KORUMALI)
# =========================================================

def select_dish_strict(pool, category, usage_history, current_day_obj, 
                       oven_banned=False, 
                       constraints=None, global_history=None):
    
    if constraints is None: constraints = {}
    
    candidates = [d for d in pool if safe_str(d.get('KATEGORİ')) == category]
    
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if safe_str(d.get('PROTEIN_TURU')) != 'BALIK']

    day_name = GUNLER_TR[current_day_obj.weekday()]
    candidates = [d for d in candidates if day_name.upper() not in safe_str(d.get('YASAKLI_GUNLER')).upper()]

    def apply_filters(candidate_list, strict_level):
        valid = []
        for dish in candidate_list:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            
            # --- FIRIN (ASLA ESNETİLMEZ) ---
            if oven_banned and meta['equip'] == 'FIRIN': continue
            
            # --- SEVİYE 0: TEMEL KURALLAR ---
            if strict_level >= 0:
                # Limit Kontrolü (Hala 0 değilse bile kotasını doldurduysa ele)
                try: limit_val = int(float(dish.get('LIMIT') or 99))
                except: limit_val = 99
                
                used = usage_history.get(u_key, [])
                if len(used) >= limit_val: continue
                
                # Sıklık Kontrolü
                try: ara_val = int(float(dish.get('ARA') or 0))
                except: ara_val = 0
                if used and (current_day_obj.day - used[-1]) <= ara_val: continue
                
                # Protein ve İsim
                if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']: continue
                if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']: continue
                if constraints.get('exclude_names') and safe_str(dish.get('YEMEK ADI')) in constraints['exclude_names']: continue
                
                # Etiket Çakışması (FIX: Boş etiketler birbirini engellemez)
                if meta['tag'] and constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']: continue

            # --- SEVİYE 1: TERCİHLER ---
            if strict_level >= 1:
                if meta['alt_tur'] == 'BAKLIYAT' and global_history:
                    if (current_day_obj.day - global_history.get('last_legume', -99)) < 3: continue
                if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']: continue

            # --- SEVİYE 2: GÖRSEL ---
            if strict_level >= 2:
                if constraints.get('current_meal_colors') and meta['renk'] == 'KIRMIZI':
                    if constraints['current_meal_colors'].count('KIRMIZI') >= 2: continue

            valid.append(dish)
        return valid

    # Deneme Zinciri
    res = apply_filters(candidates, 2)
    if not res: res = apply_filters(candidates, 1)
    if not res: res = apply_filters(candidates, 0)
    
    # Acil Durum (Sadece Fırın Kuralına Bak)
    if not res:
        res = [d for d in candidates if not (oven_banned and safe_str(d.get('PISIRME_EKIPMAN')) == 'FIRIN')]
        if res:
            chosen = random.choice(res).copy()
            if "(ZORUNLU)" not in chosen['YEMEK ADI']:
                chosen['YEMEK ADI'] += " (ZORUNLU)"
            return chosen
        return {"YEMEK ADI": "---", "PISIRME_EKIPMAN": "YOK"}

    never_used = [d for d in res if len(usage_history.get(get_unique_key(d), [])) == 0]
    return random.choice(never_used if never_used else res)

# =========================================================
# 📅 PLANLAMA VE DİĞER FONKSİYONLAR (V5 - STABİL)
# =========================================================

def record_usage(dish, usage_history, day, global_history):
    if not dish or dish.get('YEMEK ADI') == "---": return
    # Kaydederken " (ZORUNLU)" ekini temizle ki limit takibi doğru olsun
    clean_name = safe_str(dish.get('YEMEK ADI')).replace(" (ZORUNLU)", "")
    u_key = f"{safe_str(dish.get('KATEGORİ'))}_{clean_name}"
    
    if u_key not in usage_history: usage_history[u_key] = []
    usage_history[u_key].append(day)
    if safe_str(dish.get('ALT_TUR')) == 'BAKLIYAT': global_history['last_legume'] = day

def generate_menu_v5(month, year, pool, holidays, ready_snack_days_indices, fish_pref, target_meatless_count):
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
            kahv = select_dish_strict(pool, "KAHVALTI EKSTRA", usage_history, curr_date, OVEN_LOCKED, {"exclude_names": daily_exclude})
            record_usage(kahv, usage_history, day, global_history)
            k_str = kahv.get('YEMEK ADI')
            if safe_str(kahv.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        # Hedef Takibi
        days_left = num_days - day + 1
        force_veg = (target_meatless_count - meatless_cnt) >= days_left - 1
        
        # 2. ÖĞLE ANA
        l_cons = {"exclude_names": daily_exclude}
        if day == fish_day: 
            l_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, {"force_fish": True})
        else:
            if force_veg: l_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_cnt >= target_meatless_count: l_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            l_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, l_cons)

        record_usage(l_ana, usage_history, day, global_history)
        if safe_str(l_ana.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True
        if safe_str(l_ana.get('PROTEIN_TURU')) == 'ETSIZ' and day != fish_day: meatless_cnt += 1

        # 3. AKŞAM ANA
        d_cons = {"exclude_names": daily_exclude + [l_ana.get('YEMEK ADI')]}
        if day != fish_day:
            if safe_str(l_ana.get('PROTEIN_TURU')) in ['KIRMIZI', 'BEYAZ']: d_cons['block_protein_list'] = [safe_str(l_ana.get('PROTEIN_TURU'))]
            if force_veg: d_cons['force_protein_types'] = ['ETSIZ']
        else: d_cons['block_protein_list'] = ['BALIK']

        d_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, OVEN_LOCKED, d_cons)
        record_usage(d_ana, usage_history, day, global_history)
        if safe_str(d_ana.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True
        if safe_str(d_ana.get('PROTEIN_TURU')) == 'ETSIZ' and day != fish_day: meatless_cnt += 1

        # 4. YAN ÜRÜNLER (Çorba, Yan, Tamm)
        def build_cons(dishes):
            res = {"exclude_names": daily_exclude}
            res['current_meal_colors'] = [get_dish_meta(d)['renk'] for d in dishes if d]
            res['block_content_tags'] = [get_dish_meta(d)['tag'] for d in dishes if d and get_dish_meta(d)['tag']]
            carbs = [get_dish_meta(d)['alt_tur'] for d in dishes if d]
            if any(c in ['HAMUR', 'PATATES', 'PIRINC', 'BULGUR'] for c in carbs):
                res['block_alt_types'] = ['HAMUR', 'PATATES', 'PIRINC', 'BULGUR']
            if any(get_dish_meta(d)['p_type'] in ['KIRMIZI', 'BEYAZ'] for d in dishes):
                res['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            return res

        soup = select_dish_strict(pool, "ÇORBA", usage_history, curr_date, OVEN_LOCKED, build_cons([l_ana, d_ana]))
        record_usage(soup, usage_history, day, global_history)
        if safe_str(soup.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        side = select_dish_strict(pool, "YAN YEMEK", usage_history, curr_date, OVEN_LOCKED, build_cons([l_ana, d_ana, soup]))
        record_usage(side, usage_history, day, global_history)
        if safe_str(side.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        tamm = select_dish_strict(pool, "TAMAMLAYICI", usage_history, curr_date, OVEN_LOCKED, build_cons([l_ana, d_ana, soup, side]))
        record_usage(tamm, usage_history, day, global_history)

        snack = select_dish_strict(pool, "GECE ATIŞTIRMALIK", usage_history, curr_date, OVEN_LOCKED, 
                                   {"exclude_names": daily_exclude, "force_equipment": 'HAZIR' if w_idx in ready_snack_days_indices else None})
        record_usage(snack, usage_history, day, global_history)

        menu_log.append({
            "TARİH": d_str, "GÜN": w_name, "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": soup.get('YEMEK ADI'), "ÖĞLE ANA": l_ana.get('YEMEK ADI'), "ÖĞLE YAN": side.get('YEMEK ADI'), "ÖĞLE TAMM": tamm.get('YEMEK ADI'),
            "AKŞAM ÇORBA": soup.get('YEMEK ADI'), "AKŞAM ANA": d_ana.get('YEMEK ADI'), "AKŞAM YAN": side.get('YEMEK ADI'), "AKŞAM TAMM": tamm.get('YEMEK ADI'),
            "GECE": f"Çay/Kahve + {snack.get('YEMEK ADI')}"
        })
        prev_dishes = [soup.get('YEMEK ADI'), l_ana.get('YEMEK ADI'), d_ana.get('YEMEK ADI'), side.get('YEMEK ADI'), tamm.get('YEMEK ADI')]

    return pd.DataFrame(menu_log)

def render_page(sel_model):
    st.header("👨‍🍳 Akıllı Menü - LİMİT KORUMALI (v5.0)")
    client = get_gspread_client()
    if not client: st.stop()

    if 'generated_menu' not in st.session_state:
        saved_df = load_last_menu(client)
        if saved_df is not None: st.session_state['generated_menu'] = saved_df

    col1, col2 = st.columns(2)
    with col1:
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        sel_month_idx = st.selectbox("Ay", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=datetime.now().month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)
    with col2:
        h_start = st.date_input("Tatil Başlangıç", value=None)
        h_end = st.date_input("Tatil Bitiş", value=None)
        ready_days = st.multiselect("Hazır Atıştırmalık", options=GUNLER_TR, default=["Pazar", "Pazartesi"])
        
    st.divider()
    c1, c2 = st.columns(2)
    with c1: fish_pref = st.selectbox("Balık Günü", ["Otomatik", "Yok"] + GUNLER_TR)
    with c2: target_meatless = st.slider("Hedef Etsiz Öğün", 0, 30, 12)

    if st.button("🚀 Menü Oluştur", type="primary"):
        pool = get_full_menu_pool(client)
        if pool:
            holidays = [(h_start, h_end)] if h_start and h_end else []
            df_menu = generate_menu_v5(sel_month_idx, sel_year, pool, holidays, [GUNLER_TR.index(d) for d in ready_days], fish_pref, target_meatless)
            if save_menu_to_sheet(client, df_menu):
                st.session_state['generated_menu'] = df_menu
                st.rerun()

    if 'generated_menu' in st.session_state:
        st.divider()
        st.data_editor(st.session_state['generated_menu'], use_container_width=True, height=600)
