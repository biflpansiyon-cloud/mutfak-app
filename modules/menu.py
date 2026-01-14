import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

# --- GEREKLİ MODÜLLER (Sizin utils dosyanızdan) ---
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
# 🛠️ YARDIMCI VE GÜVENLİK FONKSİYONLARI
# =========================================================

def safe_str(val):
    """Excel'den gelen None/NaN değerlerini güvenli boş stringe çevirir."""
    if val is None: return ""
    s = str(val).strip()
    if s.lower() == 'nan': return ""
    return s

def get_unique_key(dish):
    """Yemek takibi için benzersiz anahtar."""
    cat = safe_str(dish.get('KATEGORİ'))
    name = safe_str(dish.get('YEMEK ADI'))
    return f"{cat}_{name}"

def get_dish_meta(dish):
    """Yemeğin özelliklerini güvenli şekilde çeker."""
    if not dish: return {"tag": "", "alt_tur": "", "renk": "", "equip": "", "p_type": ""}
    
    name = safe_str(dish.get('YEMEK ADI'))
    tag = safe_str(dish.get('ICERIK_TURU'))
    
    # Otomatik Tag
    if not tag and any(k in name.upper() for k in YOGURT_KEYWORDS): tag = "YOGURT"
    
    return {
        "tag": tag,
        "alt_tur": safe_str(dish.get('ALT_TUR')),
        "renk": safe_str(dish.get('RENK')),
        "equip": safe_str(dish.get('PISIRME_EKIPMAN')),
        "p_type": safe_str(dish.get('PROTEIN_TURU'))
    }

# =========================================================
# 🛡️ GELİŞMİŞ SEÇİCİ (DOMINO STRATEJİSİ - ZORUNLU FIX)
# =========================================================

def select_dish_strict(pool, category, usage_history, current_day_obj, 
                       oven_banned=False, 
                       constraints=None, global_history=None):
    
    if constraints is None: constraints = {}
    
    # 1. Havuzdan Kategoriye Göre Adayları Al
    candidates = [d for d in pool if safe_str(d.get('KATEGORİ')) == category]
    
    # Balık Filtresi
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if safe_str(d.get('PROTEIN_TURU')) != 'BALIK']

    # Yasaklı Gün Kontrolü
    day_name = GUNLER_TR[current_day_obj.weekday()]
    candidates = [d for d in candidates if day_name.upper() not in safe_str(d.get('YASAKLI_GUNLER')).upper()]

    # --- FİLTRE MOTORU ---
    def apply_filters(candidate_list, strict_level):
        valid = []
        for dish in candidate_list:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            
            # --- KIRMIZI ÇİZGİ: FIRIN KURALI ---
            if oven_banned and meta['equip'] == 'FIRIN': continue
            if constraints.get('block_equipment') == 'FIRIN' and meta['equip'] == 'FIRIN': continue
            
            # --- Seviye 0: Temel Kurallar ---
            if strict_level >= 0:
                # Limit
                used = usage_history.get(u_key, [])
                if len(used) >= int(dish.get('LIMIT') or 99): continue
                
                # Sıklık (Ara)
                if used and (current_day_obj.day - used[-1]) <= int(dish.get('ARA') or 0): continue
                
                # Protein Bloklama
                if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']: continue
                if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']: continue
                
                # İsim Engelleme
                if constraints.get('exclude_names') and safe_str(dish.get('YEMEK ADI')) in constraints['exclude_names']: continue
                
                # İçerik Çakışması (BOŞLUK HATASI BURADA DÜZELTİLDİ)
                # Sadece meta['tag'] doluysa kontrol et
                if meta['tag'] and constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']: continue

            # --- Seviye 1: Tercihler ---
            if strict_level >= 1:
                # Bakliyat Arası
                if meta['alt_tur'] == 'BAKLIYAT' and global_history:
                    last = global_history.get('last_legume', -99)
                    if (current_day_obj.day - last) < 3: continue
                    
                if constraints.get('force_equipment') and meta['equip'] != constraints['force_equipment']: continue
                if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']: continue

            # --- Seviye 2: Görsel ---
            if strict_level >= 2:
                if constraints.get('current_meal_colors') and meta['renk'] == 'KIRMIZI':
                    if constraints['current_meal_colors'].count('KIRMIZI') >= 2: continue

            valid.append(dish)
        return valid

    # --- DENEME ZİNCİRİ ---
    final_list = apply_filters(candidates, strict_level=2)
    if not final_list: final_list = apply_filters(candidates, strict_level=1)
    if not final_list: final_list = apply_filters(candidates, strict_level=0)
    
    # 4. Hiçbiri olmadıysa -> ZORUNLU (Fırınsız)
    if not final_list:
        emergency_pool = [d for d in candidates if not (oven_banned and safe_str(d.get('PISIRME_EKIPMAN')) == 'FIRIN')]
        if emergency_pool:
            chosen = random.choice(emergency_pool)
            # İsim kirliliğini önlemek için sadece bir kez (ZORUNLU) yaz
            current_name = chosen.get('YEMEK ADI', '')
            if "(ZORUNLU)" not in current_name:
                chosen['YEMEK ADI'] = current_name + " (ZORUNLU)"
            return chosen
        else:
            return {"YEMEK ADI": "---", "PISIRME_EKIPMAN": "YOK", "PROTEIN_TURU": ""}

    never_used = [d for d in final_list if len(usage_history.get(get_unique_key(d), [])) == 0]
    if never_used: return random.choice(never_used)
    
    return random.choice(final_list)

def record_usage(dish, usage_history, day, global_history):
    if not dish or dish.get('YEMEK ADI') == "---": return
    u_key = get_unique_key(dish)
    if u_key not in usage_history: usage_history[u_key] = []
    usage_history[u_key].append(day)
    
    if safe_str(dish.get('ALT_TUR')) == 'BAKLIYAT':
        global_history['last_legume'] = day

# =========================================================
# 📅 ANA PLANLAMA DÖNGÜSÜ
# =========================================================

def generate_menu_v4(month, year, pool, holidays, ready_snack_days_indices, fish_pref, target_meatless_count):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    global_history = {'last_legume': -99}
    
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
        
        # 1. KAHVALTI
        k_str = "-"
        if w_idx in [1, 3, 5, 6]:
            kahv = select_dish_strict(pool, "KAHVALTI EKSTRA", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints={"exclude_names": daily_exclude})
            record_usage(kahv, usage_history, day, global_history)
            k_str = kahv.get('YEMEK ADI')
            if safe_str(kahv.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        # HEDEF YÖNETİMİ
        days_left = num_days - day + 1
        needed = target_meatless_count - meatless_cnt
        force_veg = (needed > 0) and (needed >= days_left - 1)
        
        is_fish = (day == fish_day)
        is_weekend = (w_idx >= 5)

        # 2. ÖĞLE ANA
        lunch_cons = {"exclude_names": daily_exclude}
        if is_fish: 
             lunch_cons['force_protein_types'] = ['BALIK']
             lunch_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints={"force_fish": True})
        else:
             if force_veg: lunch_cons['force_protein_types'] = ['ETSIZ']
             elif meatless_cnt >= target_meatless_count: lunch_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
             lunch_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=lunch_cons)

        record_usage(lunch_ana, usage_history, day, global_history)
        if safe_str(lunch_ana.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True
        
        l_ptype = safe_str(lunch_ana.get('PROTEIN_TURU'))
        if l_ptype == 'ETSIZ' and not is_fish: meatless_cnt += 1

        # 3. AKŞAM ANA
        dinner_cons = {"exclude_names": daily_exclude + [lunch_ana.get('YEMEK ADI')]}
        if not is_fish:
            if l_ptype in ['KIRMIZI', 'BEYAZ']: dinner_cons['block_protein_list'] = [l_ptype]
            if force_veg: dinner_cons['force_protein_types'] = ['ETSIZ']
        else:
            dinner_cons['block_protein_list'] = ['BALIK']

        dinner_ana = select_dish_strict(pool, "ANA YEMEK", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=dinner_cons)
        record_usage(dinner_ana, usage_history, day, global_history)
        if safe_str(dinner_ana.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True
        
        d_ptype = safe_str(dinner_ana.get('PROTEIN_TURU'))
        if d_ptype == 'ETSIZ' and not is_fish: meatless_cnt += 1

        # --- YAN ÜRÜNLER İÇİN CONSTRAINT HAZIRLAYICI ---
        def build_cons(base, dishes):
            # RENK
            colors = [get_dish_meta(d)['renk'] for d in dishes if d]
            base['current_meal_colors'] = colors
            
            # TAG (BOŞ OLANLARI ALMA - KRİTİK DÜZELTME)
            tags = [get_dish_meta(d)['tag'] for d in dishes if d]
            tags = [t for t in tags if t] # Boş stringleri temizle
            base['block_content_tags'] = tags
            
            # KARBONHİDRAT
            carbs = [get_dish_meta(d)['alt_tur'] for d in dishes if d]
            blocked_carbs = []
            for c in carbs:
                if c in ['HAMUR', 'PATATES', 'PIRINC', 'BULGUR']:
                    blocked_carbs.extend(['HAMUR', 'PATATES', 'PIRINC', 'BULGUR'])
            if blocked_carbs: base['block_alt_types'] = list(set(blocked_carbs))
            
            # PROTEIN
            if any(get_dish_meta(d)['p_type'] in ['KIRMIZI', 'BEYAZ'] for d in dishes):
                base['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
                
            return base

        # 4. ÇORBA
        soup_cons = build_cons({"exclude_names": daily_exclude}, [lunch_ana, dinner_ana])
        soup = select_dish_strict(pool, "ÇORBA", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=soup_cons)
        record_usage(soup, usage_history, day, global_history)
        if safe_str(soup.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        # 5. YAN YEMEK
        side_cons = build_cons({"exclude_names": daily_exclude}, [lunch_ana, dinner_ana, soup])
        forced_side = lunch_ana.get('ZORUNLU_YAN') or dinner_ana.get('ZORUNLU_YAN')
        if forced_side:
            side = {"YEMEK ADI": forced_side, "PISIRME_EKIPMAN": "TENCERE", "PROTEIN_TURU": ""}
        else:
            side = select_dish_strict(pool, "YAN YEMEK", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=side_cons)
        
        record_usage(side, usage_history, day, global_history)
        if safe_str(side.get('PISIRME_EKIPMAN')) == 'FIRIN': OVEN_LOCKED = True

        # 6. TAMAMLAYICI
        tamm_cons = build_cons({"exclude_names": daily_exclude}, [lunch_ana, dinner_ana, soup, side])
        tamm = select_dish_strict(pool, "TAMAMLAYICI", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=tamm_cons)
        record_usage(tamm, usage_history, day, global_history)

        # 7. GECE
        snack_cons = {"exclude_names": daily_exclude}
        if w_idx in ready_snack_days_indices: snack_cons['force_equipment'] = 'HAZIR'
        snack = select_dish_strict(pool, "GECE ATIŞTIRMALIK", usage_history, curr_date, oven_banned=OVEN_LOCKED, constraints=snack_cons)
        record_usage(snack, usage_history, day, global_history)

        menu_log.append({
            "TARİH": d_str, "GÜN": w_name, "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": soup.get('YEMEK ADI'), "ÖĞLE ANA": lunch_ana.get('YEMEK ADI'), "ÖĞLE YAN": side.get('YEMEK ADI'), "ÖĞLE TAMM": tamm.get('YEMEK ADI'),
            "AKŞAM ÇORBA": soup.get('YEMEK ADI'), "AKŞAM ANA": dinner_ana.get('YEMEK ADI'), "AKŞAM YAN": side.get('YEMEK ADI'), "AKŞAM TAMM": tamm.get('YEMEK ADI'),
            "GECE": f"Çay/Kahve + {snack.get('YEMEK ADI')}"
        })
        
        prev_dishes = [soup.get('YEMEK ADI'), lunch_ana.get('YEMEK ADI'), dinner_ana.get('YEMEK ADI'), side.get('YEMEK ADI'), tamm.get('YEMEK ADI')]

    return pd.DataFrame(menu_log)

# =========================================================
# 🖥️ ARAYÜZ
# =========================================================
def render_page(sel_model):
    st.header("👨‍🍳 Akıllı Menü - FIRIN KORUMALI (v4.1 Fixed)")
    st.markdown("---")
    
    client = get_gspread_client()
    if not client: st.error("Bağlantı hatası!"); st.stop()

    if 'generated_menu' not in st.session_state:
        with st.spinner("Kayıtlı menü yükleniyor..."):
            saved_df = load_last_menu(client)
            if saved_df is not None and not saved_df.empty:
                st.session_state['generated_menu'] = saved_df

    col1, col2 = st.columns(2)
    with col1:
        tr_aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        current_month = datetime.now().month
        sel_month_idx = st.selectbox("Ay Seçin", list(tr_aylar.keys()), format_func=lambda x: tr_aylar[x], index=current_month-1)
        sel_year = st.number_input("Yıl", value=datetime.now().year)

    with col2:
        st.info("🛠️ **Ayarlar**")
        holiday_start = st.date_input("Tatil Başlangıç", value=None)
        holiday_end = st.date_input("Tatil Bitiş", value=None)
        ready_snack_days = st.multiselect("Hazır Atıştırmalık Günleri", options=GUNLER_TR, default=["Pazar", "Pazartesi"])
        
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        fish_options = ["Otomatik", "Yok"] + GUNLER_TR
        fish_pref = st.selectbox("Balık Günü", fish_options, index=0)
    with c2:
        target_meatless = st.slider("Hedef Etsiz Öğün (Aylık)", 0, 30, 12)

    if st.button("🚀 Menü Oluştur (Sıfırdan)", type="primary"):
        with st.spinner("Domino Algoritması Çalışıyor..."):
            pool = get_full_menu_pool(client)
            if pool:
                holidays = []
                if holiday_start and holiday_end: holidays.append((holiday_start, holiday_end))
                ready_indices = [GUNLER_TR.index(d) for d in ready_snack_days]
                
                df_menu = generate_menu_v4(sel_month_idx, sel_year, pool, holidays, ready_indices, fish_pref, target_meatless)
                
                if save_menu_to_sheet(client, df_menu):
                    st.session_state['generated_menu'] = df_menu
                    st.success("İşlem Tamam. ZORUNLU hatası giderildi! ✅")
                    st.rerun()
                else: st.error("Kaydedilemedi.")

    if 'generated_menu' in st.session_state:
        st.divider()
        st.subheader("📋 Menü Önizleme")
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state['generated_menu'].to_excel(writer, index=False, sheet_name='Menu')
        st.download_button("📥 Excel İndir", data=output.getvalue(), file_name="Menu_Plan.xlsx")
        
        edited = st.data_editor(st.session_state['generated_menu'], num_rows="fixed", use_container_width=True, height=600)
        if st.button("Kaydet"):
            if save_menu_to_sheet(client, edited): st.success("Kaydedildi.")
