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

st.set_page_config(page_title="Mutfak ERP V13", page_icon="👨‍🍳", layout="wide")

# --- AYARLAR ---
SHEET_NAME = "Mutfak_Takip"
PRICE_SHEET_NAME = "FIYAT_ANAHTARI"
SETTINGS_SHEET_NAME = "AYARLAR"

# --- GOOGLE SHEETS BAĞLANTISI ---
def get_gspread_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client, creds_dict.get("client_email")
    except Exception as e:
        return None, str(e)

# --- YARDIMCI FONKSİYONLAR (ESKİLER AYNI) ---
def fetch_google_models():
    api_key = st.secrets["GOOGLE_API_KEY"]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            return [m['name'] for m in data.get('models', []) if 'generateContent' in m['supportedGenerationMethods']]
        return []
    except: return []

def clean_number(num_str):
    try:
        clean = ''.join(c for c in num_str if c.isdigit() or c in [',', '.'])
        clean = clean.replace(',', '.')
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
        matched_key = matches[0]
        idx = db_keys.index(matched_key)
        return db_list[idx]
    return None

def resolve_company_name(ocr_name, client):
    std_name = standardize_name(ocr_name)
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(SETTINGS_SHEET_NAME)
        except: return std_name
        data = ws.get_all_values()
        alias_map = {}
        for row in data[1:]:
            if len(row) >= 2:
                alias_map[turkish_lower(row[0])] = row[1].strip()
        key = turkish_lower(std_name)
        if key in alias_map: return alias_map[key]
        best = find_best_match(std_name, list(alias_map.keys()), cutoff=0.7)
        if best: return alias_map[turkish_lower(best)]
        return std_name
    except: return std_name

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
                variation = turkish_lower(row[2])
                master = row[3].strip()
                if variation and master: product_map[variation] = master
        key = turkish_lower(clean_prod)
        if key in product_map: return product_map[key]
        best = find_best_match(clean_prod, list(product_map.keys()), cutoff=0.85)
        if best: return product_map[turkish_lower(best)]
        return clean_prod
    except: return clean_prod

def get_price_database(client):
    price_db = {}
    try:
        sh = client.open(SHEET_NAME)
        try: ws = sh.worksheet(PRICE_SHEET_NAME)
        except: return {}
        data = ws.get_all_values()
        for row in data[1:]:
            if len(row) >= 3:
                ted = standardize_name(row[0])
                urn = row[1].strip()
                fyt = clean_number(row[2])
                if ted not in price_db: price_db[ted] = {}
                price_db[ted][urn] = fyt
        return price_db
    except: return {}

# ==========================================
# MODÜL 1 & 2 (İRSALİYE VE FATURA - AYNI KALDI)
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
    ÇIKTI: TEDARİKÇİ | TARİH (GG.AA.YYYY) | ÜRÜN ADI | MİKTAR | BİRİM FİYAT | TOPLAM TUTAR
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
    price_db = get_price_database(client)
    try:
        sh = client.open(SHEET_NAME)
        existing_sheets = {turkish_lower(ws.title): ws for ws in sh.worksheets()}
        firm_data = {}
        for line in raw_text.split('\n'):
            line = line.replace("*", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 6: parts.append("0")
                ocr_raw_name = parts[0]
                final_firma = resolve_company_name(ocr_raw_name, client)
                tarih = parts[1]
                ocr_urun = parts[2]
                final_urun = resolve_product_name(ocr_urun, client)
                miktar, fiyat, tutar = parts[3], parts[4], parts[5]
                f_val = clean_number(fiyat)
                if f_val == 0 and final_firma in price_db:
                    prods = list(price_db[final_firma].keys())
                    match_prod = find_best_match(final_urun, prods, cutoff=0.7)
                    if match_prod:
                        f_val = price_db[final_firma][match_prod]
                        fiyat = str(f_val)
                        final_urun = match_prod 
                        m_val = clean_number(miktar)
                        tutar = f"{m_val * f_val:.2f}"
                if final_firma not in firm_data: firm_data[final_firma] = []
                firm_data[final_firma].append([tarih, final_urun, miktar, fiyat, tutar])
        msg = []
        for firma, rows in firm_data.items():
            fn = turkish_lower(firma)
            if fn in existing_sheets: ws = existing_sheets[fn]
            else:
                ws = sh.add_worksheet(title=firma, rows=1000, cols=10)
                ws.append_row(["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM FİYAT", "TOPLAM TUTAR"])
                existing_sheets[fn] = ws
            ws.append_rows(rows)
            msg.append(f"{firma}: {len(rows)}")
        return True, " | ".join(msg) + " eklendi."
    except Exception as e: return False, str(e)

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
    2. Kalemlerin BİRİM FİYATLARINI (KDV Hariç) çıkar.
    ÇIKTI: TEDARİKÇİ | ÜRÜN ADI | GÜNCEL BİRİM FİYAT
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
        try: ws = sh.worksheet(PRICE_SHEET_NAME)
        except: 
            ws = sh.add_worksheet(title=PRICE_SHEET_NAME, rows=1000, cols=5)
            ws.append_row(["TEDARİKÇİ", "ÜRÜN ADI", "BİRİM FİYAT", "GÜNCELLEME TARİHİ"])
        existing_data = ws.get_all_values()
        product_map = {}
        for idx, row in enumerate(existing_data):
            if idx == 0: continue
            if len(row) >= 2:
                k_firma = turkish_lower(row[0])
                k_urun = turkish_lower(row[1])
                product_map[f"{k_firma}|{k_urun}"] = idx + 1
        updates_batch, new_rows_batch = [], []
        cnt_upd, cnt_new = 0, 0
        lines = raw_text.split('\n')
        for line in lines:
            line = line.replace("*", "").replace("- ", "").strip()
            if "|" in line:
                parts = [p.strip() for p in line.split('|')]
                if "TEDARİKÇİ" in parts[0].upper(): continue
                while len(parts) < 3: parts.append("0")
                if clean_number(parts[2]) == 0: continue
                raw_supplier = parts[0]
                target_supplier = resolve_company_name(raw_supplier, client)
                raw_prod = parts[1].strip()
                final_prod = resolve_product_name(raw_prod, client)
                fiyat = clean_number(parts[2])
                bugun = datetime.now().strftime("%d.%m.%Y")
                key = f"{turkish_lower(target_supplier)}|{turkish_lower(final_prod)}"
                if key in product_map:
                    row_idx = product_map[key]
                    updates_batch.append({'range': f'C{row_idx}', 'values': [[fiyat]]})
                    updates_batch.append({'range': f'D{row_idx}', 'values': [[bugun]]})
                    cnt_upd += 1
                else:
                    new_rows_batch.append([target_supplier, final_prod, fiyat, bugun])
                    cnt_new += 1
        if updates_batch: ws.batch_update(updates_batch)
        if new_rows_batch: ws.append_rows(new_rows_batch)
        return True, f"✅ {cnt_upd} güncellendi, {cnt_new} eklendi."
    except Exception as e: return False, str(e)

# ==========================================
# MODÜL 3: MENÜ PLANLAYICI (ŞEFİN DEFTERİ)
# ==========================================
def generate_monthly_menu(month, year, student_count, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name.replace("models/", "")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""
    Sen profesyonel bir okul/pansiyon aşçıbaşısısın.
    GÖREV: {year} {month} ayı için 30 günlük (veya ay kaç günse) yemek menüsü hazırla.
    
    HEDEF KİTLE: {student_count} kişilik Lise Öğrencisi (Gelişme çağı, yüksek enerji ihtiyacı).
    
    KURALLAR:
    1. KAHVALTI: 'Standart Kahvaltı (12 Çeşit)' sabit yaz, yanına her gün 1 tane ekstra sıcak/farklı ürün ekle (Yumurta, Menemen, Omlet, Pankek vb.).
    2. ÖĞLE YEMEĞİ: 4 Kap (Çorba + Ana Yemek + Yan Yemek + Tatlı/Meyve/İçecek).
    3. AKŞAM YEMEĞİ: 4 Kap.
       - ÖNEMLİ: Öğle yemeğindeki Çorba, Yan Yemek ve Tatlı AKŞAM DA AYNI KALABİLİR (İsrafı önlemek için).
       - AMA: Ana Yemek (Protein kaynağı) akşamları mutlaka ÖĞLEDEN FARKLI olmalı.
    4. ARA ÖĞÜN: Gece için 1 çeşit (Meyve, Kek, Süt, Galeta vb.).
    5. MEVSİMSELLİK: {month} ayının sebze/meyvelerini kullan.
    6. DENGELİ BESLENME: Protein, Karbonhidrat dengesini gözet.
    
    ÇIKTI FORMATI (CSV Formatında, başlıklarıyla):
    GÜN | KAHVALTI EKSTRA | ÖĞLE ÇORBA | ÖĞLE ANA | ÖĞLE YAN | ÖĞLE EKSTRA | AKŞAM ANA | ARA ÖĞÜN
    
    Örnek Satır:
    1 | Haşlanmış Yumurta | Mercimek Çorbası | Orman Kebabı | Pirinç Pilavı | Elma | Tavuk Sote | Muz
    2 | Menemen | Ezogelin Çorba | Kuru Fasulye | Bulgur Pilavı | Turşu | Köfte Patates | Süt ve Kek
    
    Sadece CSV verisini ver, açıklama yapma.
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200: return False, response.text
        return True, response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e: return False, str(e)

# ==========================================
# UI NAVIGASYON
# ==========================================
def main():
    with st.sidebar:
        st.title("Mutfak ERP V13")
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
                    if st.form_submit_button("Kaydet"):
                        s, m = save_receipt_smart(ed)
                        if s: st.success(m); del st.session_state['res']
                        else: st.error(m)

    elif page == "🧾 Fatura & Fiyatlar":
        st.header("🧾 Fiyat Güncelleme")
        st.info("PDF Fatura yükleyerek maliyetleri güncelle.")
        pdf = st.file_uploader("PDF Fatura", type=['pdf'])
        if pdf:
            if st.button("Analiz Et"):
                with st.spinner("PDF Okunuyor..."):
                    s, r = analyze_invoice_pdf(pdf, sel_model)
                    st.session_state['inv'] = r
            if 'inv' in st.session_state:
                with st.form("upd"):
                    ed = st.text_area("Algılanan", st.session_state['inv'], height=200)
                    if st.form_submit_button("Fiyatları İşle"):
                        s, m = update_price_list(ed)
                        if s: st.success(m); del st.session_state['inv']
                        else: st.error(m)

    elif page == "📅 Menü Planlayıcı":
        st.header("👨‍🍳 Şefin Defteri (Aylık Menü)")
        
        col1, col2 = st.columns(2)
        with col1:
            aylar = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
            secilen_ay = st.selectbox("Ay Seçin", aylar, index=datetime.now().month - 1)
        with col2:
            ogrenci_sayisi = st.number_input("Öğrenci Sayısı", value=200, step=10)
            
        if st.button("👩‍🍳 Menüyü Oluştur (Yapay Zeka)", type="primary"):
            with st.spinner(f"{secilen_ay} ayı için lezzetli ve dengeli bir menü hazırlanıyor..."):
                year = datetime.now().year
                success, raw_csv = generate_monthly_menu(secilen_ay, year, ogrenci_sayisi, sel_model)
                
                if success:
                    st.session_state['menu_csv'] = raw_csv
                    st.balloons()
                else:
                    st.error(f"Hata: {raw_csv}")
        
        if 'menu_csv' in st.session_state:
            st.info("💡 Menü oluşturuldu! Aşağıdaki tablodan değişiklik yapabilir ve Excel olarak indirebilirsiniz.")
            
            # CSV'yi DataFrame'e çevir (Görselleştirme ve Düzenleme için)
            try:
                # Bazen AI ```csv gibi tagler koyar, temizleyelim
                clean_csv = st.session_state['menu_csv'].replace("```csv", "").replace("```", "").strip()
                
                # Veriyi satır satır oku ve "|" veya "," ile ayır
                rows = []
                lines = clean_csv.split('\n')
                header = [h.strip() for h in lines[0].split('|')] if '|' in lines[0] else [h.strip() for h in lines[0].split(',')]
                
                for line in lines[1:]:
                    if not line.strip(): continue
                    parts = [p.strip() for p in line.split('|')] if '|' in line else [p.strip() for p in line.split(',')]
                    # Sütun sayısı eşleşmezse boşlukla doldur
                    while len(parts) < len(header): parts.append("")
                    rows.append(parts[:len(header)])
                
                df = pd.DataFrame(rows, columns=header)
                
                # EDİTÖR (Elle Düzeltme İmkanı)
                edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
                
                # EXCEL İNDİRME
                # Pandas dataframe'i Excel bytes'a çevir
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    edited_df.to_excel(writer, sheet_name=f'{secilen_ay}_Menu', index=False)
                
                st.download_button(
                    label="📥 Menüyü Excel Olarak İndir (Aşçıya Gönder)",
                    data=output.getvalue(),
                    file_name=f"{secilen_ay}_Yemek_Menusu.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
            except Exception as e:
                st.error("Tablo oluşturulurken hata oldu, ham metin aşağıdadır:")
                st.text_area("Ham Veri", st.session_state['menu_csv'])

if __name__ == "__main__":
    main()
