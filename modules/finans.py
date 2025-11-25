import streamlit as st
import pandas as pd
from modules.utils import get_gspread_client, SHEET_YATILI, SHEET_GUNDUZLU

def get_data(sheet_name):
    """Google Sheets'ten veriyi çeker ve DataFrame'e çevirir."""
    try:
        client = get_gspread_client()
        sh = client.open("Mutfak_Takip") # Ana dosya adın
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return df
    except Exception as e:
        st.error(f"Veri çekme hatası ({sheet_name}): {e}")
        return pd.DataFrame()

def render_page(selected_model):
    st.header("💰 Finans Yönetimi")
    st.info(f"Aktif Model: {selected_model} (Şu an sadece listeleme modundayız)")

    tab1, tab2, tab3 = st.tabs(["🏫 Paralı Yatılı (Taksit)", "🍽️ Gündüzlü (Yemek)", "🤖 Dekont İşle (AI)"])

    # --- TAB 1: PARALI YATILI ---
    with tab1:
        st.subheader("Taksit Takip Çizelgesi")
        df_yatili = get_data(SHEET_YATILI)
        
        if not df_yatili.empty:
            # --- VERİ TEMİZLİĞİ (GÜNCELLEME BURADA) ---
            # Para içeren sütunları sayıya çevir, hata verirse (boşsa) 0 yap
            para_sutunlari = ['Toplam_Yillik_Ucret', 'Odenen_Toplam', 'Kalan_Borc']
            for col in para_sutunlari:
                if col in df_yatili.columns:
                    # Boş stringleri NaN yap, sonra sayıya çevir, en son 0 ile doldur
                    df_yatili[col] = pd.to_numeric(df_yatili[col], errors='coerce').fillna(0)

            # Özet Kartlar
            col1, col2 = st.columns(2)
            # Sütun yoksa 0 varsay
            toplam_borc = df_yatili['Toplam_Yillik_Ucret'].sum() if 'Toplam_Yillik_Ucret' in df_yatili.columns else 0.0
            toplam_odenen = df_yatili['Odenen_Toplam'].sum() if 'Odenen_Toplam' in df_yatili.columns else 0.0
            
            # Artık toplam_borc kesinlikle sayı olduğu için hata vermez
            col1.metric("Toplam Beklenen Gelir", f"{toplam_borc:,.2f} ₺")
            col2.metric("Tahsil Edilen", f"{toplam_odenen:,.2f} ₺", delta=f"{toplam_odenen - toplam_borc:,.2f} ₺")
            
            st.dataframe(df_yatili, use_container_width=True)
        else:
            st.warning(f"'{SHEET_YATILI}' sayfasında veri bulunamadı veya sütun başlıkları hatalı.")

    # --- TAB 2: GÜNDÜZLÜ YEMEK ---
    with tab2:
        st.subheader("Aylık Yemek Ücretleri")
        df_gunduzlu = get_data(SHEET_GUNDUZLU)
        
        if not df_gunduzlu.empty:
             # --- VERİ TEMİZLİĞİ ---
            if 'Toplam_Tutar' in df_gunduzlu.columns:
                 df_gunduzlu['Toplam_Tutar'] = pd.to_numeric(df_gunduzlu['Toplam_Tutar'], errors='coerce').fillna(0)

            # Filtreleme
            if 'Ay' in df_gunduzlu.columns:
                aylar = df_gunduzlu['Ay'].unique()
                if len(aylar) > 0:
                    secilen_ay = st.selectbox("Dönem Seçiniz:", aylar)
                    df_goster = df_gunduzlu[df_gunduzlu['Ay'] == secilen_ay]
                else:
                    df_goster = df_gunduzlu
            else:
                df_goster = df_gunduzlu
                
            st.dataframe(df_goster, use_container_width=True)
        else:
            st.warning(f"'{SHEET_GUNDUZLU}' sayfasında veri bulunamadı.")

    # --- TAB 3: AI DEKONT İŞLEME ---
    with tab3:
        st.subheader("🤖 Gemini ile Dekont Analizi")
        st.write("Drive'daki 'Finans/Gelen_Dekontlar' klasöründeki dosyalar burada taranacak.")
        
        if st.button("Drive'ı Tara ve Dekontları Analiz Et"):
            st.warning("⚠️ Bu özellik bir sonraki adımda aktif edilecek. Önce Sheets yapısını doğrulayalım!")
