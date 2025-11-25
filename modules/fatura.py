import streamlit as st
import requests
import json
import base64
from modules.utils import *

def analyze_invoice_pdf(uploaded_file, model_name):
    # ... (V21'deki analyze_invoice_pdf içeriği) ...
    pass 

def update_price_list(raw_text):
    # ... (V21'deki update_price_list içeriği) ...
    pass

def render_page(sel_model):
    st.header("🧾 Fiyat Güncelleme")
    pdf = st.file_uploader("PDF Fatura", type=['pdf'])
    if pdf:
        if st.button("Analiz Et"):
            # ... (UI Kodları) ...
            pass
