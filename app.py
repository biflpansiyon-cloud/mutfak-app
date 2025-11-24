import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import difflib
import pandas as pd
import random
import re

st.set_page_config(page_title="Mutfak ERP V18", page_icon="📉", layout="wide")

# ==========================================
# 🔒 GÜVENLİK DUVARI
# ==========================================
def check_password():
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    def password_entered():
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else: st.session_state["password_correct"] = False
    if not st.session_state["password_correct"]:
        st.text_input("Şifre:", type="password", on_change=password_entered, key="password")
        return False
    return True

if not check_password(): st.stop()

# ==========================================
# ⚙️ AYARLAR
# ==========================================
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR"
MENU_POOL_SHEET_NAME = "YEMEK_HAVUZU"

def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client, creds_dict.get("client_email")
    except Exception as e: return None, str(e)

# ==========================================
# 🛠️ YARDIMCI FONKSİYONLAR
# ==========================================
def clean_number(num_str):
    try:
        clean = re.sub(r'[^\d.,-]', '', str(num_str)) # Eksi işaretine de izin ver
        if not clean: return 0.0
        if clean.count('.') > 1 or clean.count(',') > 1:
             clean = clean.replace('.', '').replace(',', '.')
        elif ',' in clean and '.' not in clean:
             clean = clean.replace(',', '.')
        elif ',' in clean and '.' in clean:
             if clean.find(',') < clean.find('.'): clean = clean.replace(',', '')
             else: clean = clean.replace('.', '').replace(',', '.')
        return float(clean)
    except: return 0.0

def turkish_lower(text):
    if not text: return ""
    return text.replace('İ', 'i').replace('I', 'ı').lower().strip()

def standardize_name(text):
    if not text or len(text.strip()) < 2: return "Genel"
    cleaned = text.replace("*", "").replace("-", "").strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.6):
    if not ocr_text: return None
    ocr_key = turkish_lower(ocr_text)
    db_keys = [turkish_lower(p) for p in db_list]
    matches = difflib.get_close_matches(ocr_key, db_keys, n=1, cutoff=cutoff)
    if matches:
        return db_list[db_keys.index(matches[0])]
    return None

def get_or_create_worksheet(sh, title, cols, header):
    try:
        for ws in sh.worksheets():
            if turkish_lower(ws.title) == turkish_lower(title): return ws
        ws = sh.add_worksheet(title=title, rows=1000, cols=cols)
        ws.append_row(header)
        return ws
    except Exception as e:
        if "already exists" in str(e): return sh.worksheet(title)
        return None

def resolve_company_name(ocr_name, client, known_companies=None):
    std_name = standardize_name(ocr_name)
    try:
        sh = client.open(SHEET_NAME)
        try:
            ws = sh.worksheet(SETTINGS_SHEET_NAME)
            data = ws.get_all_values()
            alias_map = {}
            for row in data[1:]:
                if len(row) >= 2: 
                    k = turkish_lower(row[0]).strip()
                    v = row[1].strip()
                    if k: alias_map[k] = v
            if turkish_lower(std_name) in alias_map: return alias_map[turkish_lower(std_name)]
            for k, v in alias_map.items():
                if k in turkish_lower(std_name): return v
            best = find_best_match(std_name, list(alias_map.keys()), cutoff=0.7)
            if best: return alias_map[turkish_lower(best)]
        except: pass
    except: pass
    if known_companies:
        best_db = find_best_match(std_name, known_companies, cutoff=0.6)
        if best_db: return best_db
    return std_name

def resolve_product_name(ocr_prod, client):
    clean_prod = ocr_prod.replace("*", "").strip()
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(SETTINGS_SHEET_NAME)
        except: return clean_prod
        data = ws.get_all_values()
        product_map = {}
        for row in data[1:]:
            if len(row) >= 4:
                if row[2] and row[3]: product_map[turkish_lower(row[2])] = row[3].strip()
        key = turkish_lower(clean_prod)
        if key in product_map: return product_map[key]
        for k, v in product_map.items():
            if k in key: return v
        best = find_best_match(clean_prod, list(product_map.keys()), cutoff=0.85)
        if best: return product_map[turkish_lower(best)]
        return clean_prod
    except: return clean_prod

def get_price_database(client):
    # DİKKAT: Artık 5. Sütun (KALAN KOTA) var
    # Dönüş yapısı: { "Firma|Urun": {"fiyat": 100, "kota": 50, "row": 2} }
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, cols=6, header=["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "GÜNCELLEME TARİHİ", "KALAN KOTA"])
        data = ws.get_all_values()
        
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 3:
                ted = standardize_name(row[0])
                urn = row[1].strip()
                fyt = clean_number(row[2])
                
                # Kota sütununu oku (E sütunu = index 4)
                kota = 0.0
                if len(row) >= 5:
                    kota = clean_number(row[4])
                
                if ted not in price_db: price_db[ted] = {}
                price_db[ted][urn] = {"fiyat": fyt, "kota": kota, "row": idx + 1}
        return price_db
    except: return {}

def get_full_menu_pool(client):
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(MENU_POOL_SHEET_NAME)
        data = ws.get_all_values()
        if not data: return []
        header = [h.strip().upper() for h in data[0]]
        pool = []
        for row in data[1:]:
            item = {}
            while len(row) < len(header): row.append("")
            for i, col_name in enumerate(header): item[col_name] = row[i].strip()
            try: item['LIMIT'] = int(item['LIMIT']) if item['LIMIT'] else 99
            except: item['LIMIT'] = 99
            try: item['ARA'] = int(item['ARA']) if item['ARA'] else 0
            except: item['ARA'] = 0
            pool.append(item)
        return pool
    except: return []

# ==========================================
# MODÜL 1: İRSALİYE (KOTA DÜŞÜCÜ)
# ==========================================
def analyze_receipt_image(image, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG')
    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    İrsaliyeyi analiz et. Tedarikçi firmayı bul.
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Birimli) | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz. Markdown kullanma.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def save_receipt_smart(raw_text):
    client, err = get_gspread_client()
    if not client: return False, err
    
    # Veritabanını çek
    price_db = get_price_database(client)
    known_companies = list(price_db.keys())
    
    try:
        sh = client.open(SHEET_NAME)
        # Fiyat anahtarı sekmesine erişim (Kota güncellemek için)
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 6, [])
        
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        firm_data = {}
        kota_updates = [] # Toplu güncelleme listesi
        
        for line in raw_text.split('\n'):
            line = line.replace("*", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client, known_companies)
                tarih, urun, miktar, fiyat, tutar = parts[1], parts[2], parts[3], parts[4], parts[5]
                f_val = clean_number(fiyat)
                final_urun = resolve_product_name(urun, client)
                m_val = clean_number(miktar) # Gelen miktar
                
                # --- FİYAT VE KOTA OPERASYONU ---
                if final_firma in price_db:
                    prods = list(price_db[final_firma].keys())
                    match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                    
                    if match_prod:
                        db_item = price_db[final_firma][match_prod]
                        
                        # 1. Fiyat Yoksa Çek
                        if f_val == 0:
                            f_val = db_item['fiyat']
                            fiyat = str(f_val)
                            tutar = f"{m_val * f_val:.2f}"
                        
                        # 2. İsmi Standartlaştır
                        final_urun = match_prod
                        
                        # 3. KOTADAN DÜŞ (İrsaliye = Harcama)
                        current_kota = db_item['kota']
                        new_kota = current_kota - m_val
                        row_num = db_item['row']
                        
                        # Güncellemeyi listeye ekle (Hücre E{row})
                        kota_updates.append({'range': f'E{row_num}', 'values': [[new_kota]]})
                
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append([tarih, final_urun, miktar, fiyat, tutar])
        
        # Google Sheets'e Yazma İşlemleri
        msg = []
        # 1. İrsaliyeleri Yaz
        for firma, rows in firm_data.items():
            fn = turkish_lower(firma)
            if fn in existing_sheets: ws = existing_sheets[fn]
            else:
                ws = get_or_create_worksheet(sh, firma, 1000, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
                existing_sheets[fn] = ws
            ws.append_rows(rows)
            msg.append(f"{firma}: {len(rows)}")
            
        # 2. Kotaları Güncelle (Batch Update)
        if kota_updates:
            price_ws.batch_update(kota_updates)
            msg.append(f"(Stoklar Güncellendi: {len(kota_updates)} kalem)")
            
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)

# ==========================================
# MODÜL 2: FATURA (KOTA YÜKLEYİCİ)
# ==========================================
def analyze_invoice_pdf(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    pdf_bytes = uploaded_file.getvalue()
    base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    prompt = """
    FATURAYI analiz et.
    1. Tedarikçi Firmayı Bul.
    2. Kalemleri listele.
    3. ÖNEMLİ: Hem BİRİM FİYATI hem de TOPLAM MİKTARI (KG/L/Adet) bul.
       - Eğer "5 Koli x 10 KG" ise Toplam Miktar = 50 KG.
       - Birim Fiyat her zaman KG/Litre fiyatı olsun.
    
    ÇIKTI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT | FATURA TOPLAM MİKTAR
    
    Örnek:
    Alp Et | Kıyma | 450.00 | 50 KG
    
    Markdown kullanma.
    """
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "application/pdf", "data": base64_pdf}}]}], "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

def update_price_list(raw_text):
    client, err = get_gspread_client()
    if not client: return False, err
    try:
        sh = client.open(SHEET_NAME)
        # Yeni sütunlu başlık (E Sütunu: KALAN KOTA)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 5, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "GÜNCELLEME TARİHİ", "KALAN KOTA"])
        
        existing_data = ws.get_all_values()
        product_map = {}
        existing_companies = set()
        
        # Veritabanını haritala
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                
                # Mevcut kotayı al (Yoksa 0)
                current_quota = 0.0
                if len(row) >= 5: current_quota = clean_number(row[4])
                
                product_map[f"{k_firma}|{k_urun}"] = {"row": idx + 1, "quota": current_quota}
                existing_companies.add(row[0])
        
        existing_companies_list = list(existing_companies)
        updates_batch, new_rows_batch = [], []
        cnt_upd, cnt_new = 0, 0
        
        lines = raw_text.split('\n')
        for line in lines:
            line = line.replace("*", "").replace("- ", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 4: parts.append("0") # 4. Sütun Miktar
                
                if clean_number(parts[2]) == 0: continue
                
                raw_supplier = parts[0]
                target_supplier = resolve_company_name(raw_supplier, client, existing_companies_list)
                raw_prod = parts[1].strip()
                final_prod = resolve_product_name(raw_prod, client)
                fiyat = clean_number(parts[2])
                
                # Faturadan gelen miktar (KREDİ)
                gelen_miktar = clean_number(parts[3])
                
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
                
                if key in product_map:
                    # GÜNCELLEME: Fiyatı değiş, Kotayı EKLE (Eski + Yeni)
                    item_data = product_map[key]
                    row_idx = item_data['row']
                    new_total_quota = item_data['quota'] + gelen_miktar
                    
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]}) # Fiyat
                    updates_batch.append({'range': f'D{row_idx}', 'values': [[bugun]]}) # Tarih
                    updates_batch.append({'range': f'E{row_idx}', 'values': [[new_total_quota]]}) # Kota
                    cnt_upd += 1
                else:
                    # YENİ ÜRÜN: Kotası gelen miktar kadar
                    new_rows_batch.append([target_supplier, final_prod, fiyat, bugun, gelen_miktar])
                    cnt_new += 1
        
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        return True, f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi. (Stoklar artırıldı)"
    except Exception as e: return False, str(e)

# ==========================================
# MODÜL 3: MENÜ PLANLAYICI (AYNI)
# ==========================================
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

# ==========================================
# NAVIGASYON & MAIN UI
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP V18")
        if st.button("🔒 Güvenli Çıkış"):
            st.session_state.clear()
            st.rerun()
        page = st.radio("Menü", ["📝 Günlük İrsaliye", "🧾 Fatura & Fiyatlar", "📅 Menü Planlayıcı"])
        st.divider()
        models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
        sel_model = st.selectbox("Yapay Zeka", models)

    if page == "📝 Günlük İrsaliye":
        st.header("📝 İrsaliye Girişi")
        f = st.file_uploader("İrsaliye Yükle", type=['jpg', 'png', 'jpeg'])
        if f:
            img = Image.open(f)
            st.image(img, width=300)
            if st.button("Analiz Et"):
                with st.spinner("Okunuyor..."):
                    s, r = analyze_receipt_image(img, sel_model)
                    st.session_state['res'] = r
            if 'res' in st.session_state:
                with st.form("save"):
                    ed = st.text_area("Veriler", st.session_state['res'], height=150)
                    if st.form_submit_button("Kaydet (Stoktan Düş)"):
                        s, m = save_receipt_smart(ed)
                        if s: st.success(m); del st.session_state['res']
                        else: st.error(m)

    elif page == "🧾 Fatura & Fiyatlar":
        st.header("🧾 Fiyat & Stok Güncelleme")
        st.info("Faturadaki miktarlar stok kotasına EKLENİR.")
        pdf = st.file_uploader("PDF Fatura", type=['pdf'])
        if pdf:
            if st.button("Analiz Et"):
                with st.spinner("Okunuyor..."):
                    s, r = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['inv'] = r
            if 'inv' in st.session_state:
                with st.form("upd"):
                    ed = st.text_area("Algılanan", st.session_state['inv'], height=200)
                    if st.form_submit_button("İşle (Stoka Ekle)"):
                        s, m = update_price_list(ed)
                        if s: st.success(m); del st.session_state['inv']
                        else: st.error(m)

    elif page == "📅 Menü Planlayıcı":
        st.header("👨‍🍳 Şefin Defteri")
        col1, col2 = st.columns(2)
        with col1:
            aylar = {1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran", 
                     7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"}
            secilen_ay = st.selectbox("Ay", list(aylar.keys()), format_func=lambda x: aylar[x], index=datetime.now().month - 1)
            year = datetime.now().year
        with col2:
            ogrenci = st.number_input("Öğrenci", value=200)
        st.write("🏖️ **Tatil Günleri**")
        holiday_range = st.date_input("Tatil Aralığı", [], min_value=datetime(year, 1, 1), max_value=datetime(year, 12, 31))
        holidays = []
        if len(holiday_range) == 2: holidays.append((holiday_range[0], holiday_range[1]))
        st.write("🍪 **Hazır Ara Öğün**")
        days_map = {0:"Pazartesi", 1:"Salı", 2:"Çarşamba", 3:"Perşembe", 4:"Cuma", 5:"Cumartesi", 6:"Pazar"}
        selected_snack = st.multiselect("Hangi günler hazır?", list(days_map.keys()), format_func=lambda x: days_map[x], default=[5, 6])
        if st.button("🚀 Menü Oluştur", type="primary"):
            client, _ = get_gspread_client()
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
                workbook = writer.book
                worksheet = writer.sheets['Menu']
                worksheet.set_column('A:G', 20, workbook.add_format({'num_format': '@'}))
            st.download_button("📥 Excel İndir", output.getvalue(), f"Menu_{aylar[secilen_ay]}.xlsx")

if __name__ == "__main__":
    main()
