import streamlit as st
import pandas as pd
from datetime import datetime
import random
import calendar
import io
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

# --- MODÜL IMPORTLARI ---
from modules.utils import (
    get_gspread_client, 
    FILE_MENU,            
    MENU_POOL_SHEET_NAME  
)

# --- AYARLAR VE SABİTLER ---
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

# Veritabanı Sütun İsimleri
COL_KATEGORI = 'KATEGORİ'
COL_YEMEK_ADI = 'YEMEK ADI'
COL_LIMIT = 'LIMIT'
COL_ARA = 'ARA'
COL_YASAKLI_GUNLER = 'YASAKLI_GUNLER'

# Meta Veri Sütun İsimleri
COL_ICERIK_TURU = 'ICERIK_TURU'
COL_ALT_TUR = 'ALT_TUR'
COL_RENK = 'RENK'
COL_PISIRME_EKIPMAN = 'PISIRME_EKIPMAN'
COL_PROTEIN_TURU = 'PROTEIN_TURU'
COL_TAT_PROFILI = 'TAT_PROFILI'
COL_DOKU = 'DOKU'
COL_GURME_PUAN = 'GURME_PUAN'
COL_EN_YAKISAN_YAN = 'EN_YAKISAN_YAN'

# Değer Sabitleri
VAL_FIRIN = 'FIRIN'
VAL_HAZIR = 'HAZIR'
VAL_BALIK = 'BALIK'
VAL_ETSIZ = 'ETSİZ'
VAL_KIRMIZI = 'KIRMIZI'
VAL_BEYAZ = 'BEYAZ'
VAL_BAKLIYAT = 'BAKLIYAT'
VAL_SULU = 'SULU'
VAL_KURU = 'KURU'
VAL_SALCALI = 'SALÇALI'
VAL_SADE = 'SADE'
VAL_KREMALI = 'KREMALI'

VAL_ZORUNLU_SUFFIX = " (ZORUNLU)"

# =========================================================
# 🛠️ TEMEL YARDIMCI FONKSİYONLAR
# =========================================================

def safe_str(val) -> str:
    """Güvenli string dönüşümü"""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() == 'nan' else s

def clean_dish_name(name: str) -> str:
    """İsimdeki zorunlu takısını temizler"""
    return name.replace(VAL_ZORUNLU_SUFFIX, "").strip()

def get_unique_key(dish: Dict) -> str:
    """Yemek için benzersiz anahtar"""
    cat = safe_str(dish.get(COL_KATEGORI))
    name = clean_dish_name(safe_str(dish.get(COL_YEMEK_ADI)))
    return f"{cat}_{name}"

# Cache decorator yerine basit bir dictionary caching mekanizması
# Streamlit reload'larında state korunması için session_state veya global kullanılabilir ama
# bu modül her çalıştığında yeniden yükleneceği için sınıf içinde cache tutmak daha güvenli.
class MetaCache:
    _cache = {}

    @classmethod
    def get_meta(cls, dish: Dict) -> Dict:
        # Dictionary hashable olmadığı için unique key kullanıyoruz
        u_key = get_unique_key(dish)
        if u_key in cls._cache:
            return cls._cache[u_key]

        meta = cls._extract_meta(dish)
        cls._cache[u_key] = meta
        return meta

    @staticmethod
    def _extract_meta(dish: Dict) -> Dict:
        """Yemeğin tüm meta bilgilerini çıkar"""
        if not dish:
            return {
                "tag": "", "alt_tur": "", "renk": "", "equip": "",
                "p_type": "", "tat": "", "doku": "", "puan": 5, "yakisan": ""
            }

        try:
            puan = float(dish.get(COL_GURME_PUAN) or 5)
        except:
            puan = 5

        return {
            "tag": safe_str(dish.get(COL_ICERIK_TURU)),
            "alt_tur": safe_str(dish.get(COL_ALT_TUR)),
            "renk": safe_str(dish.get(COL_RENK)),
            "equip": safe_str(dish.get(COL_PISIRME_EKIPMAN)),
            "p_type": safe_str(dish.get(COL_PROTEIN_TURU)),
            "tat": safe_str(dish.get(COL_TAT_PROFILI)),
            "doku": safe_str(dish.get(COL_DOKU)),
            "puan": puan,
            "yakisan": safe_str(dish.get(COL_EN_YAKISAN_YAN))
        }

def get_dish_meta(dish: Dict) -> Dict:
    """Wrapper for backward compatibility and caching"""
    return MetaCache.get_meta(dish)

# =========================================================
# 💾 VERİTABANI İŞLEMLERİ
# =========================================================

def save_menu_to_sheet(client, df):
    """Menüyü Google Sheets'e kaydet"""
    try:
        sh = client.open(FILE_MENU)
        try:
            ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        except:
            ws = sh.add_worksheet(ACTIVE_MENU_SHEET_NAME, 100, 20)
        ws.clear()
        ws.update([df.columns.values.tolist()] + df.astype(str).values.tolist())
        return True
    except Exception as e:
        st.error(f"Kaydetme Hatası: {e}")
        return False

def load_last_menu(client):
    """Son kaydedilen menüyü yükle"""
    try:
        sh = client.open(FILE_MENU)
        ws = sh.worksheet(ACTIVE_MENU_SHEET_NAME)
        data = ws.get_all_records()
        if data:
            return pd.DataFrame(data)
        return None
    except:
        return None

def get_full_menu_pool(client):
    """Yemek havuzunu Google Sheets'ten oku"""
    try:
        sh = client.open(FILE_MENU)
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        data = ws.get_all_values()
        if not data:
            return []
        
        header = [h.strip().upper() for h in data[0]]
        pool = []
        
        for row in data[1:]:
            item = {}
            while len(row) < len(header):
                row.append("")
            
            for i, col_name in enumerate(header):
                item[col_name] = row[i].strip()
            
            # Limit 0 olanları baştan ele (Gurme kuralı: 0 limitli yemek yoktur)
            try:
                l_val = float(item.get(COL_LIMIT, 99) or 99)
                if l_val > 0:
                    pool.append(item)
            except:
                pool.append(item)
        
        return pool
    except Exception as e:
        st.error(f"Havuz Okuma Hatası: {e}")
        return []

# =========================================================
# 📊 HAVUZ ANALİZÖRÜ - Seçim Stratejisi Belirler
# =========================================================

class PoolAnalyzer:
    """Havuzdaki yemekleri analiz edip seçim stratejisi oluşturur"""
    
    def __init__(self, pool: List[Dict]):
        self.pool = pool
        self.stats = self._analyze_pool()
    
    def _analyze_pool(self) -> Dict:
        """Havuzu kategorilere göre analiz et"""
        stats = defaultdict(lambda: {
            'total': 0,
            'by_protein': defaultdict(int),
            'by_equipment': defaultdict(int),
            'by_texture': defaultdict(int),
            'by_flavor': defaultdict(int),
            'by_color': defaultdict(int),
            'by_alt_type': defaultdict(int),
            'available_dishes': []
        })
        
        for dish in self.pool:
            cat = safe_str(dish.get(COL_KATEGORI))
            if not cat:
                continue
                
            stats[cat]['total'] += 1
            stats[cat]['available_dishes'].append(dish)
            
            # Alt kategorileri say
            stats[cat]['by_protein'][safe_str(dish.get(COL_PROTEIN_TURU))] += 1
            stats[cat]['by_equipment'][safe_str(dish.get(COL_PISIRME_EKIPMAN))] += 1
            stats[cat]['by_texture'][safe_str(dish.get(COL_DOKU))] += 1
            stats[cat]['by_flavor'][safe_str(dish.get(COL_TAT_PROFILI))] += 1
            stats[cat]['by_color'][safe_str(dish.get(COL_RENK))] += 1
            stats[cat]['by_alt_type'][safe_str(dish.get(COL_ALT_TUR))] += 1
        
        return dict(stats)
    
    def get_category_info(self, category: str) -> Dict:
        """Kategori hakkında bilgi döndür"""
        return self.stats.get(category, {'total': 0})

# =========================================================
# 🎯 CONSTRAINT YÖNETİCİSİ - Akıllı Gevşetme
# =========================================================

class ConstraintManager:
    """Constraint'leri katmanlı ve akıllı şekilde yönetir"""
    
    def __init__(self):
        pass
    
    def build_progressive_filters(self, base_constraints: Dict) -> List[Dict]:
        """
        Constraint'leri 4 farklı sıkılık seviyesiyle döndür
        Level 4: Full Gourmet (tüm kurallar aktif)
        Level 3: Estetik gevşetilmiş
        Level 2: Beslenme dengesi hafifletilmiş
        Level 1: Sadece hard limitler
        """
        levels = []
        
        # Level 4: TAM GURME - Tüm kurallar
        levels.append(base_constraints.copy())
        
        # Level 3: ESTETİK GEVŞETME
        level3 = base_constraints.copy()
        for key in ['color_balance', 'texture_diversity', 'flavor_diversity', 'perfect_match']:
            level3.pop(key, None)
        levels.append(level3)
        
        # Level 2: BESLENME GEVŞETME
        level2 = level3.copy()
        for key in ['block_alt_types', 'carb_balance']:
            level2.pop(key, None)
        levels.append(level2)
        
        # Level 1: SADECE HARD LIMITS
        level1 = {
            'oven_banned': base_constraints.get('oven_banned', False),
            'exclude_names': base_constraints.get('exclude_names', []),
            'day_bans': base_constraints.get('day_bans', ''),
        }
        levels.append(level1)
        
        return levels

# =========================================================
# 🎨 GURME SKORLAYICI - Detaylı Puanlama
# =========================================================

class GourmetScorer:
    """Yemekleri gurme kriterlerine göre skorlar"""
    
    def __init__(self):
        # Bonus puanlar
        self.PERFECT_MATCH_BONUS = 50
        self.TEXTURE_HARMONY_BONUS = 15
        self.FLAVOR_CONTRAST_BONUS = 10
        self.COLOR_BALANCE_BONUS = 10
        self.FRESHNESS_BONUS = 5  # Son kullanımdan bu yana geçen gün başına
        
        # Penalty puanlar
        self.TEXTURE_CLASH_PENALTY = 10
        self.FLAVOR_CLASH_PENALTY = 15
        self.COLOR_OVERLOAD_PENALTY = 8
        self.OVERUSED_PENALTY = 20
    
    def score_dish(self, dish: Dict, meta: Dict, context: Dict) -> float:
        """
        Context içinde şunlar olabilir:
        - meal_textures: Öğündeki diğer dokuları
        - meal_flavors: Öğündeki diğer tatları
        - meal_colors: Öğündeki diğer renkleri
        - perfect_match_name: İdeal yan yemek/tamamlayıcı
        - usage_days: Bu yemek kaç gün önce kullanılmış
        - total_usage: Bu ay kaç kez kullanılmış
        """
        base_score = meta.get('puan', 5)
        score = float(base_score)
        
        # 1. PERFECT MATCH bonusu
        if context.get('perfect_match_name'):
            dish_name = clean_dish_name(safe_str(dish.get(COL_YEMEK_ADI)))
            if dish_name.upper() in context['perfect_match_name'].upper():
                score += self.PERFECT_MATCH_BONUS
        
        # 2. DOKU UYUMU
        meal_textures = context.get('meal_textures', [])
        dish_texture = meta.get('doku', '')
        
        if dish_texture and meal_textures:
            # Sulu + Kuru = Harmoni
            if VAL_SULU in meal_textures and dish_texture == VAL_KURU:
                score += self.TEXTURE_HARMONY_BONUS
            # Aynı doku çok fazla = Penalty
            elif dish_texture in meal_textures:
                score -= self.TEXTURE_CLASH_PENALTY
        
        # 3. TAT UYUMU
        meal_flavors = context.get('meal_flavors', [])
        dish_flavor = meta.get('tat', '')
        
        if dish_flavor and meal_flavors:
            # Aynı tat = Monotonluk
            if dish_flavor in meal_flavors:
                score -= self.FLAVOR_CLASH_PENALTY
            # Salçalı + Sade/Kremalı = İyi kontrast
            elif VAL_SALCALI in meal_flavors and dish_flavor in [VAL_SADE, VAL_KREMALI]:
                score += self.FLAVOR_CONTRAST_BONUS
        
        # 4. RENK DENGESİ
        meal_colors = context.get('meal_colors', [])
        dish_color = meta.get('renk', '')
        
        if dish_color == VAL_KIRMIZI and meal_colors:
            red_count = meal_colors.count(VAL_KIRMIZI)
            if red_count >= 2:
                score -= self.COLOR_OVERLOAD_PENALTY * red_count
            elif red_count == 0:
                score += self.COLOR_BALANCE_BONUS
        
        # 5. TÜKENMİŞLİK CEZASI
        total_usage = context.get('total_usage', 0)
        if total_usage >= 3:
            score -= self.OVERUSED_PENALTY * (total_usage - 2)
        
        # 6. YENİLİK BONUSU
        usage_days = context.get('usage_days', [])
        if usage_days:
            days_since = context.get('current_day', 1) - usage_days[-1]
            score += min(days_since * self.FRESHNESS_BONUS, 30)  # Max 30 bonus
        
        return max(score, 0)  # Negatif olmaz

# =========================================================
# 🎯 ANA SEÇİM MOTORU
# =========================================================

class DishSelector:
    """Yemek seçim motorunun ana sınıfı"""
    
    def __init__(self, pool: List[Dict], analyzer: PoolAnalyzer):
        self.pool = pool
        self.analyzer = analyzer
        self.constraint_mgr = ConstraintManager()
        self.scorer = GourmetScorer()
    
    def select_dish(
        self,
        category: str,
        usage_history: Dict,
        current_day_obj: datetime,
        base_constraints: Dict,
        score_context: Dict = None
    ) -> Dict:
        """
        Ana yemek seçim fonksiyonu
        """
        if score_context is None:
            score_context = {}
        
        current_day = current_day_obj.day
        day_name = GUNLER_TR[current_day_obj.weekday()]
        
        # 1. Havuzdan kategoriyi filtrele
        candidates = [d for d in self.pool if safe_str(d.get(COL_KATEGORI)) == category]
        
        if not candidates:
            return {COL_YEMEK_ADI: "---", COL_KATEGORI: category}
        
        # 2. Gün yasağını uygula (her seviyede)
        candidates = [
            d for d in candidates 
            if day_name.upper() not in safe_str(d.get(COL_YASAKLI_GUNLER)).upper()
        ]
        
        if not candidates:
            return {COL_YEMEK_ADI: "--- (GÜN YASAĞI)", COL_KATEGORI: category}
        
        # 3. Progressive filtering: 4 seviye dene
        filter_levels = self.constraint_mgr.build_progressive_filters(base_constraints)
        
        best_candidates = []
        used_level = -1
        
        for level_idx, constraints in enumerate(filter_levels):
            filtered = self._apply_constraints(
                candidates, 
                constraints, 
                usage_history, 
                current_day
            )
            
            if filtered:
                best_candidates = filtered
                used_level = 4 - level_idx  # 4=Full, 3=Estetik gevşetilmiş, ...
                break
        
        # 4. Hiç sonuç çıkmadıysa acil durum
        if not best_candidates:
            emergency = self._emergency_selection(candidates, base_constraints)
            if emergency:
                name = safe_str(emergency.get(COL_YEMEK_ADI))
                if VAL_ZORUNLU_SUFFIX not in name:
                    emergency[COL_YEMEK_ADI] = f"{name}{VAL_ZORUNLU_SUFFIX}"
            return emergency or {COL_YEMEK_ADI: "---", COL_KATEGORI: category}
        
        # 5. Skorla ve en iyileri seç
        scored = []
        for dish in best_candidates:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            
            # Scoring context'i hazırla
            context = score_context.copy()
            context['usage_days'] = usage_history.get(u_key, [])
            context['total_usage'] = len(usage_history.get(u_key, []))
            context['current_day'] = current_day
            
            score = self.scorer.score_dish(dish, meta, context)
            scored.append((dish, score, used_level))
        
        # 6. Puanı yüksek olanlardan rastgele seç (çeşitlilik için)
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Top 3 arasından seç
        top_n = min(3, len(scored))
        finalists = [s[0] for s in scored[:top_n]]
        
        selected = random.choice(finalists)
        
        # Eğer Level 1-2'de seçildiyse (zorunlu), işaretle
        if used_level <= 2:
            selected_copy = selected.copy()
            name = safe_str(selected_copy.get(COL_YEMEK_ADI))
            if VAL_ZORUNLU_SUFFIX not in name:
                selected_copy[COL_YEMEK_ADI] = f"{name}{VAL_ZORUNLU_SUFFIX}"
            return selected_copy
        
        return selected
    
    def _apply_constraints(
        self, 
        candidates: List[Dict], 
        constraints: Dict, 
        usage_history: Dict, 
        current_day: int
    ) -> List[Dict]:
        """Constraint'leri uygula"""
        filtered = []
        
        for dish in candidates:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            name = clean_dish_name(safe_str(dish.get(COL_YEMEK_ADI)))
            
            # === HARD CONSTRAINTS (asla gevşemez) ===
            
            # 1. Fırın yasağı
            if constraints.get('oven_banned') and meta['equip'] == VAL_FIRIN:
                continue
            
            # 2. Limit aşımı
            used_days = usage_history.get(u_key, [])
            try:
                limit_val = int(float(dish.get(COL_LIMIT) or 99))
            except:
                limit_val = 99
            
            if len(used_days) >= limit_val:
                continue
            
            # 3. Ara kuralı
            try:
                ara_val = int(float(dish.get(COL_ARA) or 0))
            except:
                ara_val = 0
            
            if used_days and (current_day - used_days[-1]) <= ara_val:
                continue
            
            # 4. İsim hariç tutma
            if constraints.get('exclude_names') and name in constraints['exclude_names']:
                continue
            
            # === SOFT CONSTRAINTS (seviyelere göre gevşer) ===
            
            # Balık kısıtı
            if constraints.get('force_fish'):
                if meta['p_type'] != VAL_BALIK:
                    continue
            
            # Protein kısıtları
            if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']:
                continue
            
            if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']:
                continue
            
            # İçerik çakışması
            if constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']:
                continue
            
            # Karbonhidrat dengesi
            if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']:
                continue
            
            # Bakliyat aralığı
            if constraints.get('legume_interval'):
                if meta['alt_tur'] == VAL_BAKLIYAT:
                    last_legume = constraints.get('last_legume_day', -99)
                    if (current_day - last_legume) < 3:
                        continue
            
            # Renk dengesi (sadece level 4)
            if constraints.get('color_balance'):
                current_colors = constraints.get('current_meal_colors', [])
                if meta['renk'] == VAL_KIRMIZI and current_colors.count(VAL_KIRMIZI) >= 2:
                    continue
            
            # Ekipman kısıtı (hazır atıştırmalık için)
            if constraints.get('force_equipment'):
                if meta['equip'] != constraints['force_equipment']:
                    continue
            
            # Geçti, adaya ekle
            filtered.append(dish)
        
        return filtered
    
    def _emergency_selection(self, candidates: List[Dict], constraints: Dict) -> Optional[Dict]:
        """
        Hiçbir şey bulunamadıysa en az kısıtlı seçimi yap
        Sadece fırın yasağına bak
        """
        if constraints.get('oven_banned'):
            non_oven = [d for d in candidates if safe_str(d.get(COL_PISIRME_EKIPMAN)) != VAL_FIRIN]
            if non_oven:
                return random.choice(non_oven)
        
        # En son çare: rastgele seç
        return random.choice(candidates) if candidates else None

# =========================================================
# 📝 KULLANIM KAYDI
# =========================================================

def record_usage(dish: Dict, usage_history: Dict, day: int, global_history: Dict):
    """Yemeğin kullanımını kaydet"""
    if not dish or dish.get(COL_YEMEK_ADI) in ["---", "--- (GÜN YASAĞI)"]:
        return
    
    u_key = get_unique_key(dish)
    if u_key not in usage_history:
        usage_history[u_key] = []
    usage_history[u_key].append(day)
    
    # Bakliyat kaydı
    meta = get_dish_meta(dish)
    if meta['alt_tur'] == VAL_BAKLIYAT:
        global_history['last_legume'] = day

# =========================================================
# 📅 GURME PLANLAMA DÖNGÜSÜ
# =========================================================

class DailyContext:
    """Günlük kısıtları yöneten yardımcı sınıf"""
    def __init__(self):
        self.oven_locked = False
        self.daily_exclude = []

    def lock_oven(self):
        self.oven_locked = True

    def add_exclusion(self, dishes: List[Dict]):
        for d in dishes:
            self.daily_exclude.append(safe_str(d.get(COL_YEMEK_ADI)))

def generate_gourmet_menu(month, year, pool, holidays, ready_snack_indices, fish_pref, target_meatless):
    """Ana menü oluşturma fonksiyonu"""
    
    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {} 
    global_history = {'last_legume': -99}
    
    # Havuz analizi
    analyzer = PoolAnalyzer(pool)
    selector = DishSelector(pool, analyzer)
    
    # Balık Günü Ayarı
    fish_day = None
    if fish_pref == "Otomatik":
        weekdays = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() < 5]
        if weekdays:
            fish_day = random.choice(weekdays)
    elif fish_pref != "Yok":
        try:
            t_idx = GUNLER_TR.index(fish_pref)
            possible = [d for d in range(1, num_days + 1) if datetime(year, month, d).weekday() == t_idx]
            if possible:
                fish_day = random.choice(possible)
        except:
            pass
    
    meatless_cnt = 0
    prev_dishes = []
    
    for day in range(1, num_days + 1):
        curr_date = datetime(year, month, day)
        d_str = curr_date.strftime("%d.%m.%Y")
        w_idx = curr_date.weekday()
        w_name = GUNLER_TR[w_idx]
        
        # Tatil kontrolü
        if any(h[0] <= curr_date.date() <= h[1] for h in holidays):
            menu_log.append({
                "TARİH": d_str,
                "GÜN": f"{w_name} (TATİL)",
                "KAHVALTI": "-",
                "ÖĞLE ÇORBA": "-",
                "ÖĞLE ANA": "-",
                "ÖĞLE YAN": "-",
                "ÖĞLE TAMM": "-",
                "AKŞAM ÇORBA": "-",
                "AKŞAM ANA": "-",
                "AKŞAM YAN": "-",
                "AKŞAM TAMM": "-",
                "GECE": "-"
            })
            prev_dishes = []
            continue
        
        # Günlük Context
        daily_ctx = DailyContext()
        daily_ctx.daily_exclude = prev_dishes.copy()
        
        # 1. KAHVALTI
        k_str = "-"
        if w_idx in [1, 3, 5, 6]:  # Salı, Perşembe, Cumartesi, Pazar
            kahv = selector.select_dish(
                category="KAHVALTI EKSTRA",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints={
                    'oven_banned': daily_ctx.oven_locked,
                    'exclude_names': daily_ctx.daily_exclude
                }
            )
            record_usage(kahv, usage_history, day, global_history)
            k_str = safe_str(kahv.get(COL_YEMEK_ADI))
            
            if get_dish_meta(kahv)['equip'] == VAL_FIRIN:
                daily_ctx.lock_oven()
        
        # Hedef Takibi
        days_left = num_days - day + 1
        force_veg = (target_meatless - meatless_cnt) >= days_left - 1
        
        # Ana öğün planla
        def plan_meal_set(is_fish_meal=False):
            nonlocal meatless_cnt
            
            # Ana Yemek Seç
            a_cons = {'oven_banned': daily_ctx.oven_locked, 'exclude_names': daily_ctx.daily_exclude}
            
            if is_fish_meal:
                a_cons['force_fish'] = True
            elif force_veg:
                a_cons['force_protein_types'] = [VAL_ETSIZ]
            elif meatless_cnt >= target_meatless:
                a_cons['force_protein_types'] = [VAL_KIRMIZI, VAL_BEYAZ]
            
            ana = selector.select_dish(
                category="ANA YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=a_cons
            )
            record_usage(ana, usage_history, day, global_history)
            
            a_m = get_dish_meta(ana)
            if a_m['equip'] == VAL_FIRIN:
                daily_ctx.lock_oven()

            if a_m['p_type'] == VAL_ETSIZ and not is_fish_meal:
                meatless_cnt += 1
            
            # Ortak Context (Skorlama için)
            meal_context = {
                'perfect_match_name': a_m['yakisan'],
                'meal_textures': [a_m['doku']],
                'meal_flavors': [a_m['tat']],
                'current_meal_colors': [a_m['renk']],
            }
            
            # Ortak Constraint'ler
            meal_cons = {
                'oven_banned': daily_ctx.oven_locked,
                'exclude_names': daily_ctx.daily_exclude + [safe_str(ana.get(COL_YEMEK_ADI))],
                'block_content_tags': [a_m['tag']] if a_m['tag'] else [],
                'legume_interval': True,
                'last_legume_day': global_history.get('last_legume', -99),
                'color_balance': True,
                'current_meal_colors': [a_m['renk']]
            }
            
            # Karbonhidrat dengesi
            if a_m['alt_tur'] in ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']:
                meal_cons['block_alt_types'] = ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']
            
            # Protein çakışması
            if a_m['p_type'] in [VAL_KIRMIZI, VAL_BEYAZ]:
                meal_cons['block_protein_list'] = [VAL_KIRMIZI, VAL_BEYAZ, VAL_BALIK]
            
            # Çorba
            corba = selector.select_dish(
                category="ÇORBA",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(corba, usage_history, day, global_history)
            
            if get_dish_meta(corba)['equip'] == VAL_FIRIN:
                daily_ctx.lock_oven()
            
            # Yan Yemek
            side = selector.select_dish(
                category="YAN YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(side, usage_history, day, global_history)
            
            if get_dish_meta(side)['equip'] == VAL_FIRIN:
                daily_ctx.lock_oven()
            
            # Tamamlayıcı
            tamm = selector.select_dish(
                category="TAMAMLAYICI",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(tamm, usage_history, day, global_history)
            
            return corba, ana, side, tamm
        
        # Hafta İçi / Sonu Ayrımı
        if w_idx >= 5:  # Hafta sonu - öğle ve akşam aynı
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set()

            # BUG FIX: Hafta sonu akşam yemeği de kullanımdan düşmeli
            # Akşam öğünü öğle ile aynı
            a_corba, a_ana, a_yan, a_tamm = o_corba, o_ana, o_yan, o_tamm
            
            # Kullanım sayılarını tekrar işle
            record_usage(a_corba, usage_history, day, global_history)
            record_usage(a_ana, usage_history, day, global_history)
            record_usage(a_yan, usage_history, day, global_history)
            record_usage(a_tamm, usage_history, day, global_history)

        else:  # Hafta içi - çorba/yan/tamm aynı, ana farklı
            is_f = (day == fish_day)
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set(is_f)
            
            # Akşam ana yemeği farklı olsun
            a_cons = {
                'oven_banned': daily_ctx.oven_locked,
                'exclude_names': daily_ctx.daily_exclude + [safe_str(o_ana.get(COL_YEMEK_ADI))]
            }
            
            if not is_f and get_dish_meta(o_ana)['p_type'] in [VAL_KIRMIZI, VAL_BEYAZ]:
                a_cons['block_protein_list'] = [get_dish_meta(o_ana)['p_type']]
            
            a_ana = selector.select_dish(
                category="ANA YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=a_cons
            )
            record_usage(a_ana, usage_history, day, global_history)
            
            # Akşam diğer yemekleri öğleden aynı
            a_corba, a_yan, a_tamm = o_corba, o_yan, o_tamm
        
        # Gece Atıştırmalık
        s_cons = {
            'oven_banned': daily_ctx.oven_locked,
            'exclude_names': daily_ctx.daily_exclude
        }
        
        if w_idx in ready_snack_indices:
            s_cons['force_equipment'] = VAL_HAZIR
        
        snack = selector.select_dish(
            category="GECE ATIŞTIRMALIK",
            usage_history=usage_history,
            current_day_obj=curr_date,
            base_constraints=s_cons
        )
        record_usage(snack, usage_history, day, global_history)
        
        # Menü kaydı
        menu_log.append({
            "TARİH": d_str,
            "GÜN": w_name,
            "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": safe_str(o_corba.get(COL_YEMEK_ADI)),
            "ÖĞLE ANA": safe_str(o_ana.get(COL_YEMEK_ADI)),
            "ÖĞLE YAN": safe_str(o_yan.get(COL_YEMEK_ADI)),
            "ÖĞLE TAMM": safe_str(o_tamm.get(COL_YEMEK_ADI)),
            "AKŞAM ÇORBA": safe_str(a_corba.get(COL_YEMEK_ADI)),
            "AKŞAM ANA": safe_str(a_ana.get(COL_YEMEK_ADI)),
            "AKŞAM YAN": safe_str(a_yan.get(COL_YEMEK_ADI)),
            "AKŞAM TAMM": safe_str(a_tamm.get(COL_YEMEK_ADI)),
            "GECE": f"Çay/Kahve + {safe_str(snack.get(COL_YEMEK_ADI))}"
        })
        
        # Ertesi gün için exclude listesi
        prev_dishes = [
            safe_str(o_corba.get(COL_YEMEK_ADI)),
            safe_str(o_ana.get(COL_YEMEK_ADI)),
            safe_str(a_ana.get(COL_YEMEK_ADI)),
            safe_str(o_yan.get(COL_YEMEK_ADI)),
            safe_str(snack.get(COL_YEMEK_ADI))
        ]
    
    return pd.DataFrame(menu_log)

# =========================================================
# 🖥️ ARAYÜZ (GURME UI)
# =========================================================

def render_page(sel_model):
    st.header("👨‍🍳 Gurme Menü Şefi v6.1 - Akıllı Planlama Motoru")
    st.info("🎯 Havuz analizi + Kademeli gevşetme + Detaylı skorlama ile sıkışmasız menü!")
    
    client = get_gspread_client()
    if not client:
        st.error("Bağlantı hatası!")
        st.stop()
    
    # Son menüyü yükle
    if 'generated_menu' not in st.session_state:
        saved_df = load_last_menu(client)
        if saved_df is not None:
            st.session_state['generated_menu'] = saved_df
    
    # Ayarlar
    col1, col2 = st.columns(2)
    
    with col1:
        tr_aylar = {
            1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan",
            5: "Mayıs", 6: "Haziran", 7: "Temmuz", 8: "Ağustos",
            9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
        }
        sel_month = st.selectbox(
            "Ay",
            list(tr_aylar.keys()),
            format_func=lambda x: tr_aylar[x],
            index=datetime.now().month - 1
        )
        sel_year = st.number_input("Yıl", value=datetime.now().year)
    
    with col2:
        h_start = st.date_input("Tatil Başlangıç", value=None)
        h_end = st.date_input("Tatil Bitiş", value=None)
        ready_days = st.multiselect(
            "Gece Hazır Atıştırmalık Günleri",
            options=GUNLER_TR,
            default=["Pazar", "Pazartesi"]
        )
    
    st.divider()
    
    c1, c2 = st.columns(2)
    with c1:
        fish_pref = st.selectbox(
            "Balık Günü",
            ["Otomatik", "Yok"] + GUNLER_TR
        )
    with c2:
        target_meatless = st.slider(
            "Etsiz Öğün Hedefi",
            min_value=0,
            max_value=30,
            value=12
        )
    
    if st.button("🚀 Gurme Menü Oluştur", type="primary"):
        with st.spinner("👨‍🍳 Şef mutfakta, akıllı algoritma çalışıyor..."):
            pool = get_full_menu_pool(client)
            
            if not pool:
                st.error("Yemek havuzu boş!")
                st.stop()
            
            # Tatil aralığı
            holidays = []
            if h_start and h_end:
                holidays = [(h_start, h_end)]
            
            # Hazır atıştırmalık günleri
            ready_snack_indices = [GUNLER_TR.index(d) for d in ready_days]
            
            # Menü oluştur
            df_menu = generate_gourmet_menu(
                month=sel_month,
                year=sel_year,
                pool=pool,
                holidays=holidays,
                ready_snack_indices=ready_snack_indices,
                fish_pref=fish_pref,
                target_meatless=target_meatless
            )
            
            # Kaydet
            if save_menu_to_sheet(client, df_menu):
                st.session_state['generated_menu'] = df_menu
                st.success("✅ Menü başarıyla oluşturuldu ve kaydedildi!")
                st.balloons()
                st.rerun()
            else:
                st.error("Kayıt sırasında hata oluştu!")
    
    # Mevcut menüyü göster
    if 'generated_menu' in st.session_state:
        st.divider()
        st.subheader("📋 Oluşturulan Menü")
        
        # Zorunlu sayısını göster
        df = st.session_state['generated_menu']
        zorunlu_count = 0
        for col in df.columns:
            if col not in ['TARİH', 'GÜN']:
                zorunlu_count += df[col].astype(str).str.contains('ZORUNLU', na=False).sum()
        
        if zorunlu_count > 0:
            st.warning(f"⚠️ Toplam {zorunlu_count} adet '(ZORUNLU)' etiketli yemek var.")
        else:
            st.success("🎉 Tüm yemekler gurme kurallara uygun seçildi!")
        
        # Düzenlenebilir tablo
        edited = st.data_editor(
            st.session_state['generated_menu'],
            use_container_width=True,
            height=600
        )
        
        # Kaydet ve İndir butonları
        col_btn1, col_btn2 = st.columns(2)
        
        with col_btn1:
            if st.button("💾 Değişiklikleri Kaydet", use_container_width=True):
                if save_menu_to_sheet(client, edited):
                    st.session_state['generated_menu'] = edited
                    st.success("✅ Değişiklikler kaydedildi!")
                else:
                    st.error("❌ Kayıt başarısız!")
        
        with col_btn2:
            # Excel indirme
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                edited.to_excel(writer, index=False, sheet_name='Menü')
                
                # Excel formatlaması
                workbook = writer.book
                worksheet = writer.sheets['Menü']
                
                # Header formatı
                header_format = workbook.add_format({
                    'bold': True,
                    'bg_color': '#4CAF50',
                    'font_color': 'white',
                    'border': 1
                })
                
                # Hücre formatı
                cell_format = workbook.add_format({
                    'border': 1,
                    'text_wrap': True,
                    'valign': 'vcenter'
                })
                
                # Sütun genişlikleri
                worksheet.set_column('A:A', 12)  # Tarih
                worksheet.set_column('B:B', 15)  # Gün
                worksheet.set_column('C:K', 25)  # Yemekler
                
                # Header'ları formatla
                for col_num, value in enumerate(edited.columns.values):
                    worksheet.write(0, col_num, value, header_format)
            
            buffer.seek(0)
            
            st.download_button(
                label="📥 Excel Olarak İndir",
                data=buffer,
                file_name=f"menu_{sel_year}_{sel_month:02d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
