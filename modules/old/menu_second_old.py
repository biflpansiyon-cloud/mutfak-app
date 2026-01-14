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
# Not: SABIT_KAHVALTI değişkeni artık hücreye yazılmıyor, sadece mantıken var.
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

# Otomatik Tanımlamalar
YOGURT_KEYWORDS = ["YAYLA", "YOĞURT", "DÜĞÜN", "ERİŞTE", "CACIK", "AYRAN", "HAYDARİ", "MANTI", "CACIK"] 

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
        
        random.shuffle(pool) # Fırsat eşitliği için karıştır
        return pool
    except Exception as e:
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 🍳 MENÜ ALGORİTMASI (SEÇİCİ)
# =========================================================

def get_dish_meta(dish):
    """Yemeğin meta verilerini (Tag, Renk, Alt Tür) döndürür."""
    if not dish: return {"tag": "", "alt_tur": "", "renk": ""}
    
    tag = dish.get('ICERIK_TURU', '').strip()
    name_upper = dish.get('YEMEK ADI', '').upper()
    
    # Otomatik Tag Atama (Özellikle Yoğurt grubu için kritik)
    if not tag and any(k in name_upper for k in YOGURT_KEYWORDS): tag = "YOGURT"
    
    return {
        "tag": tag,
        "alt_tur": dish.get('ALT_TUR', '').strip(),
        "renk": dish.get('RENK', '').strip()
    }

def select_dish(pool, category, usage_history, current_day_obj, constraints=None, global_history=None):
    if constraints is None: constraints = {}
    candidates = [d for d in pool if d.get('KATEGORİ') == category]
    
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if d.get('PROTEIN_TURU') != 'BALIK']
    
    current_day_name_tr = GUNLER_TR[current_day_obj.weekday()]
    
    valid_options = []
    for dish in candidates:
        name = dish['YEMEK ADI']
        meta = get_dish_meta(dish)
        p_type = dish.get('PROTEIN_TURU', '').strip()
        banned_days = dish.get('YASAKLI_GUNLER', '').strip()

        # --- 0. YASAKLI GÜN KONTROLÜ ---
        if banned_days and current_day_name_tr.upper() in banned_days.upper(): continue

        # 1. LIMIT ve ARA
        count_used = len(usage_history.get(name, []))
        if count_used >= dish['LIMIT']: continue
        if count_used > 0:
            last_seen_day = usage_history[name][-1]
            if (current_day_obj.day - last_seen_day) <= dish['ARA']: continue
        
        # 2. EKİPMAN
        if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']: continue
        if constraints.get('force_equipment') and dish.get('PISIRME_EKIPMAN') != constraints['force_equipment']: continue

        # 3. PROTEIN
        if constraints.get('block_protein_list') and p_type in constraints['block_protein_list']: continue
        if constraints.get('force_protein_types') and p_type not in constraints['force_protein_types']: continue
        
        # 4. İÇERİK (TAG) KONTROLÜ (Yoğurt-Yoğurt çakışması burada engellenir)
        if constraints.get('block_content_tags') and meta['tag'] and meta['tag'] in constraints['block_content_tags']: continue

        # 5. YASAKLI İSİMLER
        if constraints.get('exclude_names') and name in constraints['exclude_names']: continue
        
        # 6. BAKLİYAT ARDIŞIKLIĞI
        if meta['alt_tur'] == 'BAKLIYAT' and global_history:
            last_legume = global_history.get('last_legume_day', -99)
            if (current_day_obj.day - last_legume) < 3: continue 
            
        # 7. KARBONHİDRAT POLİSİ
        if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']: continue
        
        # 8. RENK DENGESİ
        if constraints.get('current_meal_colors') and meta['renk'] == 'KIRMIZI':
            red_count = constraints['current_meal_colors'].count('KIRMIZI')
            if red_count >= 2: continue

        valid_options.append(dish)
    
    if not valid_options:
        if candidates: 
            chosen = random.choice(candidates)
            chosen['YEMEK ADI'] = f"{chosen['YEMEK ADI']} (!)" 
            return chosen
        return {"YEMEK ADI": "---", "PISIRME_EKIPMAN": "", "PROTEIN_TURU": "", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
    
    # Fırsat Eşitliği
    never_used = [d for d in valid_options if len(usage_history.get(d['YEMEK ADI'], [])) == 0]
    if never_used: chosen = random.choice(never_used)
    else: chosen = random.choice(valid_options)
        
    return chosen

def record_usage(dish, usage_history, day, global_history):
    name = dish['YEMEK ADI'].replace(" (!)", "")
    if name == "---": return
    if name not in usage_history: usage_history[name] = []
    usage_history[name].append(day)
    
    if dish.get('ALT_TUR') == 'BAKLIYAT':
        global_history['last_legume_day'] = day

# =========================================================
# 🧠 ANA ALGORİTMA
# =========================================================

def generate_smart_menu(month, year, pool, holidays, ready_snack_days_indices, fish_pref, target_meatless_count):
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    global_history = {'last_legume_day': -99}
    
    # --- BALIK GÜNÜ BELİRLEME ---
    fish_day = None
    if fish_pref == "Otomatik":
        weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
        if weekdays: fish_day = random.choice(weekdays)
    elif fish_pref != "Yok":
        # Kullanıcı belirli bir gün seçti (Örn: "Cuma")
        try:
            target_weekday_idx = GUNLER_TR.index(fish_pref)
            possible_days = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() == target_weekday_idx]
            if possible_days: 
                # Ayda 1 kez kuralı: O günlerden rastgele birini seç
                fish_day = random.choice(possible_days)
        except:
            pass # Hata olursa balık günü atama

    previous_day_dishes = [] 
    meatless_main_count = 0 
    
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
        kahvalti_str = "-"
        if weekday_idx in [1, 3, 5, 6]: # Salı, Perşembe, Cts, Pz
            kahvalti_ekstra = select_dish(pool, "KAHVALTI EKSTRA", usage_history, current_date, constraints={"exclude_names": previous_day_dishes}, global_history=global_history)
            record_usage(kahvalti_ekstra, usage_history, day, global_history)
            kahvalti_str = kahvalti_ekstra['YEMEK ADI']
        else:
            kahvalti_str = "-" # Standart kahvaltı yazmıyoruz artık
        
        daily_exclude = previous_day_dishes.copy()
        is_today_fish = (day == fish_day)
        is_weekend = (weekday_idx >= 5)
        
        daily_oven_used = False
        
        # Yardımcı Fonksiyon: Kısıtlama Oluşturucu
        def build_constraints(base_cons, dish_list_for_colors=[], dish_list_for_carbs=[], dish_list_for_tags=[]):
            # Renkleri topla
            colors = [get_dish_meta(d)['renk'] for d in dish_list_for_colors if get_dish_meta(d)['renk']]
            base_cons['current_meal_colors'] = colors
            
            # İçerik Türlerini (Tag) topla - Yoğurt çakışması için kritik
            tags = [get_dish_meta(d)['tag'] for d in dish_list_for_tags if get_dish_meta(d)['tag']]
            base_cons['block_content_tags'] = tags
            
            # Karbonhidrat dengesi
            blocked_alts = []
            for d in dish_list_for_carbs:
                alt = get_dish_meta(d)['alt_tur']
                if alt in ['HAMUR', 'PATATES']: 
                    blocked_alts.extend(['HAMUR', 'PIRINC', 'BULGUR', 'PATATES']) 
            if blocked_alts: base_cons['block_alt_types'] = list(set(blocked_alts))
            return base_cons

        if is_weekend:
            # === HAFTA SONU ===
            ana_cons = {"exclude_names": daily_exclude}
            
            # Hedeflenen etsiz sayısına ulaşıldıysa et zorla
            if meatless_main_count >= target_meatless_count: 
                ana_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints=ana_cons, global_history=global_history)
            
            ana_p_type = ana.get('PROTEIN_TURU', '').strip()
            if ana_p_type == 'ETSIZ': meatless_main_count += 1
            if ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Çorba seçimi
            side_cons = build_constraints({"exclude_names": daily_exclude}, [ana], [ana], [ana])
            if daily_oven_used: side_cons['block_equipment'] = 'FIRIN'
            if ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            corba = select_dish(pool, "ÇORBA", usage_history, current_date, constraints=side_cons, global_history=global_history)
            if corba.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Yan Yemek
            yan_cons = build_constraints({"exclude_names": daily_exclude}, [ana, corba], [ana], [ana, corba])
            if daily_oven_used: yan_cons['block_equipment'] = 'FIRIN'
            if ana.get('ZORUNLU_YAN'): yan = {"YEMEK ADI": ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, yan_cons, global_history=global_history)
            if yan.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Tamamlayıcı (Tag listesine yan yemeği de ekliyoruz ki Cacık+Ayran olmasın)
            tamm_cons = build_constraints({"exclude_names": daily_exclude}, [ana, corba, yan], [ana, yan], [ana, corba, yan])
            if ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ana.get('ZORUNLU_TAMM'): tamm = {"YEMEK ADI": ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history=global_history)
            
            ogle_corba = aksam_corba = corba
            ogle_ana = aksam_ana = ana
            ogle_yan = aksam_yan = yan
            ogle_tamm = aksam_tamm = tamm
            
            for d in [corba, ana, yan, tamm]: record_usage(d, usage_history, day, global_history)

        elif is_today_fish:
            # === BALIK GÜNÜ ===
            fish_cands = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            allowed_fish = []
            for f in fish_cands:
                banned = f.get('YASAKLI_GUNLER', '').strip()
                if not banned or weekday_name.upper() not in banned.upper():
                    allowed_fish.append(f)
            
            ogle_corba = {"YEMEK ADI": "Mercimek Çorbası", "ICERIK_TURU": "", "ALT_TUR": "BAKLIYAT", "RENK": "SARI"}
            ogle_ana = random.choice(allowed_fish) if allowed_fish else {"YEMEK ADI": "BALIK YOK", "PROTEIN_TURU": "BALIK"}
            record_usage(ogle_ana, usage_history, day, global_history)
            ogle_yan = {"YEMEK ADI": "Mevsim Salata", "ICERIK_TURU": "SALATA", "ALT_TUR": "SEBZE", "RENK": "YESIL"}
            ogle_tamm = {"YEMEK ADI": "Tahin Helvası", "ICERIK_TURU": "TATLI", "ALT_TUR": "TATLI", "RENK": "KAHVE"}
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Akşam Yemeği (Balık günü akşamı normal menü)
            aksam_corba = ogle_corba
            dinner_cons = {"exclude_names": daily_exclude, "block_protein_list": ['BALIK']}
            if daily_oven_used: dinner_cons['block_equipment'] = 'FIRIN'
            
            # Eğer bütçe hedefi dolduysa et ver, yoksa serbest bırak
            if meatless_main_count >= target_meatless_count: dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_cons, global_history=global_history)
            record_usage(aksam_ana, usage_history, day, global_history)
            
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ': meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            aksam_side_cons = build_constraints({"exclude_names": daily_exclude}, [aksam_corba, aksam_ana], [aksam_ana], [aksam_ana])
            if daily_oven_used: aksam_side_cons['block_equipment'] = 'FIRIN'
            if a_p_type in ['KIRMIZI', 'BEYAZ']: aksam_side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if aksam_ana.get('ZORUNLU_YAN'): aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN']}
            else: aksam_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, aksam_side_cons, global_history=global_history)
            record_usage(aksam_yan, usage_history, day, global_history)
            
            if aksam_ana.get('ZORUNLU_TAMM'): aksam_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM']}
            else: 
                tamm_cons = build_constraints({"exclude_names": daily_exclude}, [aksam_corba, aksam_ana, aksam_yan], [aksam_ana, aksam_yan], [aksam_ana, aksam_yan])
                aksam_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history=global_history)
            record_usage(aksam_tamm, usage_history, day, global_history)

        else:
            # === NORMAL HAFTA İÇİ ===
            lunch_cons = {"exclude_names": daily_exclude}
            
            # Bütçe kontrolü: Hedefe ulaşana kadar serbest, ulaşınca Et zorunlu
            if meatless_main_count >= target_meatless_count: lunch_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            ogle_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, constraints=lunch_cons, global_history=global_history)
            record_usage(ogle_ana, usage_history, day, global_history)
            o_p_type = ogle_ana.get('PROTEIN_TURU', '').strip()
            if o_p_type == 'ETSIZ': meatless_main_count += 1
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Akşam Ana Yemek
            dinner_cons = {"exclude_names": daily_exclude + [ogle_ana['YEMEK ADI']]}
            if daily_oven_used: dinner_cons['block_equipment'] = 'FIRIN'
            
            # Öğlen et yedilerse akşam yemesinler (veya tam tersi) - Ama bütçe kısıtı da önemli
            if o_p_type in ['KIRMIZI', 'BEYAZ']: 
                dinner_cons['block_protein_list'] = [o_p_type] # Öğlen tavuksa akşam köfte olabilir
            elif o_p_type == 'ETSIZ' and meatless_main_count >= target_meatless_count:
                dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish(pool, "ANA YEMEK", usage_history, current_date, dinner_cons, global_history=global_history)
            record_usage(aksam_ana, usage_history, day, global_history)
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ': meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # ORTAK YAN & ÇORBA & TAMAMLAYICI
            shared_cons = build_constraints({"exclude_names": daily_exclude}, [ogle_ana, aksam_ana], [ogle_ana, aksam_ana], [ogle_ana, aksam_ana])
            if daily_oven_used: shared_cons['block_equipment'] = 'FIRIN'
            
            is_any_meat = (o_p_type in ['KIRMIZI', 'BEYAZ']) or (a_p_type in ['KIRMIZI', 'BEYAZ'])
            if is_any_meat: shared_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            shared_corba = select_dish(pool, "ÇORBA", usage_history, current_date, shared_cons, global_history=global_history)
            record_usage(shared_corba, usage_history, day, global_history)
            if shared_corba.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            yan_cons = build_constraints({"exclude_names": daily_exclude}, [ogle_ana, aksam_ana, shared_corba], [ogle_ana, aksam_ana], [ogle_ana, aksam_ana])
            if daily_oven_used: yan_cons['block_equipment'] = 'FIRIN'
            
            if ogle_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            elif aksam_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: shared_yan = select_dish(pool, "YAN YEMEK", usage_history, current_date, yan_cons, global_history=global_history)
            record_usage(shared_yan, usage_history, day, global_history)
            if shared_yan.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Tamamlayıcı seçerken Yan yemeğin Tag'ini de blokla (YOĞURT KONTROLÜ)
            tamm_cons = build_constraints(
                {"exclude_names": daily_exclude}, 
                [ogle_ana, aksam_ana, shared_corba, shared_yan], 
                [ogle_ana, aksam_ana, shared_yan],
                [ogle_ana, aksam_ana, shared_yan] # TAG KONTROLÜ İÇİN LİSTE
            )
            if is_any_meat: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ogle_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": ogle_ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            elif aksam_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: shared_tamm = select_dish(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history=global_history)
            record_usage(shared_tamm, usage_history, day, global_history)
            
            ogle_corba = aksam_corba = shared_corba
            ogle_yan = aksam_yan = shared_yan
            ogle_tamm = aksam_tamm = shared_tamm

        # --- GECE ---
        gece_cons = {"exclude_names": daily_exclude}
        if weekday_idx in ready_snack_days_indices: gece_cons['force_equipment'] = 'HAZIR'
        if daily_oven_used: gece_cons['block_equipment'] = 'FIRIN' 
        
        gece = select_dish(pool, "GECE ATIŞTIRMALIK", usage_history, current_date, gece_cons, global_history=global_history)
        record_usage(gece, usage_history, day, global_history)

        # KAYIT
        menu_log.append({
            "TARİH": date_str, "GÜN": weekday_name, "KAHVALTI": kahvalti_str,
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
    st.header("👨‍🍳 Akıllı Menü Planlayıcı (v2.0)")
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
        ready_snack_days = st.multiselect("Gece 'HAZIR' Atıştırmalık", options=GUNLER_TR, default=["Pazar", "Pazartesi"])
        
    # --- YENİ KONTROLLER ---
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.write("🐟 **Balık Günü Ayarı**")
        fish_options = ["Otomatik", "Yok"] + GUNLER_TR
        fish_pref = st.selectbox("Balık günü hangi gün olsun?", fish_options, index=0, help="Otomatik: Rastgele bir gün. Yok: Hiç çıkmaz. Gün Seçimi: Ayda 1 kez o güne koyar.")
    
    with c2:
        st.write("🥦 **Bütçe & Etsiz Yemek Ayarı**")
        target_meatless = st.slider("Ayda HEDEF kaç öğün etsiz (sebze/bakliyat) olsun?", min_value=0, max_value=30, value=12, help="Bu sayıya ulaşana kadar sistem daha ekonomik menüler oluşturur.")

    st.divider()

    if st.button("🚀 Yeni Menü Oluştur", type="primary"):
        with st.spinner("Kurallar işleniyor..."):
            pool = get_full_menu_pool(client)
            if pool:
                holidays = []
                if holiday_start and holiday_end: holidays.append((holiday_start, holiday_end))
                ready_indices = [GUNLER_TR.index(d) for d in ready_snack_days]
                
                df_menu = generate_smart_menu(
                    sel_month_idx, 
                    sel_year, 
                    pool, 
                    holidays, 
                    ready_indices,
                    fish_pref,        # Yeni parametre
                    target_meatless   # Yeni parametre
                )
                
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
