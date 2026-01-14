import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
import calendar

# Kendi modüllerinizden importlar
from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            
    MENU_POOL_SHEET_NAME  
)

# --- AYARLAR ---
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

# Otomatik Tanımlamalar - İÇERİK TÜRÜ İÇİN
# Not: Erişte listeden çıkarıldı (Pilav grubuna girmeli)
YOGURT_KEYWORDS = ["YAYLA", "YOĞURT", "DÜĞÜN", "CACIK", "AYRAN", "HAYDARİ", "MANTI"] 

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
# 🧠 YARDIMCI FONKSİYONLAR
# =========================================================

def get_unique_key(dish):
    """
    Yemeği sistemde benzersiz tanımlayan anahtar.
    Örn: 'ANA YEMEK_MENEMEN'
    """
    category = dish.get('KATEGORİ', 'GENEL')
    name = dish.get('YEMEK ADI', 'BILINMIYOR')
    return f"{category}_{name}"

def get_dish_meta(dish):
    """Yemeğin meta verilerini (Tag, Renk, Alt Tür) döndürür."""
    if not dish: return {"tag": "", "alt_tur": "", "renk": ""}
    
    tag = dish.get('ICERIK_TURU', '').strip()
    name_upper = dish.get('YEMEK ADI', '').upper()
    
    # Otomatik Tag Atama (Özellikle Yoğurt grubu için)
    if not tag and any(k in name_upper for k in YOGURT_KEYWORDS): tag = "YOGURT"
    
    return {
        "tag": tag,
        "alt_tur": dish.get('ALT_TUR', '').strip(),
        "renk": dish.get('RENK', '').strip()
    }

# =========================================================
# 🍳 MENÜ ALGORİTMASI (AKILLI SEÇİCİ)
# =========================================================

def select_dish_smart(pool, category, usage_history, current_day_obj, constraints=None, global_history=None, daily_oven_used=False):
    """
    GELİŞTİRİLMİŞ VERSİYON:
    Asla boş dönmez. Kademeli olarak kuralları esnetir.
    Level 2: İdeal (Tüm kurallar)
    Level 1: Görsel/Tercih kurallarını kaldır
    Level 0: Limit ve Sıklık kurallarını kaldır (Yeter ki yemek çıksın)
    Level -1: Fırın kuralını bile yık (Aç kalmaktan iyidir)
    """
    if constraints is None: constraints = {}
    
    # 1. Havuzu Kategoriye Göre Süz
    candidates = [d for d in pool if d.get('KATEGORİ') == category]
    
    # Balık zorlaması yoksa balıkları baştan ele (Burası standart)
    if not constraints.get('force_fish'):
        candidates = [d for d in candidates if d.get('PROTEIN_TURU') != 'BALIK']
    
    # Eğer bu kategoride hiç yemek yoksa (Veri hatası), mecburen boş dön
    if not candidates:
        return {"YEMEK ADI": "---", "KATEGORİ": category, "PISIRME_EKIPMAN": "YOK", "PROTEIN_TURU": ""}

    # Yasaklı gün kontrolü (HARD CONSTRAINT)
    current_day_name_tr = GUNLER_TR[current_day_obj.weekday()]
    
    # --- FİLTRELEME MOTORU ---
    def filter_candidates(candidate_list, strict_level=2):
        valid = []
        for dish in candidate_list:
            unique_key = get_unique_key(dish)
            name = dish['YEMEK ADI']
            meta = get_dish_meta(dish)
            p_type = dish.get('PROTEIN_TURU', '').strip()
            equip = dish.get('PISIRME_EKIPMAN', '').strip()
            
            # --- SEVİYE -1: KIRMIZI ÇİZGİLER (Bunlar bile gerekirse yıkılır) ---
            
            # 1. FIRIN KURALI
            # strict_level -1 ise fırın kuralını önemseme
            if strict_level > -1:
                if daily_oven_used and equip == 'FIRIN': continue
                if constraints.get('block_equipment') == 'FIRIN' and equip == 'FIRIN': continue

            # 2. YASAKLI GÜNLER
            # strict_level -1 ise bunu da önemseme (Çok nadir gerekir)
            if strict_level > -1:
                if dish.get('YASAKLI_GUNLER') and current_day_name_tr.upper() in dish.get('YASAKLI_GUNLER', '').upper(): continue

            # --- SEVİYE 0: TEMEL KURALLAR (Limit, Protein) ---
            if strict_level >= 0:
                # Limit Kontrolü
                used_days = usage_history.get(unique_key, [])
                if len(used_days) >= dish['LIMIT']: continue
                
                # Ara Verme (Frequency)
                if used_days:
                    last_seen = used_days[-1]
                    if (current_day_obj.day - last_seen) <= dish['ARA']: continue
                
                # Bakliyat Arası
                if meta['alt_tur'] == 'BAKLIYAT' and global_history:
                    last_leg = global_history.get('last_legume_day', -99)
                    if (current_day_obj.day - last_leg) < 3: continue
                
                # İsim Engelleme (Bugün çıkan yemek)
                if constraints.get('exclude_names') and name in constraints['exclude_names']: continue

                # Protein Hedefleri
                if constraints.get('block_protein_list') and p_type in constraints['block_protein_list']: continue
                if constraints.get('force_protein_types') and p_type not in constraints['force_protein_types']: continue
                
                # İçerik Çakışması (Yoğurt vb.)
                if constraints.get('block_content_tags') and meta['tag'] and meta['tag'] in constraints['block_content_tags']: continue

            # --- SEVİYE 1: TERCİHLER (Ekipman Tercihi, Karbonhidrat) ---
            if strict_level >= 1:
                if constraints.get('force_equipment') and equip != constraints['force_equipment']: continue
                if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']: continue

            # --- SEVİYE 2: GÖRSEL (Renk) ---
            if strict_level >= 2:
                if constraints.get('current_meal_colors') and meta['renk'] == 'KIRMIZI':
                    if constraints['current_meal_colors'].count('KIRMIZI') >= 2: continue

            valid.append(dish)
        return valid

    # --- STRATEJİK SEÇİM ZİNCİRİ ---
    
    # 1. İdeal Durum (Tüm kurallar aktif)
    options = filter_candidates(candidates, strict_level=2)
    
    # 2. Tercihleri Gevşet (Renk, Karbonhidrat önemli değil)
    if not options:
        options = filter_candidates(candidates, strict_level=1)
        
    # 3. Limitleri Zorla (Kotası dolsa da ver, YETER Kİ FIRIN OLMASIN)
    if not options:
        options = filter_candidates(candidates, strict_level=0)
        # Not: Burada isimleri (!) ile işaretleyebiliriz ama gerek yok, yemek çıksın yeter.
        for opt in options:
            opt['YEMEK ADI'] = f"{opt['YEMEK ADI']} (KOTAAŞIM)"
    # 4. ACİL DURUM (Fırın kuralını bile yık - Aç kalmaktan iyidir)
    if not options:
        options = filter_candidates(candidates, strict_level=-1)
        # Uyarı ekle ki listede belli olsun
        for opt in options:
            opt['YEMEK ADI'] = f"{opt['YEMEK ADI']} (KURALDIŞI)"

    # 5. HALA YOKSA (Veri hatası veya imkansız kısıtlamalar)
    if not options:
        # Rastgele bir tane ver gitsin
        chosen = random.choice(candidates)
        chosen['YEMEK ADI'] = f"{chosen['YEMEK ADI']} (ZORUNLU)"
        return chosen

    # Fırsat Eşitliği
    never_used = [d for d in options if len(usage_history.get(get_unique_key(d), [])) == 0]
    if never_used: return random.choice(never_used)
    
    return random.choice(options)

def record_usage(dish, usage_history, day, global_history):
    if dish['YEMEK ADI'] == "---" or "(!)" in dish['YEMEK ADI']: return

    # ARTIK SADECE İSİM DEĞİL, KATEGORİ+İSİM KAYDEDİYORUZ (Namespace Isolation)
    unique_key = get_unique_key(dish)
    
    if unique_key not in usage_history: usage_history[unique_key] = []
    usage_history[unique_key].append(day)
    
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
        except: pass 

    meatless_main_count = 0 
    previous_day_dishes = [] # İsim bazlı takip (Ardışık gün engelleme için)
    
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        date_str = current_date.strftime("%d.%m.%Y")
        weekday_idx = current_date.weekday()
        weekday_name = GUNLER_TR[weekday_idx]
        
        # Tatil Kontrolü
        if any(h[0] <= current_date.date() <= h[1] for h in holidays):
            menu_log.append({"TARİH": date_str, "GÜN": f"{weekday_name} (TATİL)", "KAHVALTI": "-", "ÖĞLE ANA": "-", "GECE": "-"})
            previous_day_dishes = [] 
            continue

        # GÜNLÜK DEĞİŞKENLER SIFIRLANIYOR
        daily_oven_used = False # Fırın bugün henüz kullanılmadı
        daily_exclude = previous_day_dishes.copy() # Dün çıkan yemekleri bugün yasakla
        
        # --- KAHVALTI ---
        kahvalti_str = "-"
        if weekday_idx in [1, 3, 5, 6]: # Salı, Perşembe, Cts, Pz
            k_cons = {"exclude_names": daily_exclude}
            # Kahvaltıda fırın kullanılabilir mi? Kullanılırsa daily_oven_used True olur.
            kahvalti_ekstra = select_dish_smart(pool, "KAHVALTI EKSTRA", usage_history, current_date, constraints=k_cons, global_history=global_history, daily_oven_used=daily_oven_used)
            record_usage(kahvalti_ekstra, usage_history, day, global_history)
            kahvalti_str = kahvalti_ekstra['YEMEK ADI']
            if kahvalti_ekstra.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
        
        # --- HEDEF ODAKLI ET/ETSİZ KARARI (PROBABILITY ENGINE) ---
        days_remaining = num_days - day + 1
        meatless_needed = target_meatless_count - meatless_main_count
        
        # Panik Modu: Eğer kalan gün sayısı hedefe çok yakınsa, Etsiz zorla.
        force_meatless_now = (meatless_needed > 0) and (meatless_needed >= days_remaining - 1)
        
        # Önceliklendirme Modu: Hedefin gerisindeysek Etsiz ihtimalini artır
        should_prioritize_meatless = False
        if meatless_needed > 0:
            ratio = meatless_needed / days_remaining
            if ratio > 0.5: should_prioritize_meatless = True # Yarıdan fazla etsiz lazım

        is_today_fish = (day == fish_day)
        is_weekend = (weekday_idx >= 5)

        # Yardımcı Fonksiyon: Kısıtlama Oluşturucu
        def build_constraints(base_cons, dish_list_for_colors=[], dish_list_for_carbs=[], dish_list_for_tags=[]):
            colors = [get_dish_meta(d)['renk'] for d in dish_list_for_colors if get_dish_meta(d)['renk']]
            base_cons['current_meal_colors'] = colors
            
            tags = [get_dish_meta(d)['tag'] for d in dish_list_for_tags if get_dish_meta(d)['tag']]
            base_cons['block_content_tags'] = tags
            
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
            
            # Etsiz Hedef Yönetimi
            if force_meatless_now: ana_cons['force_protein_types'] = ['ETSIZ']
            elif should_prioritize_meatless: 
                 # Şans ver ama zorlama (Soft Bias yapılabilir, şimdilik basit tutalım)
                 if random.random() < 0.7: ana_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_main_count >= target_meatless_count: 
                 ana_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ', 'BALIK']

            # ANA YEMEK SEÇİMİ
            ana = select_dish_smart(pool, "ANA YEMEK", usage_history, current_date, ana_cons, global_history, daily_oven_used)
            
            # Seçilen yemeği işle
            ana_p_type = ana.get('PROTEIN_TURU', '').strip()
            if ana_p_type == 'ETSIZ': meatless_main_count += 1
            if ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True # FIRIN KİLİTLENDİ
            
            # Çorba
            side_cons = build_constraints({"exclude_names": daily_exclude}, [ana], [ana], [ana])
            if ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            corba = select_dish_smart(pool, "ÇORBA", usage_history, current_date, side_cons, global_history, daily_oven_used)
            if corba.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Yan Yemek
            yan_cons = build_constraints({"exclude_names": daily_exclude}, [ana, corba], [ana], [ana, corba])
            if ana.get('ZORUNLU_YAN'): yan = {"YEMEK ADI": ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: yan = select_dish_smart(pool, "YAN YEMEK", usage_history, current_date, yan_cons, global_history, daily_oven_used)
            if yan.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Tamamlayıcı
            tamm_cons = build_constraints({"exclude_names": daily_exclude}, [ana, corba, yan], [ana, yan], [ana, corba, yan])
            if ana_p_type in ['KIRMIZI', 'BEYAZ', 'BALIK']: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ana.get('ZORUNLU_TAMM'): tamm = {"YEMEK ADI": ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: tamm = select_dish_smart(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history, daily_oven_used)
            
            ogle_corba = aksam_corba = corba
            ogle_ana = aksam_ana = ana
            ogle_yan = aksam_yan = yan
            ogle_tamm = aksam_tamm = tamm
            
            for d in [corba, ana, yan, tamm]: record_usage(d, usage_history, day, global_history)

        elif is_today_fish:
            # === BALIK GÜNÜ ===
            fish_cands = [d for d in pool if d.get('PROTEIN_TURU') == 'BALIK']
            # Yasaklı gün kontrolü
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
            
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True # FIRIN KİLİTLENDİ
            
            # Akşam Yemeği
            aksam_corba = ogle_corba
            dinner_cons = {"exclude_names": daily_exclude, "block_protein_list": ['BALIK']}
            
            # Akşam için etsiz/etli kararı
            if force_meatless_now: dinner_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_main_count >= target_meatless_count: dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish_smart(pool, "ANA YEMEK", usage_history, current_date, dinner_cons, global_history, daily_oven_used)
            record_usage(aksam_ana, usage_history, day, global_history)
            
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ': meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            aksam_side_cons = build_constraints({"exclude_names": daily_exclude}, [aksam_corba, aksam_ana], [aksam_ana], [aksam_ana])
            if a_p_type in ['KIRMIZI', 'BEYAZ']: aksam_side_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if aksam_ana.get('ZORUNLU_YAN'): aksam_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN']}
            else: aksam_yan = select_dish_smart(pool, "YAN YEMEK", usage_history, current_date, aksam_side_cons, global_history, daily_oven_used)
            record_usage(aksam_yan, usage_history, day, global_history)
            
            if aksam_ana.get('ZORUNLU_TAMM'): aksam_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM']}
            else: 
                tamm_cons = build_constraints({"exclude_names": daily_exclude}, [aksam_corba, aksam_ana, aksam_yan], [aksam_ana, aksam_yan], [aksam_ana, aksam_yan])
                aksam_tamm = select_dish_smart(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history, daily_oven_used)
            record_usage(aksam_tamm, usage_history, day, global_history)

        else:
            # === NORMAL HAFTA İÇİ ===
            lunch_cons = {"exclude_names": daily_exclude}
            
            # Etsiz Yönetimi
            if force_meatless_now: lunch_cons['force_protein_types'] = ['ETSIZ']
            elif should_prioritize_meatless:
                if random.random() < 0.65: lunch_cons['force_protein_types'] = ['ETSIZ']
            elif meatless_main_count >= target_meatless_count: 
                lunch_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            ogle_ana = select_dish_smart(pool, "ANA YEMEK", usage_history, current_date, lunch_cons, global_history, daily_oven_used)
            record_usage(ogle_ana, usage_history, day, global_history)
            
            o_p_type = ogle_ana.get('PROTEIN_TURU', '').strip()
            if o_p_type == 'ETSIZ': meatless_main_count += 1
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True # FIRIN KİLİTLENDİ
            
            # Akşam Ana Yemek
            dinner_cons = {"exclude_names": daily_exclude + [ogle_ana['YEMEK ADI']]}
            
            # Öğlen et yedilerse akşam yemesinler (veya bütçeye göre ayarla)
            if o_p_type in ['KIRMIZI', 'BEYAZ']: 
                dinner_cons['block_protein_list'] = [o_p_type] 
            elif o_p_type == 'ETSIZ' and meatless_main_count >= target_meatless_count:
                dinner_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            
            aksam_ana = select_dish_smart(pool, "ANA YEMEK", usage_history, current_date, dinner_cons, global_history, daily_oven_used)
            record_usage(aksam_ana, usage_history, day, global_history)
            
            a_p_type = aksam_ana.get('PROTEIN_TURU', '').strip()
            if a_p_type == 'ETSIZ': meatless_main_count += 1
            if aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True # FIRIN KİLİTLENDİ (Eğer öğlen kilitlenmediyse)
            
            # ORTAK YAN & ÇORBA
            shared_cons = build_constraints({"exclude_names": daily_exclude}, [ogle_ana, aksam_ana], [ogle_ana, aksam_ana], [ogle_ana, aksam_ana])
            
            is_any_meat = (o_p_type in ['KIRMIZI', 'BEYAZ']) or (a_p_type in ['KIRMIZI', 'BEYAZ'])
            if is_any_meat: shared_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            shared_corba = select_dish_smart(pool, "ÇORBA", usage_history, current_date, shared_cons, global_history, daily_oven_used)
            record_usage(shared_corba, usage_history, day, global_history)
            if shared_corba.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Yan Yemek
            yan_cons = build_constraints({"exclude_names": daily_exclude}, [ogle_ana, aksam_ana, shared_corba], [ogle_ana, aksam_ana], [ogle_ana, aksam_ana])
            
            if ogle_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": ogle_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            elif aksam_ana.get('ZORUNLU_YAN'): shared_yan = {"YEMEK ADI": aksam_ana['ZORUNLU_YAN'], "PISIRME_EKIPMAN": "TENCERE", "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: shared_yan = select_dish_smart(pool, "YAN YEMEK", usage_history, current_date, yan_cons, global_history, daily_oven_used)
            record_usage(shared_yan, usage_history, day, global_history)
            if shared_yan.get('PISIRME_EKIPMAN') == 'FIRIN': daily_oven_used = True
            
            # Tamamlayıcı
            tamm_cons = build_constraints(
                {"exclude_names": daily_exclude}, 
                [ogle_ana, aksam_ana, shared_corba, shared_yan], 
                [ogle_ana, aksam_ana, shared_yan],
                [ogle_ana, aksam_ana, shared_yan] # TAG KONTROLÜ
            )
            if is_any_meat: tamm_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']
            
            if ogle_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": ogle_ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            elif aksam_ana.get('ZORUNLU_TAMM'): shared_tamm = {"YEMEK ADI": aksam_ana['ZORUNLU_TAMM'], "ICERIK_TURU": "", "ALT_TUR": "", "RENK": ""}
            else: shared_tamm = select_dish_smart(pool, "TAMAMLAYICI", usage_history, current_date, tamm_cons, global_history, daily_oven_used)
            record_usage(shared_tamm, usage_history, day, global_history)
            
            ogle_corba = aksam_corba = shared_corba
            ogle_yan = aksam_yan = shared_yan
            ogle_tamm = aksam_tamm = shared_tamm

        # --- GECE ---
        gece_cons = {"exclude_names": daily_exclude}
        if weekday_idx in ready_snack_days_indices: gece_cons['force_equipment'] = 'HAZIR'
        
        # Gece için fırın yasağı (Eğer gün içinde kullanıldıysa)
        gece = select_dish_smart(pool, "GECE ATIŞTIRMALIK", usage_history, current_date, gece_cons, global_history, daily_oven_used)
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
    st.header("👨‍🍳 Akıllı Menü Planlayıcı (v3.0 - Ultimate)")
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
        
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.write("🐟 **Balık Günü Ayarı**")
        fish_options = ["Otomatik", "Yok"] + GUNLER_TR
        fish_pref = st.selectbox("Balık günü tercihi?", fish_options, index=0)
    
    with c2:
        st.write("🥦 **Bütçe & Etsiz Yemek Ayarı**")
        target_meatless = st.slider("Ayda HEDEF kaç öğün etsiz olsun?", 0, 30, 12, help="Sistem bu sayıya ulaşmak için menüyü optimize eder.")

    st.divider()

    if st.button("🚀 Yeni Menü Oluştur", type="primary"):
        with st.spinner("Kurallar işleniyor (Fırın Kontrolü, Bütçe Hesabı, Renk Dengesi)..."):
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
                    fish_pref,
                    target_meatless
                )
                
                if save_menu_to_sheet(client, df_menu):
                    st.session_state['generated_menu'] = df_menu
                    st.success("Menü başarıyla oluşturuldu! ✅")
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
