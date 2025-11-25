import streamlit as st
from googleapiclient.http import MediaIoBaseDownload
from datetime import datetime
import io
import base64
import json
import requests
from modules.utils import *

# ID'leri buraya taşıdık
YATILI_FOLDER_ID = "1xxxxx-SENIN-YATILI-ID-xxxxx"
GUNDUZLU_FOLDER_ID = "1xxxxx-SENIN-GUNDUZLU-ID-xxxxx"

def process_yatili_batch(client, service, folder_id, model_name):
    # ... (V21'deki process_yatili_batch içeriği) ...
    pass

def render_page(sel_model):
    st.header("💰 Öğrenci Dekont Takibi")
    # ... (UI Kodları) ...
