# backend.py
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os, json, datetime, time, traceback

# --------------------------
# CONFIG (hardcoded keys, for Render deploy)
# --------------------------
GEMINI_API_KEY = "AIzaSyB8YVZz-UYA6ILALFOX1ljdnsYgWLiYE_Q"
BOT_EMAIL = "chatbotsozhaatech@gmail.com"
COMPANY_EMAIL = "groupsozhaa@gmail.com"

# --------------------------
# WhatsApp Cloud API config
# --------------------------
WHATSAPP_TOKEN = "EAAQYRWtYvBoBPXiBziwbUnCdZBDtk38MW8FKY4KGao3e74DpqdnEIWf4teUg7SA38vjF1PuW6ntl5l9AKjlPBRGzosKpjeOZA5lwCynDoVwqVQeDZBIdeBZCZB5ZCWTDIYWYFZAEa7Fvqx838CmphYFZCP6lIDpfvOYjFi4OyoHKFCYmTNRU15cOUZB79ENZCKiQZDZD"
WHATSAPP_PHONE_NUMBER_ID = "787754397756112"
COMPANY_WA_NUMBER = "+917094062522"
GRAPH_API_VERSION = "v22.0"
GRAPH_API_BASE = f"https://graph.facebook.com/v22.0"

# --------------------------
# SendGrid Config
# --------------------------
SENDGRID_API_KEY = "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"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

COMPANY_URLS = [
    "https://sozhaa.tech/",
    "https://sozhaa.tech/about",
    "https://sozhaa.tech/services",
    "https://sozhaa.tech/contact",
]

STORAGE_DIR = "chat_data"
TRANSCRIPT_EXCEL = os.path.join(STORAGE_DIR, "sozhaa_full_chat_history.xlsx")
TRANSCRIPT_JSON = os.path.join(STORAGE_DIR, "sozhaa_transcripts.json")

# --------------------------
# Setup
# --------------------------
os.makedirs(STORAGE_DIR, exist_ok=True)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash-8b")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------
# Helpers
# --------------------------
def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"

def fetch_snippets(urls, chars=1500):
    snippets = []
    headers = {"User-Agent": "SozhaaBot/1.0 (+https://sozhaa.tech)"}
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=8)
            r.raise_for_status()
            s = BeautifulSoup(r.text, "html.parser")
            for t in s.select("nav, footer, header, script, style, noscript"):
                t.decompose()
            text = " ".join(s.get_text(" ").split())
            title = (s.title.string.strip() if s.title and s.title.string else u)
            snippets.append({"url": u, "title": title, "text": text[:chars]})
        except Exception as e:
            snippets.append({"url": u, "title": u, "text": f"(failed: {e})"})
    return snippets

def build_system_prompt(snippets):
    context_text = "\n\n".join([f"{p['title']} ({p['url']}):\n{p['text']}" for p in snippets])
    return (
        "You are Sozhaa Tech AI Assistant. Use only the company's information provided below "
        "(sozhaa.tech). Answer only about the company, services, pages and contact information. "
        "If asked outside scope, reply politely that you only provide Sozhaa Tech info. "
        "Keep replies concise.'.\n\n"
        "Company context:\n" + context_text + "\n\n"
    )

def call_gemini(system_prompt, history, user_message):
    history_text = ""
    for role, text in history[-3:]:
        tag = "User" if role == "user" else "Assistant"
        history_text += f"{tag}: {text}\n"
    prompt = system_prompt + "\nConversation:\n" + history_text + f"User: {user_message}\nAssistant:"
    try:
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 200},
            stream=True
        )
        collected = []
        for chunk in response:
            if chunk.text:
                collected.append(chunk.text)
        return "".join(collected).strip() or "Sorry — I couldn't generate a reply. Our team will connect with you soon 🚀"
    except Exception as e:
        print("Gemini Error:", e)
        traceback.print_exc()
        return "Sorry — service unavailable. Our team will connect with you soon 🚀"

def append_transcript_json(entry):
    all_data = []
    if os.path.exists(TRANSCRIPT_JSON):
        with open(TRANSCRIPT_JSON, "r", encoding="utf-8") as f:
            try: all_data = json.load(f)
            except: all_data = []
    all_data.append(entry)
    with open(TRANSCRIPT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

def build_html_email(user_details, service, transcript):
    header = f"""
    <h2>Sozhaa Tech — Chat Transcript</h2>
    <p><b>Name:</b> {user_details.get('name')}<br/>
    <b>Email:</b> {user_details.get('email')}<br/>
    <b>Phone:</b> {user_details.get('phone')}<br/>
    <b>Service:</b> {service}<br/>
    <b>Captured:</b> {now_iso()}</p>
    <hr/>
    <h3>Conversation</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <thead><tr style="background:#f2f2f2"><th>Time</th><th>Role</th><th>Message</th></tr></thead><tbody>
    """
    rows = "".join([
        f"<tr><td>{it.get('timestamp')}</td><td>{it.get('role')}</td><td>{(it.get('message') or '').replace(chr(10),'<br/>')}</td></tr>"
        for it in transcript
    ])
    return header + rows + "</tbody></table><hr/><p>End of transcript.</p>"

# --------------------------
# Email (SendGrid)
# --------------------------
def send_email_with_attachment(to_email, subject, html_body, attachment_path=None):
    try:
        headers = {
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        }
        # Basic email
        data = {
            "personalizations": [{
                "to": [{"email": to_email}],
                "subject": subject
            }],
            "from": {"email": BOT_EMAIL, "name": "Sozhaa Chatbot"},
            "content": [{"type": "text/html", "value": html_body}]
        }
        # If attachment exists, encode and attach
        if attachment_path and os.path.exists(attachment_path):
            import base64
            with open(attachment_path, "rb") as f:
                file_data = f.read()
            encoded_file = base64.b64encode(file_data).decode()
            data["attachments"] = [{
                "content": encoded_file,
                "filename": os.path.basename(attachment_path),
                "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "disposition": "attachment"
            }]
        r = requests.post(SENDGRID_URL, headers=headers, json=data, timeout=20)
        if r.status_code >= 200 and r.status_code < 300:
            print(f"✅ Email sent via SendGrid to {to_email}")
            return True, None
        else:
            print(f"❌ SendGrid error {r.status_code}: {r.text}")
            return False, r.text
    except Exception as e:
        print(f"❌ Email send failed (SendGrid) to {to_email}: {e}")
        traceback.print_exc()
        return False, str(e)

# --------------------------
# WhatsApp Helpers (unchanged)
# --------------------------
def normalize_phone(phone):
    if not phone: return None
    s = str(phone).strip()
    s = ''.join(ch for ch in s if ch.isdigit() or ch == '+')
    if s.startswith('+'): return s
    if len(s) == 10: return '+91' + s
    if s.startswith('0'): return '+' + s.lstrip('0')
    return '+' + s

def _to_api_phone_format(phone):
    return phone.lstrip('+') if phone else None

def send_whatsapp_text(to, message):
    try:
        to_api = _to_api_phone_format(to)
        url = f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product":"whatsapp","to":to_api,"type":"text","text":{"body":message}}
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"WhatsApp text send response: status={r.status_code} body={r.text[:400]}")
        return r.status_code, r.text
    except Exception as e:
        print("send_whatsapp_text error:", e)
        traceback.print_exc()
        return None, str(e)

def upload_and_send_document(file_path, to):
    try:
        to_api = _to_api_phone_format(to)
        upload_url = f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh)}
            data = {"messaging_product": "whatsapp"}
            r = requests.post(upload_url, headers=headers, data=data, files=files, timeout=60)
            print("WhatsApp media upload response:", r.status_code, r.text[:500])
            r.raise_for_status()
            media_id = r.json().get("id")
            if not media_id:
                return False, f"no media id returned: {r.text}"
        send_url = f"{GRAPH_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        payload = {"messaging_product":"whatsapp","to":to_api,"type":"document","document":{"id":media_id,"filename":os.path.basename(file_path)}}
        r2 = requests.post(send_url, headers=headers, json=payload, timeout=20)
        print("WhatsApp document send response:", r2.status_code, r2.text[:400])
        r2.raise_for_status()
        return True, None
    except Exception as e:
        print("upload_and_send_document error:", e)
        traceback.print_exc()
        return False, str(e)

# --------------------------
# Models
# --------------------------
class ChatPayload(BaseModel):
    user_details: dict
    message: str
    service: str = ""
    history: list = []

# --------------------------
# Prefetch snippets
# --------------------------
SEED_SNIPPETS = fetch_snippets(COMPANY_URLS, chars=1500)
SYSTEM_PROMPT = build_system_prompt(SEED_SNIPPETS)

# --------------------------
# Routes (unchanged except email calls now use SendGrid)
# --------------------------
@app.get("/")
def root():
    return {"status": "ok", "msg": "Sozhaa Chatbot backend running"}

# (keep your /chat route code same, since email calls already redirect to send_email_with_attachment)
