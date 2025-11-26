import streamlit as st
import sys
import os
import pandas as pd # Dashboard grafikleri için
from modules.utils import check_password, fetch_google_models, FILE_FINANS, SHEET_YATILI # FILE_FINANS eklendi

# Yolu ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# Modül importları
try:
    from modules.utils import check_password, fetch_google_models, SHEET_YATILI, SHEET_GUNDUZLU
    from modules import irsaliye, fatura, menu, finans
except ImportError as e:
    st.error(f"🚨 MODÜL HATASI: {e}")
    st.stop()

# --- AYARLAR ---
st.set_page_config(page_title="Mutfak ERP", layout="wide", page_icon="🍳")

# 1. GÜVENLİK
if not check_password():
    st.stop()

# 2. KENAR ÇUBUĞU
with st.sidebar:
    st.title("🍳 Mutfak ERP")
    st.caption("Yönetici Paneli v1.1")
    
    page = st.radio("Modül Seç", [
        "🏠 Ana Sayfa",
        "📝 Tüketim Fişi (İrsaliye)", 
        "🧾 Fatura & Fiyat Girişi", 
        "📅 Menü Planlayıcı",
        "💰 Öğrenci Finans"
    ])
    
    st.divider()
    
    # Model Seçimi
    favorite_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    
    if st.button("🔄 Modelleri Güncelle"):
        fetched = fetch_google_models()
        if fetched:
            st.session_state['model_list'] = sorted(list(set(favorite_models + fetched)))
            st.success("Güncellendi!")
            
    current_list = st.session_state.get('model_list', favorite_models)
    def_ix = 0
    if "models/gemini-2.5-flash" in current_list:
        def_ix = current_list.index("models/gemini-2.5-flash")
        
    sel_model = st.selectbox("🤖 AI Modeli:", current_list, index=def_ix)
    
    st.markdown("---")
    if st.button("🔒 Çıkış Yap"):
        st.session_state.clear()
        st.rerun()

# 3. SAYFA YÖNLENDİRME & DASHBOARD
if page == "🏠 Ana Sayfa":
    st.header("📊 Genel Bakış")
    st.markdown("Hoş geldin Hocam. İşte durum özeti:")
    
    # Dashboard Verilerini Çek
    col1, col2, col3 = st.columns(3)
    
    # Finans verilerini çekmek için:
    try:
        client = modules.utils.get_gspread_client() # Client al
        sh = client.open(FILE_FINANS) # Finans dosyasını aç
        ws = sh.worksheet(SHEET_YATILI)
        df_yatili = pd.DataFrame(ws.get_all_records())
    except:
        df_yatili = pd.DataFrame()
    
    toplam_beklenti = 0
    toplam_tahsilat = 0
    ogrenci_sayisi = 0
    
    if not df_yatili.empty:
        # Sayısal dönüşüm
        for col in ['Toplam_Yillik_Ucret', 'Odenen_Toplam']:
             if col in df_yatili.columns:
                 df_yatili[col] = pd.to_numeric(df_yatili[col], errors='coerce').fillna(0)
        
        toplam_beklenti = df_yatili['Toplam_Yillik_Ucret'].sum()
        toplam_tahsilat = df_yatili['Odenen_Toplam'].sum()
        ogrenci_sayisi = len(df_yatili)
        kalan_alacak = toplam_beklenti - toplam_tahsilat
        tahsilat_orani = (toplam_tahsilat / toplam_beklenti * 100) if toplam_beklenti > 0 else 0

    with col1:
        st.metric("👨‍🎓 Yatılı Öğrenci", f"{ogrenci_sayisi} Kişi")
        
    with col2:
        st.metric("💰 Toplam Tahsilat", f"{toplam_tahsilat:,.0f} ₺", delta=f"%{tahsilat_orani:.1f} Tahsil edildi")
        
    with col3:
        st.metric("📉 Beklenen Alacak", f"{kalan_alacak:,.0f} ₺", delta_color="inverse")

    st.divider()
    
    # Hızlı Erişim Butonları
    c1, c2 = st.columns(2)
    with c1:
        st.info("💡 **İpucu:** Mutfaktan çıkan malzemeleri 'Tüketim Fişi'nden, yeni gelen malzemeleri 'Fatura'dan gir.")
    with c2:
        if st.button("📂 Google Drive Klasörünü Aç"):
            st.markdown("[Drive'a Git](https://drive.google.com)", unsafe_allow_html=True)

elif page == "📝 Tüketim Fişi (İrsaliye)":
    irsaliye.render_page(sel_model)

elif page == "🧾 Fatura & Fiyat Girişi":
    fatura.render_page(sel_model)

elif page == "📅 Menü Planlayıcı":
    menu.render_page(sel_model)

elif page == "💰 Öğrenci Finans":
    finans.render_page(sel_model)
