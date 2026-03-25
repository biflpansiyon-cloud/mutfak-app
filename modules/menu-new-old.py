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

# --- AYARLAR ---
ACTIVE_MENU_SHEET_NAME = "AKTIF_MENU"
GUNLER_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

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
    return name.replace(" (ZORUNLU)", "").strip()

def get_unique_key(dish: Dict) -> str:
    """Yemek için benzersiz anahtar"""
    cat = safe_str(dish.get('KATEGORİ'))
    name = clean_dish_name(safe_str(dish.get('YEMEK ADI')))
    return f"{cat}_{name}"

def get_dish_meta(dish: Dict) -> Dict:
    """Yemeğin tüm meta bilgilerini çıkar"""
    if not dish:
        return {
            "tag": "", "alt_tur": "", "renk": "", "equip": "",
            "p_type": "", "tat": "", "doku": "", "puan": 5, "yakisan": ""
        }

    try:
        puan = float(dish.get('GURME_PUAN') or 5)
    except:
        puan = 5

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

            try:
                l_val = float(item.get('LIMIT', 99) or 99)
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
            cat = safe_str(dish.get('KATEGORİ'))
            if not cat:
                continue

            stats[cat]['total'] += 1
            stats[cat]['available_dishes'].append(dish)
            stats[cat]['by_protein'][safe_str(dish.get('PROTEIN_TURU'))] += 1
            stats[cat]['by_equipment'][safe_str(dish.get('PISIRME_EKIPMAN'))] += 1
            stats[cat]['by_texture'][safe_str(dish.get('DOKU'))] += 1
            stats[cat]['by_flavor'][safe_str(dish.get('TAT_PROFILI'))] += 1
            stats[cat]['by_color'][safe_str(dish.get('RENK'))] += 1
            stats[cat]['by_alt_type'][safe_str(dish.get('ALT_TUR'))] += 1

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
        levels = []

        # Level 4: TAM GURME
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
            # force_protein_types ve force_fish hard limit sayılır — gevşetme ezip geçemez
            'force_protein_types': base_constraints.get('force_protein_types'),
            'force_fish': base_constraints.get('force_fish', False),
        }
        # None olan anahtarları temizle
        level1 = {k: v for k, v in level1.items() if v is not None and v is not False}
        levels.append(level1)

        return levels

# =========================================================
# 🎨 GURME SKORLAYICI - Detaylı Puanlama
# =========================================================

class GourmetScorer:
    """Yemekleri gurme kriterlerine göre skorlar"""

    def __init__(self):
        self.PERFECT_MATCH_BONUS = 50
        self.TEXTURE_HARMONY_BONUS = 15
        self.FLAVOR_CONTRAST_BONUS = 10
        self.COLOR_BALANCE_BONUS = 10
        self.FRESHNESS_BONUS = 5
        self.TEXTURE_CLASH_PENALTY = 10
        self.FLAVOR_CLASH_PENALTY = 15
        self.COLOR_OVERLOAD_PENALTY = 8
        self.OVERUSED_PENALTY = 20

    def score_dish(self, dish: Dict, meta: Dict, context: Dict) -> float:
        base_score = meta.get('puan', 5)
        score = float(base_score)

        if context.get('perfect_match_name'):
            dish_name = clean_dish_name(safe_str(dish.get('YEMEK ADI')))
            if dish_name.upper() in context['perfect_match_name'].upper():
                score += self.PERFECT_MATCH_BONUS

        meal_textures = context.get('meal_textures', [])
        dish_texture = meta.get('doku', '')
        if dish_texture and meal_textures:
            if 'SULU' in meal_textures and dish_texture == 'KURU':
                score += self.TEXTURE_HARMONY_BONUS
            elif dish_texture in meal_textures:
                score -= self.TEXTURE_CLASH_PENALTY

        meal_flavors = context.get('meal_flavors', [])
        dish_flavor = meta.get('tat', '')
        if dish_flavor and meal_flavors:
            if dish_flavor in meal_flavors:
                score -= self.FLAVOR_CLASH_PENALTY
            elif 'SALÇALI' in meal_flavors and dish_flavor in ['SADE', 'KREMALI']:
                score += self.FLAVOR_CONTRAST_BONUS

        meal_colors = context.get('meal_colors', [])
        dish_color = meta.get('renk', '')
        if dish_color == 'KIRMIZI' and meal_colors:
            red_count = meal_colors.count('KIRMIZI')
            if red_count >= 2:
                score -= self.COLOR_OVERLOAD_PENALTY * red_count
            elif red_count == 0:
                score += self.COLOR_BALANCE_BONUS

        total_usage = context.get('total_usage', 0)
        if total_usage >= 3:
            score -= self.OVERUSED_PENALTY * (total_usage - 2)

        usage_days = context.get('usage_days', [])
        if usage_days:
            days_since = context.get('current_day', 1) - usage_days[-1]
            score += min(days_since * self.FRESHNESS_BONUS, 30)

        return max(score, 0)

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
        if score_context is None:
            score_context = {}

        current_day = current_day_obj.day
        day_name = GUNLER_TR[current_day_obj.weekday()]

        candidates = [d for d in self.pool if safe_str(d.get('KATEGORİ')) == category]
        if not candidates:
            return {"YEMEK ADI": "---", "KATEGORİ": category}

        candidates = [
            d for d in candidates
            if day_name.upper() not in safe_str(d.get('YASAKLI_GUNLER')).upper()
        ]
        if not candidates:
            return {"YEMEK ADI": "--- (GÜN YASAĞI)", "KATEGORİ": category}

        filter_levels = self.constraint_mgr.build_progressive_filters(base_constraints)
        best_candidates = []
        used_level = -1

        for level_idx, constraints in enumerate(filter_levels):
            filtered = self._apply_constraints(candidates, constraints, usage_history, current_day)
            if filtered:
                best_candidates = filtered
                used_level = 4 - level_idx
                break

        if not best_candidates:
            emergency = self._emergency_selection(candidates, base_constraints)
            if emergency:
                name = safe_str(emergency.get('YEMEK ADI'))
                if "(ZORUNLU)" not in name:
                    emergency['YEMEK ADI'] = f"{name} (ZORUNLU)"
            return emergency or {"YEMEK ADI": "---", "KATEGORİ": category}

        scored = []
        for dish in best_candidates:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            context = score_context.copy()
            context['usage_days'] = usage_history.get(u_key, [])
            context['total_usage'] = len(usage_history.get(u_key, []))
            context['current_day'] = current_day
            score = self.scorer.score_dish(dish, meta, context)
            scored.append((dish, score, used_level))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = min(3, len(scored))
        finalists = [s[0] for s in scored[:top_n]]
        selected = random.choice(finalists)

        if used_level <= 2:
            selected_copy = selected.copy()
            name = safe_str(selected_copy.get('YEMEK ADI'))
            if "(ZORUNLU)" not in name:
                selected_copy['YEMEK ADI'] = f"{name} (ZORUNLU)"
            return selected_copy

        return selected

    def _apply_constraints(
        self,
        candidates: List[Dict],
        constraints: Dict,
        usage_history: Dict,
        current_day: int
    ) -> List[Dict]:
        filtered = []

        for dish in candidates:
            meta = get_dish_meta(dish)
            u_key = get_unique_key(dish)
            name = clean_dish_name(safe_str(dish.get('YEMEK ADI')))

            if constraints.get('oven_banned') and meta['equip'] == 'FIRIN':
                continue

            used_days = usage_history.get(u_key, [])
            try:
                limit_val = int(float(dish.get('LIMIT') or 99))
            except:
                limit_val = 99
            if len(used_days) >= limit_val:
                continue

            try:
                ara_val = int(float(dish.get('ARA') or 0))
            except:
                ara_val = 0
            if used_days and (current_day - used_days[-1]) <= ara_val:
                continue

            if constraints.get('exclude_names') and name in constraints['exclude_names']:
                continue

            if constraints.get('force_fish'):
                if meta['p_type'] != 'BALIK':
                    continue

            if constraints.get('block_protein_list') and meta['p_type'] in constraints['block_protein_list']:
                continue

            if constraints.get('force_protein_types') and meta['p_type'] not in constraints['force_protein_types']:
                continue

            if constraints.get('block_content_tags') and meta['tag'] in constraints['block_content_tags']:
                continue

            if constraints.get('block_alt_types') and meta['alt_tur'] in constraints['block_alt_types']:
                continue

            if constraints.get('legume_interval'):
                if meta['alt_tur'] == 'BAKLIYAT':
                    last_legume = constraints.get('last_legume_day', -99)
                    if (current_day - last_legume) < 3:
                        continue

            if constraints.get('color_balance'):
                current_colors = constraints.get('current_meal_colors', [])
                if meta['renk'] == 'KIRMIZI' and current_colors.count('KIRMIZI') >= 2:
                    continue

            if constraints.get('force_equipment'):
                if meta['equip'] != constraints['force_equipment']:
                    continue

            filtered.append(dish)

        return filtered

    def _emergency_selection(self, candidates: List[Dict], constraints: Dict) -> Optional[Dict]:
        if constraints.get('oven_banned'):
            non_oven = [d for d in candidates if safe_str(d.get('PISIRME_EKIPMAN')) != 'FIRIN']
            if non_oven:
                return random.choice(non_oven)
        return random.choice(candidates) if candidates else None

# =========================================================
# 📝 KULLANIM KAYDI
# =========================================================

def record_usage(dish: Dict, usage_history: Dict, day: int, global_history: Dict):
    """Yemeğin kullanımını kaydet"""
    if not dish or dish.get('YEMEK ADI') in ["---", "--- (GÜN YASAĞI)"]:
        return

    u_key = get_unique_key(dish)
    if u_key not in usage_history:
        usage_history[u_key] = []
    usage_history[u_key].append(day)

    meta = get_dish_meta(dish)
    if meta['alt_tur'] == 'BAKLIYAT':
        global_history['last_legume'] = day

# =========================================================
# 📊 YEMEK İSTATİSTİKLERİ
# =========================================================

def compute_meal_stats(df: pd.DataFrame) -> pd.DataFrame:
    """
    Menü DataFrame'inden öğün bazlı yemek sayımı yapar.
    Kahvaltı hariç: Öğle, Akşam, Gece.
    """
    ogle_cols  = ["ÖĞLE ÇORBA", "ÖĞLE ANA", "ÖĞLE YAN", "ÖĞLE TAMM"]
    aksam_cols = ["AKŞAM ÇORBA", "AKŞAM ANA", "AKŞAM YAN", "AKŞAM TAMM"]
    gece_cols  = ["GECE"]

    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"ÖĞLE": 0, "AKŞAM": 0, "GECE": 0})

    for _, row in df.iterrows():
        gun = str(row.get("GÜN", ""))
        if "TATİL" in gun:
            continue

        for col in ogle_cols:
            if col in df.columns:
                val = clean_dish_name(safe_str(row.get(col, "")))
                if val and val not in ["-", "---", "--- (GÜN YASAĞI)"]:
                    counts[val]["ÖĞLE"] += 1

        for col in aksam_cols:
            if col in df.columns:
                val = clean_dish_name(safe_str(row.get(col, "")))
                if val and val not in ["-", "---", "--- (GÜN YASAĞI)"]:
                    counts[val]["AKŞAM"] += 1

        for col in gece_cols:
            if col in df.columns:
                raw = safe_str(row.get(col, ""))
                if "+" in raw:
                    val = clean_dish_name(raw.split("+", 1)[1].strip())
                else:
                    val = clean_dish_name(raw)
                if val and val not in ["-", "---"]:
                    counts[val]["GECE"] += 1

    if not counts:
        return pd.DataFrame()

    rows = []
    for yemek, oguns in counts.items():
        toplam = oguns["ÖĞLE"] + oguns["AKŞAM"] + oguns["GECE"]
        rows.append({
            "YEMEK ADI": yemek,
            "ÖĞLE": oguns["ÖĞLE"],
            "AKŞAM": oguns["AKŞAM"],
            "GECE": oguns["GECE"],
            "TOPLAM": toplam
        })

    stats_df = pd.DataFrame(rows).sort_values("TOPLAM", ascending=False).reset_index(drop=True)
    return stats_df


def render_stats_tab(df: pd.DataFrame):
    """İstatistik sekmesini çizer"""
    st.subheader("📊 Aylık Yemek Kullanım İstatistikleri")
    st.caption("Kahvaltı hariç; Öğle, Akşam ve Gece atıştırmalıkları bazında kaç kez çıktığı gösterilmektedir.")

    stats_df = compute_meal_stats(df)

    if stats_df.empty:
        st.info("İstatistik oluşturmak için önce bir menü üretin.")
        return

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        search = st.text_input("🔍 Yemek Ara", placeholder="örn: mercimek")
    with col_f2:
        min_count = st.number_input("Min. tekrar sayısı", min_value=1, value=1, step=1)

    filtered = stats_df[stats_df["TOPLAM"] >= min_count]
    if search:
        filtered = filtered[filtered["YEMEK ADI"].str.contains(search, case=False, na=False)]

    st.markdown(f"**{len(filtered)} yemek** listeleniyor")

    def color_total(val):
        if val >= 4:
            return "background-color: #ffcccc"
        elif val == 3:
            return "background-color: #fff3cd"
        elif val == 2:
            return "background-color: #d4edda"
        return ""

    styled = filtered.style.applymap(color_total, subset=["TOPLAM"])
    st.dataframe(styled, use_container_width=True, height=500)

    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Farklı Yemek Sayısı", len(stats_df))
    m2.metric(
        "En Çok Tekrar Eden",
        stats_df.iloc[0]["YEMEK ADI"] if not stats_df.empty else "-",
        delta=f"{stats_df.iloc[0]['TOPLAM']}x" if not stats_df.empty else ""
    )
    m3.metric("3+ Kez Çıkan Yemek", int((stats_df["TOPLAM"] >= 3).sum()))
    m4.metric("Tek Seferlik Yemek", int((stats_df["TOPLAM"] == 1).sum()))

    st.divider()
    st.markdown("#### Öğün Dağılımı Karşılaştırması")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("🍽️ **En çok öğle çıkanlar**")
        top_ogle = stats_df.nlargest(5, "ÖĞLE")[["YEMEK ADI", "ÖĞLE"]]
        st.dataframe(top_ogle, hide_index=True, use_container_width=True)
    with col_b:
        st.markdown("🌙 **En çok akşam çıkanlar**")
        top_aksam = stats_df.nlargest(5, "AKŞAM")[["YEMEK ADI", "AKŞAM"]]
        st.dataframe(top_aksam, hide_index=True, use_container_width=True)
    with col_c:
        st.markdown("⭐ **En çok gece çıkanlar**")
        top_gece = stats_df.nlargest(5, "GECE")[["YEMEK ADI", "GECE"]]
        st.dataframe(top_gece, hide_index=True, use_container_width=True)

# =========================================================
# 📅 GURME PLANLAMA DÖNGÜSÜ
# =========================================================

def generate_gourmet_menu(month, year, pool, holidays, ready_snack_indices, fish_pref, target_meatless):
    """Ana menü oluşturma fonksiyonu"""

    num_days = calendar.monthrange(year, month)[1]
    menu_log = []
    usage_history = {}
    global_history = {'last_legume': -99}

    analyzer = PoolAnalyzer(pool)
    selector = DishSelector(pool, analyzer)

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

    # Katı dönüşümlü mod: hedef == aktif gün sayısı ise her güne tam 1 etsiz öğün koy
    active_days = sum(
        1 for d in range(1, num_days + 1)
        if not any(h[0] <= datetime(year, month, d).date() <= h[1] for h in holidays)
    )
    strict_alternating = (target_meatless == active_days)

    # Dönüşümlü modda hangi öğünün etsiz olacağını önceden belirle:
    # Çift günler → öğle etsiz, tek günler → akşam etsiz (dönüşümlü dağılım)
    strict_meatless_at_lunch: Dict[int, bool] = {}
    if strict_alternating:
        toggle = True
        for d in range(1, num_days + 1):
            curr = datetime(year, month, d)
            if any(h[0] <= curr.date() <= h[1] for h in holidays):
                continue
            if d == fish_day:
                continue  # Balık günü kurala dahil edilmez
            strict_meatless_at_lunch[d] = toggle
            toggle = not toggle

    for day in range(1, num_days + 1):
        curr_date = datetime(year, month, day)
        d_str = curr_date.strftime("%d.%m.%Y")
        w_idx = curr_date.weekday()
        w_name = GUNLER_TR[w_idx]

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

        OVEN_LOCKED = False
        daily_exclude = prev_dishes.copy()

        k_str = "-"
        if w_idx in [1, 3, 5, 6]:
            kahv = selector.select_dish(
                category="KAHVALTI EKSTRA",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints={
                    'oven_banned': OVEN_LOCKED,
                    'exclude_names': daily_exclude
                }
            )
            record_usage(kahv, usage_history, day, global_history)
            k_str = safe_str(kahv.get('YEMEK ADI'))
            if get_dish_meta(kahv)['equip'] == 'FIRIN':
                OVEN_LOCKED = True

        days_left = num_days - day + 1
        meatless_remaining = target_meatless - meatless_cnt

        if strict_alternating and day in strict_meatless_at_lunch:
            # Katı mod: bu günün hangi öğününün etsiz olduğu önceden belirlendi
            force_veg = strict_meatless_at_lunch[day]          # öğle için
            force_veg_evening = not strict_meatless_at_lunch[day]  # akşam için
        else:
            force_veg = meatless_remaining > 0 and (meatless_remaining / days_left) >= 0.4
            force_veg_evening = force_veg

        def plan_meal_set(is_fish_meal=False):
            nonlocal OVEN_LOCKED, meatless_cnt

            a_cons = {'oven_banned': OVEN_LOCKED, 'exclude_names': daily_exclude}

            if is_fish_meal:
                a_cons['force_fish'] = True
            elif strict_alternating and day in strict_meatless_at_lunch:
                # Katı mod: öğle için önceden belirlenen role uygula
                if force_veg:
                    a_cons['force_protein_types'] = ['ETSİZ']
                else:
                    a_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
            elif force_veg:
                a_cons['force_protein_types'] = ['ETSİZ']
            elif meatless_cnt >= target_meatless:
                a_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']

            ana = selector.select_dish(
                category="ANA YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=a_cons
            )
            record_usage(ana, usage_history, day, global_history)

            a_m = get_dish_meta(ana)
            if a_m['equip'] == 'FIRIN':
                OVEN_LOCKED = True
            if a_m['p_type'] == 'ETSİZ' and not is_fish_meal:
                meatless_cnt += 1

            meal_context = {
                'perfect_match_name': a_m['yakisan'],
                'meal_textures': [a_m['doku']],
                'meal_flavors': [a_m['tat']],
                'current_meal_colors': [a_m['renk']],
            }

            meal_cons = {
                'oven_banned': OVEN_LOCKED,
                'exclude_names': daily_exclude + [safe_str(ana.get('YEMEK ADI'))],
                'block_content_tags': [a_m['tag']] if a_m['tag'] else [],
                'legume_interval': True,
                'last_legume_day': global_history.get('last_legume', -99),
                'color_balance': True,
                'current_meal_colors': [a_m['renk']]
            }

            if a_m['alt_tur'] in ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']:
                meal_cons['block_alt_types'] = ['PIRINC', 'BULGUR', 'HAMUR', 'PATATES']

            if a_m['p_type'] in ['KIRMIZI', 'BEYAZ']:
                meal_cons['block_protein_list'] = ['KIRMIZI', 'BEYAZ', 'BALIK']

            corba = selector.select_dish(
                category="ÇORBA",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(corba, usage_history, day, global_history)
            if get_dish_meta(corba)['equip'] == 'FIRIN':
                OVEN_LOCKED = True

            side = selector.select_dish(
                category="YAN YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(side, usage_history, day, global_history)
            if get_dish_meta(side)['equip'] == 'FIRIN':
                OVEN_LOCKED = True

            tamm = selector.select_dish(
                category="TAMAMLAYICI",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=meal_cons,
                score_context=meal_context
            )
            record_usage(tamm, usage_history, day, global_history)

            return corba, ana, side, tamm

        if w_idx >= 5:
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set()
            a_corba, a_ana, a_yan, a_tamm = o_corba, o_ana, o_yan, o_tamm
        else:
            is_f = (day == fish_day)
            o_corba, o_ana, o_yan, o_tamm = plan_meal_set(is_f)

            a_cons = {
                'oven_banned': OVEN_LOCKED,
                'exclude_names': daily_exclude + [safe_str(o_ana.get('YEMEK ADI'))]
            }

            # Akşam yemeğine de etsiz/etli kısıt uygula
            if not is_f:
                if strict_alternating and day in strict_meatless_at_lunch:
                    # Katı mod: öğle etsiz ise akşam etli, öğle etli ise akşam etsiz
                    if force_veg_evening:
                        a_cons['force_protein_types'] = ['ETSİZ']
                    else:
                        a_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
                elif force_veg:
                    a_cons['force_protein_types'] = ['ETSİZ']
                elif meatless_cnt >= target_meatless:
                    a_cons['force_protein_types'] = ['KIRMIZI', 'BEYAZ']
                elif get_dish_meta(o_ana)['p_type'] in ['KIRMIZI', 'BEYAZ']:
                    a_cons['block_protein_list'] = [get_dish_meta(o_ana)['p_type']]

            a_ana = selector.select_dish(
                category="ANA YEMEK",
                usage_history=usage_history,
                current_day_obj=curr_date,
                base_constraints=a_cons
            )
            record_usage(a_ana, usage_history, day, global_history)

            # Akşam etsiz seçildiyse sayaca ekle
            if get_dish_meta(a_ana)['p_type'] == 'ETSİZ' and not is_f:
                meatless_cnt += 1

            a_corba, a_yan, a_tamm = o_corba, o_yan, o_tamm

        s_cons = {
            'oven_banned': OVEN_LOCKED,
            'exclude_names': daily_exclude
        }
        if w_idx in ready_snack_indices:
            s_cons['force_equipment'] = 'HAZIR'

        snack = selector.select_dish(
            category="GECE ATIŞTIRMALIK",
            usage_history=usage_history,
            current_day_obj=curr_date,
            base_constraints=s_cons
        )
        record_usage(snack, usage_history, day, global_history)

        menu_log.append({
            "TARİH": d_str,
            "GÜN": w_name,
            "KAHVALTI": k_str,
            "ÖĞLE ÇORBA": safe_str(o_corba.get('YEMEK ADI')),
            "ÖĞLE ANA": safe_str(o_ana.get('YEMEK ADI')),
            "ÖĞLE YAN": safe_str(o_yan.get('YEMEK ADI')),
            "ÖĞLE TAMM": safe_str(o_tamm.get('YEMEK ADI')),
            "AKŞAM ÇORBA": safe_str(a_corba.get('YEMEK ADI')),
            "AKŞAM ANA": safe_str(a_ana.get('YEMEK ADI')),
            "AKŞAM YAN": safe_str(a_yan.get('YEMEK ADI')),
            "AKŞAM TAMM": safe_str(a_tamm.get('YEMEK ADI')),
            "GECE": f"Çay/Kahve + {safe_str(snack.get('YEMEK ADI'))}"
        })

        prev_dishes = [
            safe_str(o_corba.get('YEMEK ADI')),
            safe_str(o_ana.get('YEMEK ADI')),
            safe_str(a_ana.get('YEMEK ADI')),
            safe_str(o_yan.get('YEMEK ADI')),
            safe_str(snack.get('YEMEK ADI'))
        ]

    return pd.DataFrame(menu_log)

# =========================================================
# 🖥️ ARAYÜZ (GURME UI)
# =========================================================

def render_page(sel_model):
    st.header("👨‍🍳 Gurme Menü Şefi v6.0 - Akıllı Planlama Motoru")
    st.info("🎯 Havuz analizi + Kademeli gevşetme + Detaylı skorlama ile sıkışmasız menü!")

    client = get_gspread_client()
    if not client:
        st.error("Bağlantı hatası!")
        st.stop()

    if 'generated_menu' not in st.session_state:
        saved_df = load_last_menu(client)
        if saved_df is not None:
            st.session_state['generated_menu'] = saved_df

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

            holidays = []
            if h_start and h_end:
                holidays = [(h_start, h_end)]

            ready_snack_indices = [GUNLER_TR.index(d) for d in ready_days]

            df_menu = generate_gourmet_menu(
                month=sel_month,
                year=sel_year,
                pool=pool,
                holidays=holidays,
                ready_snack_indices=ready_snack_indices,
                fish_pref=fish_pref,
                target_meatless=target_meatless
            )

            if save_menu_to_sheet(client, df_menu):
                st.session_state['generated_menu'] = df_menu
                st.success("✅ Menü başarıyla oluşturuldu ve kaydedildi!")
                st.balloons()
                st.rerun()
            else:
                st.error("Kayıt sırasında hata oluştu!")

    # ── Menü ve İstatistik Sekmeleri ──────────────────────
    if 'generated_menu' in st.session_state:
        st.divider()
        tab1, tab2 = st.tabs(["📋 Menü", "📊 İstatistikler"])

        with tab1:
            st.subheader("📋 Oluşturulan Menü")
            df = st.session_state['generated_menu']

            zorunlu_count = 0
            for col in df.columns:
                if col not in ['TARİH', 'GÜN']:
                    zorunlu_count += df[col].astype(str).str.contains('ZORUNLU', na=False).sum()

            if zorunlu_count > 0:
                st.warning(f"⚠️ Toplam {zorunlu_count} adet '(ZORUNLU)' etiketli yemek var.")
            else:
                st.success("🎉 Tüm yemekler gurme kurallara uygun seçildi!")

            edited = st.data_editor(
                st.session_state['generated_menu'],
                use_container_width=True,
                height=600
            )

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("💾 Değişiklikleri Kaydet", use_container_width=True):
                    if save_menu_to_sheet(client, edited):
                        st.session_state['generated_menu'] = edited
                        st.success("✅ Değişiklikler kaydedildi!")
                    else:
                        st.error("❌ Kayıt başarısız!")

            with col_btn2:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    edited.to_excel(writer, index=False, sheet_name='Menü')
                    workbook = writer.book
                    worksheet = writer.sheets['Menü']
                    header_format = workbook.add_format({
                        'bold': True,
                        'bg_color': '#4CAF50',
                        'font_color': 'white',
                        'border': 1
                    })
                    workbook.add_format({'border': 1, 'text_wrap': True, 'valign': 'vcenter'})
                    worksheet.set_column('A:A', 12)
                    worksheet.set_column('B:B', 15)
                    worksheet.set_column('C:K', 25)
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

        with tab2:
            render_stats_tab(st.session_state['generated_menu'])
