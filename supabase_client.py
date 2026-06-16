import os

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


@st.cache_resource
def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)
