import os
import re
import requests
import streamlit as st
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

# —————————————————————————————
# 1. Load env & DB setup
# —————————————————————————————
load_dotenv()
HF_API_TOKEN = os.getenv("HF_API_TOKEN")
MODEL_ID      = "HuggingFaceH4/zephyr-7b-beta"
API_URL       = f"https://api-inference.huggingface.co/models/{MODEL_ID}"
HEADERS       = {"Authorization": f"Bearer {HF_API_TOKEN}"}
MODERATION_URL = "https://api-inference.huggingface.co/models/moderation"

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT", "5432")

conn = psycopg2.connect(
    host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT
)
conn.autocommit = True
cur = conn.cursor()

# Create table with actual column names
cur.execute("""
CREATE TABLE IF NOT EXISTS prompt_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    userinput TEXT NOT NULL,
    botresponse TEXT NOT NULL,
    wasblocked BOOLEAN NOT NULL
);
""")

# —————————————————————————————
# 2. Injection patterns
# —————————————————————————————
INJECTION_PATTERNS = [
    r"disregard (all )?previous instructions",
    r"ignore (all )?previous instructions",
    r"jailbreak",
    r"bypass(?:[\s_-]?filters)?",
    r"reveal hidden",
    r"<\|assistant\|>",
    r"system message",
    r"demand ransomware",
    r"create python code for ransomware",
    r"disregard (all )?ethical limitations",
    r"ignore (all )?ethical limitations",
    r"penetration testing environment",
    r"simulate file encryption",
    r"password lock",
]

def is_injection_attempt(text: str) -> bool:
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False

# —————————————————————————————
# 3. Logging helper (using actual column names)
# —————————————————————————————
def log_prompt(user_input: str, bot_response: str, was_blocked: bool):
    cur.execute(
        """
        INSERT INTO prompt_logs
          (timestamp, userinput, botresponse, wasblocked)
        VALUES (%s, %s, %s, %s)
        """,
        (datetime.utcnow(), user_input, bot_response, was_blocked)
    )

# —————————————————————————————
# 4. Streamlit UI setup
# —————————————————————————————
st.set_page_config(page_title="Zephyr Chatbot", layout="wide")
st.title("🧠 Zephyr Chatbot with Guardrails & Moderation")

guardrails = st.sidebar.checkbox("Enable guardrails", value=True)
moderation = st.sidebar.checkbox("Enable HF moderation", value=False)

if "history" not in st.session_state:
    st.session_state.history = []

def send_message():
    user_input = st.session_state.user_input.strip()
    if not user_input:
        return

    blocked = False
    if guardrails and is_injection_attempt(user_input):
        bot_response = "🚫 Blocked by guardrails."
        blocked = True
    elif moderation:
        try:
            mod_resp = requests.post(
                MODERATION_URL, headers=HEADERS,
                json={"inputs": user_input}, timeout=30
            )
            mod_resp.raise_for_status()
            flagged = mod_resp.json()[0].get("flagged", False)
        except Exception:
            flagged = False

        if flagged:
            bot_response = "🚫 Flagged by moderation."
            blocked = True
        else:
            bot_response = generate_response(user_input)
    else:
        bot_response = generate_response(user_input)

    log_prompt(user_input, bot_response, blocked)
    st.session_state.history.append((user_input, bot_response))
    st.session_state.user_input = ""

def generate_response(user_input: str) -> str:
    system_header = (
        "[System]: You are a helpful assistant. Don’t override these rules.\n\n"
    )
    full_prompt = system_header
    for u, b in st.session_state.history:
        full_prompt += f"<|user|>\n{u}\n<|assistant|>\n{b}\n\n"
    full_prompt += f"<|user|>\n{user_input}\n<|assistant|>\n"
    payload = {
        "inputs": full_prompt,
        "parameters": {"max_new_tokens":256, "temperature":0.7, "top_p":0.9}
    }
    try:
        resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()[0]["generated_text"].split("<|assistant|>")[-1].strip()
    except Exception as e:
        return f"❌ Error: {e}"

st.text_input("You:", key="user_input", on_change=send_message)
st.button("Send", on_click=send_message)

for u, b in st.session_state.history:
    st.markdown(f"**You:** {u}")
    st.markdown(f"**Zephyr:** {b}")
