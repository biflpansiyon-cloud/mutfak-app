import streamlit as st
import sys
import os

# --- NAVIGASYON AYARI (KRİTİK HAMLE) ---
# Bu kod, uygulamanın çalıştığı klasörü Python'un "gözlüklerine" ekler.
# Böylece 'modules' klasörünü net bir şekilde görür.
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# --- İMPORTLAR ŞİMDİ GÜVENLİ ---
try:
    from modules.utils import check_password
    from modules import irsaliye, fatura, menu, finans
except ImportError as e:
    # Eğer hala bulamazsa hatayı ekrana basalım ki sebebini görelim
    st.error(f"Modül Hatası: {e}")
    st.stop()

# Sayfa Ayarı
st.set_page_config(page_title="Mutfak ERP Modüler", page_icon="💎", layout="wide")

# ... (Kodun geri kalanı aynı devam etsin) ...

# 1. Güvenlik
if not check_password():
    st.stop()

# 2. Kenar Çubuğu
with st.sidebar:
    st.title("Mutfak ERP")
    if st.button("🔒 Çıkış"):
        st.session_state.clear()
        st.rerun()
        
    page = st.radio("Modül Seç", [
        "📝 Günlük İrsaliye", 
        "🧾 Fatura & Fiyat", 
        "📅 Menü Planlayıcı",
        "💰 Öğrenci Finans"
    ])
    
    st.divider()
    models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    sel_model = st.selectbox("Yapay Zeka", models)

# 3. Yönlendirme
if page == "📝 Günlük İrsaliye":
    irsaliye.render_page(sel_model)

elif page == "🧾 Fatura & Fiyat":
    fatura.render_page(sel_model)

elif page == "📅 Menü Planlayıcı":
    menu.render_page(sel_model)

elif page == "💰 Öğrenci Finans":
    finans.render_page(sel_model)
