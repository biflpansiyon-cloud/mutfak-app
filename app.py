import streamlit as st
from utils import check_password

# Sayfa Ayarları
st.set_page_config(page_title="Mutfak ERP V21", page_icon="🏛️", layout="wide")

# 1. Güvenlik Kontrolü (Utils'den gelir)
if not check_password():
    st.stop()

# 2. Modülleri Çağır
from modules import irsaliye, fatura, menu, finans

# 3. Yan Menü (Navigasyon)
with st.sidebar:
    st.title("Mutfak ERP")
    if st.button("🔒 Güvenli Çıkış"):
        st.session_state.clear()
        st.rerun()
        
    page = st.radio("Menü", [
        "📝 Günlük İrsaliye", 
        "🧾 Fatura & Fiyat", 
        "📅 Menü Planlayıcı",
        "💰 Öğrenci Finans"
    ])
    
    st.divider()
    models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    sel_model = st.selectbox("Yapay Zeka Modeli", models)

# 4. Sayfa Yönlendirme (Trafik Polisi)
if page == "📝 Günlük İrsaliye":
    irsaliye.render_page(sel_model)

elif page == "🧾 Fatura & Fiyat":
    fatura.render_page(sel_model)

elif page == "📅 Menü Planlayıcı":
    menu.render_page(sel_model)

elif page == "💰 Öğrenci Finans":
    finans.render_page(sel_model)
