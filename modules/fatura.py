import streamlit as st
import requests
import json
import base64
import pandas as pd
from datetime import datetime
import io

from modules.utils import (
    get_gspread_client, 
    get_or_create_worksheet,
    get_company_list,
    resolve_product_name, 
    clean_number, 
    FILE_STOK, 
    PRICE_SHEET_NAME
)

# --- AI ANALİZ ---
def analyze_invoice_file(uploaded_file, model_name):
    api_key = st.secrets["GOOGLE_API_KEY"]
    clean_model = model_name if "models/" not in model_name else model_name.replace("models/", "")
    
    uploaded_file.seek(0)
    file_bytes = uploaded_file.getvalue()
    base64_data = base64.b64encode(file_bytes).decode('utf-8')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    # PROMPT DEĞİŞTİ: Firma ismini sormuyoruz, sadece ürünleri soruyoruz.
    prompt = """
    Bu FATURAYI analiz et.
    Sadece kalemleri çıkar. Firma ismine veya tarihe bakma.
    
    KURALLAR:
    1. Paket (Koli/Teneke) fiyatını paketin içindeki miktara bölerek KG/LT/ADET başı GERÇEK BİRİM FİYATI bul.
    2. MİKTAR ve FİYATLARI yazarken "Binlik Ayracı" KULLANMA. (1.500 yazma, 1500 yaz). Ondalık için nokta kullan.
    
    ÇIKTI FORMATI:
    ÜRÜN ADI | BİRİM FİYAT (Sayı) | MİKTAR (Sayı) | BİRİM
    """
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": uploaded_file.type, "data": base64_data}}
            ]
        }],
        "safetySettings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}]
    }
    
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload))
        if res.status_code == 200:
            return True, res.json()['candidates'][0]['content']['parts'][0]['text']
        return False, "API Cevap Vermedi"
    except Exception as e: return False, str(e)

def text_to_dataframe_fatura(raw_text):
    data = []
    lines = raw_text.split('\n')
    for line in lines:
        if "---" in line or not line.strip(): continue
        line = line.replace("*", "").strip()
        if "|" in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) > 0 and "ÜRÜN ADI" in parts[0].upper(): continue # Başlık satırı
            if len(parts) < 2: continue
            
            while len(parts) < 4: parts.append("0")
            
            data.append({
                "ÜRÜN ADI": parts[0],
                "BİRİM FİYAT": parts[1],
                "MİKTAR": parts[2],
                "BİRİM": parts[3]
            })
    return pd.DataFrame(data)

# --- VERİTABANI VE KONTROL ---
def check_invoice_duplicate(client, company, date_str):
    """
    Seçilen firmanın kendi sayfasına bakar.
    Eğer o tarihte daha önce 'Fatura Girişi' yapılmışsa True döner.
    """
    try:
        sh = client.open(FILE_STOK)
        # Firma sayfasını bul
        try: ws = sh.worksheet(company)
        except: return False # Sayfa yoksa fatura da yoktur
        
        records = ws.get_all_records() # Başlıklar olması lazım: TARİH, İŞLEM, ...
        # Eğer sayfa boşsa veya başlık yoksa False
        if not records: return False
        
        # Basitçe satırları tara
        # Not: get_all_values() daha güvenli olabilir format hatalarına karşı
        rows = ws.get_all_values()
        for row in rows:
            # Örn: Row[0] = Tarih, Row[5] = İşlem Türü (Aşağıdaki yapıya göre)
            if len(row) > 1:
                row_date = str(row[0]).strip()
                # İşlem türünü bulmamız lazım. Kaydederken son sütuna yazacağız.
                # Şimdilik satırda "Fatura Girişi" yazısı var mı diye bakalım.
                if row_date == date_str and "Fatura Girişi" in row:
                    return True
        return False
    except: return False

def update_price_list_dataframe(df, company, date_obj):
    client = get_gspread_client()
    if not client: return False, "Bağlantı Hatası"
    
    date_str = date_obj.strftime("%d.%m.%Y")
    
    # 1. DUPLICATE KONTROLÜ
    if check_invoice_duplicate(client, company, date_str):
        return False, [f"⛔ HATA: {company} firmasına ait {date_str} tarihli fatura ZATEN GİRİLMİŞ!"]
    
    log_messages = []
    try:
        sh = client.open(FILE_STOK)
        
        # Fiyat Anahtarı (Stok Deposu)
        ws_price = get_or_create_worksheet(sh, PRICE_SHEET_NAME, 7, [])
        price_data = ws_price.get_all_values()
        
        # Firma Sayfası (Cari Ekstresi Gibi)
        # Başlıklar: TARİH | ÜRÜN ADI | MİKTAR | BİRİM | BİRİM FİYAT | TUTAR | İŞLEM TÜRÜ
        ws_company = get_or_create_worksheet(sh, company, 10, ["TARİH", "ÜRÜN ADI", "MİKTAR", "BİRİM", "BİRİM FİYAT", "TUTAR", "İŞLEM TÜRÜ"])
        
        # Mevcut Stok Haritası
        product_map = {}
        for idx, row in enumerate(price_data):
            if idx == 0: continue
            if len(row) >= 2:
                # Key: "FİRMA|ÜRÜN"
                # Artık firma adını manuel seçtiğimiz için, veritabanındaki firma adını da dikkate alarak eşleştiriyoruz
                db_comp = row[0].strip()
                db_prod = row[1].strip()
                # Sadece seçili firmanın ürünlerini haritalayalım
                if db_comp == company:
                    product_map[db_prod.lower()] = {"row": idx + 1, "quota": clean_number(row[5]) if len(row) >= 6 else 0.0}
        
        updates_batch = []
        new_rows_batch = []
        company_log_rows = []
        
        for index, row in df.iterrows():
            raw_prod = str(row["ÜRÜN ADI"])
            # Ürün ismini, sadece o firmanın DB'sinde ara
            final_prod = resolve_product_name(raw_prod, client, company)
            
            fiyat = clean_number(row["BİRİM FİYAT"])
            miktar = clean_number(row["MİKTAR"])
            birim = str(row["BİRİM"]).upper()
            tutar = fiyat * miktar
            
            if fiyat == 0: continue
            
            key = final_prod.lower()
            
            # Güncelleme mi Yeni mi?
            if key in product_map:
                item = product_map[key]
                # FATURA GİRİŞİ -> STOK ARTAR (+)
                new_quota = item['quota'] + miktar
                
                updates_batch.append({'range': f'C{item["row"]}', 'values': [[fiyat]]}) # Yeni Fiyat
                updates_batch.append({'range': f'E{item["row"]}', 'values': [[date_str]]}) # Güncelleme Tarihi
                updates_batch.append({'range': f'F{item["row"]}', 'values': [[new_quota]]}) # Kota Artır
                updates_batch.append({'range': f'G{item["row"]}', 'values': [[birim]]})
                
                log_messages.append(f"➕ EKLENDİ: {final_prod} -> +{miktar} {birim} (Yeni Stok: {new_quota})")
            else:
                # Yeni Ürün (Kota = Miktar)
                new_rows_batch.append([company, final_prod, fiyat, "TL", date_str, miktar, birim])
                log_messages.append(f"✨ YENİ ÜRÜN: {final_prod} ({miktar} {birim})")
            
            # Firma Sayfasına Log (Cari Kaydı)
            company_log_rows.append([
                date_str, 
                final_prod, 
                miktar, 
                birim, 
                fiyat, 
                f"{tutar:.2f}", 
                "Fatura Girişi" # Bu ifade duplicate kontrolü için önemli
            ])
                
        # Toplu İşlemler
        if updates_batch: ws_price.batch_update(updates_batch)
        if new_rows_batch: ws_price.append_rows(new_rows_batch)
        if company_log_rows: ws_company.append_rows(company_log_rows)
        
        return True, log_messages
        
    except Exception as e: return False, [str(e)]

# --- ARAYÜZ ---
def render_page(sel_model):
    st.header("🧾 Fatura Girişi (Alacak/Stok Ekleme)")
    st.info("ℹ️ Fatura girdiğinde firmanın bakiyesi (stok) **ARTAR**.")
    st.markdown("---")
    
    # 1. AYARLAR
    client = get_gspread_client()
    companies = get_company_list(client) if client else []
    
    if not companies:
        st.error("⚠️ Firma listesi boş! Lütfen 'Mutfak_Stok_SatinAlma' dosyasında 'AYARLAR' sekmesine firma isimlerini ekle.")
        st.stop()
        
    c1, c2 = st.columns(2)
    selected_company = c1.selectbox("Firma Seç", companies)
    selected_date = c2.date_input("Fatura Tarihi", datetime.now())

    # 2. DOSYA YÜKLEME
    uploaded_file = st.file_uploader("Fatura Yükle (PDF/Resim)", type=['pdf', 'jpg', 'png', 'jpeg'])
    
    if uploaded_file and st.button("🔍 Faturayı Analiz Et", type="primary"):
        with st.spinner("AI ürünleri okuyor..."):
            s, raw_text = analyze_invoice_file(uploaded_file, sel_model)
            if s:
                st.session_state['fatura_df'] = text_to_dataframe_fatura(raw_text)
            else:
                st.error(f"Hata: {raw_text}")
    
    # 3. KONTROL VE KAYIT
    if 'fatura_df' in st.session_state:
        st.subheader("Ürün Kontrolü")
        edited_df = st.data_editor(st.session_state['fatura_df'], num_rows="dynamic", use_container_width=True)
        
        if st.button("💾 Kaydet ve Stok İşle", type="primary"):
            with st.spinner("Stok artırılıyor ve cariye işleniyor..."):
                success, logs = update_price_list_dataframe(edited_df, selected_company, selected_date)
                
                if success:
                    st.balloons()
                    st.success(f"✅ {selected_company} Faturası başarıyla işlendi!")
                    with st.expander("Detaylar", expanded=True):
                        for log in logs: st.text(log)
                    del st.session_state['fatura_df']
                else:
                    st.error(logs[0]) # Hata mesajını göster
