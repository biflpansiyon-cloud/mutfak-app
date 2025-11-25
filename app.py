import streamlit as st
import sys
import os

# --- AJAN KODU (DEBUGGER) ---
# Bu kısım, sunucunun hangi klasörde olduğunu ve yanında neleri gördüğünü ekrana basacak.
st.write("📂 **Mevcut Çalışma Yolu:**", os.getcwd())
st.write("📂 **Bu Klasördeki Dosyalar:**", os.listdir())

# Yolu zorla ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# Modülleri şimdi çağır
try:
    import modules
    st.success("✅ 'modules' klasörü bulundu!")
    from modules.utils import check_password, fetch_google_models
    from modules import irsaliye, fatura, menu, finans
except ImportError as e:
    st.error(f"🚨 MODÜL HATASI DEVAM EDİYOR: {e}")
    st.stop()

# --- AYARLAR ---
st.set_page_config(page_title="Mutfak ERP", layout="wide")

if not check_password():
    st.stop()

# ... (Kodun geri kalanı aynı) ...

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
    st.header("⚙️ Model Ayarı")
    
    # Favori modellerimiz (İnternet yoksa veya API hatası varsa bunlar görünür)
    favorite_models = ["models/gemini-2.5-flash", "models/gemini-exp-1206", "models/gemini-1.5-flash"]
    
    # 1. Güncelleme Butonu
    if st.button("Listeyi Google'dan Güncelle"):
        fetched = fetch_google_models() # Utils'den çağırıyoruz
        if fetched:
            # Favorilerle gelenleri birleştirip session'a atıyoruz
            st.session_state['model_list'] = sorted(list(set(favorite_models + fetched)))
            st.success("Liste güncellendi!")
    
    # 2. Listeyi Belirle (Session'da varsa onu kullan, yoksa favorileri)
    current_list = st.session_state.get('model_list', favorite_models)
    
    # 3. Varsayılan Seçim (2.5 Flash varsa onu seçili getir)
    def_ix = 0
    if "models/gemini-2.5-flash" in current_list:
        def_ix = current_list.index("models/gemini-2.5-flash")
        
    sel_model = st.selectbox("Model Seç:", current_list, index=def_ix)

# 3. Yönlendirme
if page == "📝 Günlük İrsaliye":
    irsaliye.render_page(sel_model)

elif page == "🧾 Fatura & Fiyat":
    fatura.render_page(sel_model)

elif page == "📅 Menü Planlayıcı":
    menu.render_page(sel_model)

elif page == "💰 Öğrenci Finans":
    finans.render_page(sel_model)
