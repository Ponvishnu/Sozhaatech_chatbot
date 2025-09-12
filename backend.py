# backend.py
"""
Sozhaa Tech Chatbot backend (FastAPI)
Features:
- Gemini 1.5 Flash (sozhaa.tech context)
- RAG fetch from site
- Save transcripts in JSON + Excel
- Email transcripts to company & user
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os, json, datetime, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# --------------------------
# CONFIG (hardcoded keys, for Render deploy)
# --------------------------
GEMINI_API_KEY = "AIzaSyB8YVZz-UYA6ILALFOX1ljdnsYgWLiYE_Q"
BOT_EMAIL = "chatbotsozhaatech@gmail.com"
BOT_PASSWORD = "ykstkaxoeykorkze"
COMPANY_EMAIL = "groupsozhaa@gmail.com"

COMPANY_URLS = [
    "https://sozhaa.tech/",
    "https://sozhaa.tech/about",
    "https://sozhaa.tech/services",
    "https://sozhaa.tech/contact",
]

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

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
    allow_origins=["*"],   # in production, restrict to your domain
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
    for role, text in history[-3:]:  # keep shorter history = faster
        tag = "User" if role == "user" else "Assistant"
        history_text += f"{tag}: {text}\n"
    prompt = system_prompt + "\nConversation:\n" + history_text + f"User: {user_message}\nAssistant:"

    try:
        response = model.generate_content(prompt, stream=True)  # ✅ stream mode
        chunks = []
        for chunk in response:
            if chunk.candidates and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0].text
                if part:
                    chunks.append(part)
        return "".join(chunks).strip()
    except Exception as e:
        print("Gemini Error:", e)
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

def send_email_with_attachment(to_email, subject, html_body, attachment_path=None):
    try:
        msg = MIMEMultipart()
        msg["From"] = BOT_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(BOT_EMAIL, BOT_PASSWORD)
            server.send_message(msg)
        return True, None
    except Exception as e:
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
# Routes
# --------------------------
@app.get("/")
def root():
    return {"status": "ok", "msg": "Sozhaa Chatbot backend running"}

@app.post("/chat")
async def chat_endpoint(payload: ChatPayload):
    history_for_model = [(h["role"], h["message"]) for h in payload.history if h.get("role") and h.get("message")]
    user_msg = payload.message or ""

    if "[User ended the chat]" in user_msg:
        assistant_text = "✅ Thank you for chatting with Sozhaa Tech 🚀<br>Our team will contact you soon."
    else:
        assistant_text = call_gemini(SYSTEM_PROMPT, history_for_model, user_msg)

    transcript = [
        {"timestamp": now_iso(), "role": "user", "message": user_msg, **payload.user_details, "service": payload.service},
        {"timestamp": now_iso(), "role": "assistant", "message": assistant_text, **payload.user_details, "service": payload.service}
    ]

    append_transcript_json({"user": payload.user_details, "service": payload.service, "transcript": transcript, "captured_at": now_iso()})

    try:
        if os.path.exists(TRANSCRIPT_EXCEL):
            existing_df = pd.read_excel(TRANSCRIPT_EXCEL)
        else:
            existing_df = pd.DataFrame(columns=["timestamp","role","message","service","name","email","phone"])
        new_df = pd.DataFrame(transcript)
        combined = pd.concat([existing_df, new_df], ignore_index=True)
        combined.to_excel(TRANSCRIPT_EXCEL, index=False)
    except Exception as e:
        print("Excel save failed:", e)
        combined = pd.DataFrame(transcript)

    html = build_html_email(payload.user_details, payload.service, combined.to_dict("records")[-100:])

    send_email_with_attachment(COMPANY_EMAIL, f"Chat update — {payload.user_details.get('name')}", html, TRANSCRIPT_EXCEL)
    if payload.user_details.get("email"):
        send_email_with_attachment(payload.user_details["email"], "Sozhaa Tech — Your Chat Transcript", html, TRANSCRIPT_EXCEL)

    return {"reply": assistant_text}



