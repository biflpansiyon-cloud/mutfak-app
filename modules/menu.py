import streamlit as st
import pandas as pd
from datetime import datetime
import random
import io
from .utils import (
    get_gspread_client, 
    FILE_MENU, # Yeni Dosya
    MENU_POOL_SHEET_NAME
)

def get_full_menu_pool(client):
    try:
        sh = client.open(FILE_MENU) # Değişiklik burada
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        data = ws.get_all_values()
        if not data: return []
        header = [h.strip().upper() for h in data[0]]
        pool = []
        for row in data[1:]:
            item = {}
            while len(row) < len(header): row.append("")
            for i, col_name in enumerate(header): item[col_name] = row[i].strip()
            item['LIMIT'] = int(item['LIMIT']) if item.get('LIMIT') else 99
            item['ARA'] = int(item['ARA']) if item.get('ARA') else 0
            pool.append(item)
        return pool
    except: return []

# ... (generate_smart_menu ve render_page fonksiyonları tamamen aynı kalacak) ...
# Çünkü sadece veriyi çektiğimiz kaynağı değiştirdik.
def generate_smart_menu(month_index, year, pool, holidays, ready_snack_days):
    start_date = datetime(year, month_index, 1)
    if month_index == 12: next_month = datetime(year + 1, 1, 1)
    else: next_month = datetime(year, month_index + 1, 1)
    num_days = (next_month - start_date).days
    menu_log = []
    usage_history = {}
    cats = {}
    for p in pool:
        c = p.get('KATEGORİ', '').upper()
        if c not in cats: cats[c] = []
        cats[c].append(p)
    def get_candidates(category): return cats.get(category, [])
    for day in range(1, num_days + 1):
        current_date = datetime(year, month_index, day)
        weekday = current_date.weekday()
        date_str = current_date.strftime("%d.%m.%Y")
        is_holiday = False
        for h_start, h_end in holidays:
            if h_start <= current_date.date() <= h_end: is_holiday = True; break
        if is_holiday:
            menu_log.append({"GÜN": date_str, "KAHVALTI": "TATİL", "ÇORBA": "---", "ÖĞLE ANA": "---", "YAN": "---", "AKŞAM ANA": "---", "ARA": "---"})
            continue
        is_weekend = (weekday >= 5)
        def pick_dish(category, constraints={}):
            candidates = get_candidates(category)
            valid_options = []
            for dish in candidates:
                name = dish['YEMEK ADI']
                used_dates = usage_history.get(name, [])
                if len(used_dates) >= dish['LIMIT']: continue
                if used_dates:
                    if (day - used_dates[-1]) <= dish['ARA']: continue
                if constraints.get('block_equipment') and dish.get('PISIRME_EKIPMAN') == constraints['block_equipment']: continue
                if constraints.get('block_protein') and dish.get('PROTEIN_TURU') == constraints['block_protein']: continue
                if constraints.get('force_ready') and dish.get('PISIRME_EKIPMAN') != 'HAZIR': continue
                valid_options.append(dish)
            if not valid_options: return {"YEMEK ADI": "SEÇENEK YOK"}
            chosen = random.choice(valid_options)
            name = chosen['YEMEK ADI']
            if name not in usage_history: usage_history[name] = []
            usage_history[name].append(day)
            return chosen
        kahvalti = pick_dish("KAHVALTI EKSTRA")
        corba = pick_dish("ÇORBA")
        ogle_ana = pick_dish("ANA YEMEK")
        if ogle_ana.get('ZORUNLU_ES'): yan = {"YEMEK ADI": ogle_ana['ZORUNLU_ES']}
        else: yan = pick_dish("YAN YEMEK")
        if is_weekend: aksam_ana = ogle_ana 
        else:
            constraints = {}
            if ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN' or yan.get('PISIRME_EKIPMAN') == 'FIRIN': constraints['block_equipment'] = 'FIRIN'
            p_type = ogle_ana.get('PROTEIN_TURU')
            if p_type == 'KIRMIZI': constraints['block_protein'] = 'KIRMIZI'
            elif p_type == 'BEYAZ': constraints['block_protein'] = 'BEYAZ'
            aksam_ana = pick_dish("ANA YEMEK", constraints)
        snack_constraints = {}
        if weekday in ready_snack_days: snack_constraints['force_ready'] = True
        if (ogle_ana.get('PISIRME_EKIPMAN') == 'FIRIN') or (not is_weekend and aksam_ana.get('PISIRME_EKIPMAN') == 'FIRIN'): snack_constraints['block_equipment'] = 'FIRIN'
        ara = pick_dish("ARA ÖĞÜN", snack_constraints)
        menu_log.append({"GÜN": date_str, "KAHVALTI": kahvalti['YEMEK ADI'], "ÇORBA": corba['YEMEK ADI'], "ÖĞLE ANA": ogle_ana['YEMEK ADI'], "YAN": yan['YEMEK ADI'], "AKŞAM ANA": aksam_ana['YEMEK ADI'], "ARA": ara['YEMEK ADI']})
    return pd.DataFrame(menu_log)

def render_page(sel_model):
    st.header("👨‍🍳 Şefin Defteri")
    col1, col2 = st.columns(2)
    with col1:
        aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
        secilen_ay = st.selectbox("Ay", list(aylar.keys()), format_func=lambda x: aylar[x], index=datetime.now().month - 1)
        year = datetime.now().year
    with col2: ogrenci = st.number_input("Öğrenci", value=200)
    st.write("🏖️ **Tatil Günleri**")
    holiday_range = st.date_input("Tatil Aralığı", [], min_value=datetime(year, 1, 1), max_value=datetime(year, 12, 31))
    holidays = []
    if len(holiday_range) == 2: holidays.append((holiday_range[0], holiday_range[1]))
    st.write("🍪 **Hazır Ara Öğün**")
    days_map = {0:"Pazartesi", 1:"Salı", 2:"Çarşamba", 3:"Perşembe", 4:"Cuma", 5:"Cumartesi", 6:"Pazar"}
    selected_snack = st.multiselect("Hangi günler hazır?", list(days_map.keys()), format_func=lambda x: days_map[x], default=[5, 6])
    if st.button("🚀 Menü Oluştur", type="primary"):
        client = get_gspread_client()
        if client:
            pool = get_full_menu_pool(client)
            if pool:
                with st.spinner("Kurallar işleniyor..."):
                    df = generate_smart_menu(secilen_ay, year, pool, holidays, selected_snack)
                    st.session_state['menu'] = df
            else: st.error("Havuz Boş!")
        else: st.error("Bağlantı Yok")
    if 'menu' in st.session_state:
        edited = st.data_editor(st.session_state['menu'], num_rows="fixed", use_container_width=True)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            edited.to_excel(writer, sheet_name='Menu', index=False)
        st.download_button("📥 Excel İndir", output.getvalue(), f"Menu_{aylar[secilen_ay]}.xlsx")
