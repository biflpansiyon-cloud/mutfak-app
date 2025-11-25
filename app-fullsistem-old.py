import streamlit as st
from PIL import Image
import requests
import json
import base64
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import difflib
import pandas as pd
import random
import re

st.set_page_config(page_title="Mutfak ERP V19.1", page_icon="🔥", layout="wide")

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
        # Sadece rakam, nokta, virgül al
        clean = re.sub(r'[^\d.,]', '', str(num_str))
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
    # Gereksiz fatura başlıklarını temizle
    prefixes = ["SAYIN", "ALICI", "MÜŞTERİ", "FİRMA", "CARİ", "FATURA"]
    cleaned = text.upper()
    for p in prefixes:
        cleaned = cleaned.replace(p, "")
    
    cleaned = cleaned.replace("*", "").replace("-", "").strip()
    return " ".join([word.capitalize() for word in cleaned.split()])

def find_best_match(ocr_text, db_list, cutoff=0.55): # Eşik değeri düşürüldü (%55)
    if not ocr_text: return None
    ocr_key = turkish_lower(ocr_text)
    db_keys = [turkish_lower(p) for p in db_list]
    matches = difflib.get_close_matches(ocr_key, db_keys, n=1, cutoff=cutoff)
    if matches:
        matched_key = matches[0]
        idx = db_keys.index(matched_key)
        return db_list[idx]
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
            
            # 1. Tam veya Kısmi Eşleşme (Sözlük)
            key = turkish_lower(std_name)
            if key in alias_map: return alias_map[key]
            for k, v in alias_map.items():
                if k in key: return v # "başar gıda zati" içinde "başar gıda" var mı?
            
            # 2. Benzerlik (Sözlük)
            best = find_best_match(std_name, list(alias_map.keys()), cutoff=0.6)
            if best: return alias_map[turkish_lower(best)]
        except: pass
    except: pass

    # 3. Veritabanı Kontrolü
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
        # Kapsama
        for k, v in product_map.items():
            if k in key: return v
        best = find_best_match(clean_prod, list(product_map.keys()), cutoff=0.8)
        if best: return product_map[turkish_lower(best)]
        return clean_prod
    except: return clean_prod

def get_price_database(client):
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, cols=7, header=["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        data = ws.get_all_values()
        for idx, row in enumerate(data):
            if idx == 0: continue
            if len(row) >= 3:
                ted = standardize_name(row[0])
                urn = row[1].strip()
                fyt = clean_number(row[2])
                kota = 0.0
                if len(row) >= 6: kota = clean_number(row[5])
                kb = ""
                if len(row) >= 7: kb = row[6].strip()
                if ted not in price_db: price_db[ted] = {}
                price_db[ted][urn] = {"fiyat": fyt, "kota": kota, "birim": kb, "row": idx + 1}
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
# MODÜL 1: İRSALİYE
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
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR (Sayı) | BİRİM (KG/L/Adet) | BİRİM FİYAT | TOPLAM TUTAR
    Fiyat yoksa 0 yaz. Miktar ve Birimi ayır. Markdown kullanma.
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
    price_db = get_price_database(client)
    known_companies = list(price_db.keys())
    try:
        sh = client.open(SHEET_NAME)
        price_ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        
        firm_data = {}
        kota_updates = []
        for line in raw_text.split('\n'):
            line = line.replace("*", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 7: parts.append("0")
                
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client, known_companies)
                
                tarih = parts[1]
                urun = parts[2]
                miktar = parts[3]
                birim = parts[4].upper()
                fiyat = parts[5]
                tutar = parts[6]
                
                f_val = clean_number(fiyat)
                final_urun = resolve_product_name(urun, client)
                m_val = clean_number(miktar)
                
                if final_firma in price_db:
                    prods = list(price_db[final_firma].keys())
                    match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                    if match_prod:
                        db_item = price_db[final_firma][match_prod]
                        if f_val == 0:
                            f_val = db_item['fiyat']
                            fiyat = str(f_val)
                            tutar = f"{m_val * f_val:.2f}"
                        final_urun = match_prod
                        
                        # Kota Düş
                        current_kota = db_item['kota']
                        new_kota = current_kota - m_val
                        row_num = db_item['row']
                        kota_updates.append({'range': f'F{row_num}', 'values': [[new_kota]]})
                
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append([tarih, final_urun, miktar, birim, fiyat, "TL", tutar])
        
        msg = []
        for firma, rows in firm_data.items():
            fn = turkish_lower(firma)
            ws = None
            if fn in existing_sheets: ws = existing_sheets[fn]
            else:
                try: ws = get_or_create_worksheet(sh, firma, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "PARA BİRİMİ", "TOPLAM TUTAR"])
                except: pass
            if ws:
                ws.append_rows(rows)
                msg.append(f"{firma}: {len(rows)}")
        
        if kota_updates:
            price_ws.batch_update(kota_updates)
            msg.append(f"(Stok Güncellendi: {len(kota_updates)})")
            
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)

# ==========================================
# MODÜL 2: FATURA (ZIRHLI)
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
    2. Kalemlerin KDV HARİÇ BİRİM FİYATINI bul.
    3. HESAPLAMA: "5KG", "Teneke" gibi paketse, BİRİM FİYATI (KG/LT) hesapla.
    
    ÇIKTI FORMATI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT | MİKTAR (Sayı) | BİRİM (KG/LT)
    Örnek: Alp Et | Kıyma | 450.00 | 50 | KG
    
    Sadece veriyi ver. Başlık yazma. Markdown kullanma.
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
        ws = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, ["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "PARA BİRİMİ", "GÜNCELLEME TARİHİ", "KALAN KOTA", "KOTA BİRİMİ"])
        
        existing_data = ws.get_all_values()
        product_map = {}
        existing_companies = set()
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                
                current_quota = 0.0
                if len(row) >= 6: current_quota = clean_number(row[5])
                
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
                while len(parts) < 5: parts.append("0")
                
                # Fiyat 0 ise atla
                if clean_number(parts[2]) == 0: continue
                
                raw_supplier = parts[0]
                # İsim Çözücü (Sözlük + Veritabanı)
                target_supplier = resolve_company_name(raw_supplier, client, existing_companies_list)
                
                raw_prod = parts[1].strip()
                final_prod = resolve_product_name(raw_prod, client)
                fiyat = clean_number(parts[2])
                miktar = clean_number(parts[3])
                birim = parts[4].strip().upper()
                bugun = datetime.now().strftime("%d.%m.%Y")
                
                key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
                
                if key in product_map:
                    item_data = product_map[key]
                    row_idx = item_data['row']
                    new_total_quota = item_data['quota'] + miktar
                    
                    # Fiyat(C), Tarih(E), Kota(F), Birim(G)
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})
                    updates_batch.append({'range': f'E{row_idx}', 'values': [[bugun]]})
                    updates_batch.append({'range': f'F{row_idx}', 'values': [[new_total_quota]]})
                    updates_batch.append({'range': f'G{row_idx}', 'values': [[birim]]})
                    cnt_upd += 1
                else:
                    new_rows_batch.append([target_supplier, final_prod, fiyat, "TL", bugun, miktar, birim])
                    cnt_new += 1
                    
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        return True, f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi."
    except Exception as e: return False, str(e)

# ==========================================
# MENÜ PLANLAYICI (DEĞİŞMEDİ)
# ==========================================
def generate_smart_menu(month_index, year, pool, holidays, ready_snack_days):
    # (V17 kodu ile aynı, yer kaplamaması için burayı kısa geçiyorum ama senin tam kodda burası dolu olmalı)
    # ... [ÖNCEKİ generate_smart_menu KODU BURAYA] ...
    return pd.DataFrame() # Placeholder

# ==========================================
# UI
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP V19.1")
        if st.button("🔒 Çıkış"): st.session_state.clear(); st.rerun()
        page = st.radio("Menü", ["📝 Günlük İrsaliye", "🧾 Fatura & Fiyatlar", "📅 Menü Planlayıcı"])
        st.divider()
        models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
        sel_model = st.selectbox("Model", models)

    if page == "📝 Günlük İrsaliye":
        st.header("📝 İrsaliye Girişi")
        f = st.file_uploader("İrsaliye", type=['jpg', 'png', 'jpeg'])
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
                    if st.form_submit_button("Kaydet"):
                        s, m = save_receipt_smart(ed)
                        if s: st.success(m); del st.session_state['res']
                        else: st.error(m)

    elif page == "🧾 Fatura & Fiyatlar":
        st.header("🧾 Fiyat Güncelleme")
        st.info("Paket miktarlarını ve firma isimlerini otomatik çözer.")
        pdf = st.file_uploader("PDF Fatura", type=['pdf'])
        if pdf:
            if st.button("Analiz Et"):
                with st.spinner("PDF Okunuyor..."):
                    s, r = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['inv'] = r
            if 'inv' in st.session_state:
                with st.form("upd"):
                    ed = st.text_area("Algılanan", st.session_state['inv'], height=200)
                    if st.form_submit_button("İşle"):
                        s, m = update_price_list(ed)
                        if s: st.success(m); del st.session_state['inv']
                        else: st.error(m)

    elif page == "📅 Menü Planlayıcı":
        st.header("👨‍🍳 Menü Planlayıcı")
        st.warning("Menü modülü V17 kodundan kopyalanmalıdır (Yer tasarrufu için buraya eklenmedi).")

if __name__ == "__main__":
    main()
