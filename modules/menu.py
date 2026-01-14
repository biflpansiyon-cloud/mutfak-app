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
            # Uyarı ekle ki listede belli olsun
        for opt in options:
            opt['YEMEK ADI'] = f"{opt['YEMEK ADI']} (KOTADISI)"

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
