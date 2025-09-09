# backend.py
"""
Sozhaa Tech Chatbot backend (FastAPI)
Features:
- Gemini 1.5 Flash (context-limited to sozhaa.tech + sozhaa.ai)
- Lightweight RAG (fetch seed pages)
- Persists transcripts, saves Excel and emails to company & user immediately
- /chat endpoint used by frontend widget
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
# CONFIG - replace with env vars or fill here (recommended: env vars)
# --------------------------
GEMINI_API_KEY = "AIzaSyB8YVZz-UYA6ILALFOX1ljdnsYgWLiYE_Q"
BOT_EMAIL = "chatbotsozhaatech@gmail.com"            # Gmail to send emails from (create app password)
BOT_PASSWORD = "ykstkaxoeykorkze"
COMPANY_EMAIL = "groupsozhaa@gmail.com"    # Where transcripts go

COMPANY_URLS = [
    "https://sozhaa.tech/",
    "https://sozhaa.tech/about",
    "https://sozhaa.tech/services",
    "https://sozhaa.tech/contact",
]

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

STORAGE_DIR = "chat_data"
TRANSCRIPT_EXCEL = os.path.join(STORAGE_DIR, "sozhaa_full_chat_history.xlsx")
TRANSCRIPT_JSON = os.path.join(STORAGE_DIR, "sozhaa_transcripts.json")

# --------------------------
# Setup
# --------------------------
os.makedirs(STORAGE_DIR, exist_ok=True)

# configure Gemini SDK
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# FastAPI app
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # in production, set to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------
# Utilities
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
            snippets.append({"url": u, "title": u, "text": f"(failed to fetch: {e})"})
    return snippets

def build_system_prompt(snippets):
    context_text = "\n\n".join([f"{p['title']} ({p['url']}):\n{p['text']}" for p in snippets])
    system = (
        "You are Sozhaa Tech AI Assistant. Use only the company's information provided below "
        "(sozhaa.tech). Answer only about the company, services, pages and contact information. "
        "If the user asks something outside company scope, reply politely that you only provide Sozhaa Tech info. "
        "Keep replies concise and professional. End every reply with: 'Our team will connect with you soon 🚀'.\n\n"
        "Company context (excerpts):\n" + context_text + "\n\n"
    )
    return system

def call_gemini(system_prompt, history, user_message):
    history_text = ""
    for role, text in history[-8:]:
        tag = "User" if role == "user" else "Assistant"
        history_text += f"{tag}: {text}\n"
    prompt = system_prompt + "\nConversation:\n" + history_text + f"User: {user_message}\nAssistant:"
    try:
        resp = model.generate_content(prompt)
        if getattr(resp, "text", None):
            return resp.text.strip()
        if isinstance(resp, dict) and "candidates" in resp:
            return resp["candidates"][0].get("content", "").strip()
        return "Sorry — I couldn't generate a reply. Our team will connect with you soon 🚀"
    except Exception as e:
        print("Error:", e)
        return "Sorry — there was an error generating the reply. Our team will connect with you soon 🚀"

def append_transcript_json(entry):
    all_data = []
    if os.path.exists(TRANSCRIPT_JSON):
        with open(TRANSCRIPT_JSON, "r", encoding="utf-8") as f:
            try:
                all_data = json.load(f)
            except:
                all_data = []
    all_data.append(entry)
    with open(TRANSCRIPT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

def build_html_email(user_details, service, transcript):
    # Build a neat HTML email for company / user
    header = f"""
    <h2>Sozhaa Tech — Chat Transcript</h2>
    <p><b>Name:</b> {user_details.get('name')}<br/>
    <b>Email:</b> {user_details.get('email')}<br/>
    <b>Phone:</b> {user_details.get('phone')}<br/>
    <b>Service:</b> {service}<br/>
    <b>Captured:</b> {now_iso()}</p>
    <hr/>
    <h3>Conversation</h3>
    <table border="0" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <thead><tr style="background:#f2f2f2"><th>Time</th><th>Role</th><th>Message</th></tr></thead><tbody>
    """
    rows = ""
    for it in transcript:
        safe_msg = (it.get("message") or "").replace("\n","<br/>")
        rows += f"<tr><td>{it.get('timestamp')}</td><td>{it.get('role')}</td><td>{safe_msg}</td></tr>"
    footer = "</tbody></table><hr/><p>End of transcript.</p>"
    return header + rows + footer

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
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls(context=context)
        server.login(BOT_EMAIL, BOT_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True, None
    except Exception as e:
        return False, str(e)

# --------------------------
# API models
# --------------------------
class ChatPayload(BaseModel):
    user_details: dict   # {"name","email","phone"}
    message: str
    service: str = ""
    history: list = []   # list of {"role","message","timestamp"}

# --------------------------
# Pre-fetch snippets once (refresh on restart)
# --------------------------
SEED_SNIPPETS = fetch_snippets(COMPANY_URLS, chars=1500)
SYSTEM_PROMPT = build_system_prompt(SEED_SNIPPETS)

# --------------------------
# Endpoints
# --------------------------
@app.get("/")
def root():
    return {"status": "ok", "msg": "Sozhaa Chatbot backend running"}

@app.post("/chat")
async def chat_endpoint(payload: ChatPayload):
    # Build history for model
    history_for_model = []
    for h in payload.history:
        if h.get("role") and h.get("message"):
            history_for_model.append((h["role"], h["message"]))

    user_msg = payload.message or ""

    # Special-case: user ended the chat (frontend sends this exact token)
    if "[User ended the chat]" in user_msg:
        assistant_text = (
            "✅ Thank you for chatting with Sozhaa Tech 🚀<br>"
            "Our team will contact you soon. Have a great day!"
        )
    else:
        assistant_text = call_gemini(SYSTEM_PROMPT, history_for_model, user_msg)

    # Build transcript entries (user + assistant)
    t_user = {
        "timestamp": now_iso(), "role": "user", "message": user_msg,
        "service": payload.service or "",
        "name": payload.user_details.get("name",""),
        "email": payload.user_details.get("email",""),
        "phone": payload.user_details.get("phone","")
    }
    t_bot = {
        "timestamp": now_iso(), "role": "assistant", "message": assistant_text,
        "service": payload.service or "",
        "name": payload.user_details.get("name",""),
        "email": payload.user_details.get("email",""),
        "phone": payload.user_details.get("phone","")
    }
    transcript = [t_user, t_bot]

    # Persist JSON transcript
    append_transcript_json({
        "user": payload.user_details,
        "service": payload.service,
        "transcript": transcript,
        "captured_at": now_iso()
    })

    # Persist Excel: append the two rows
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

    # Build HTML body and send emails immediately
    html = build_html_email(payload.user_details, payload.service, combined.to_dict("records")[-100:])

    # Send to company
    ok, err = send_email_with_attachment(
        COMPANY_EMAIL,
        f"Live chat update — {payload.user_details.get('name')}",
        html,
        attachment_path=TRANSCRIPT_EXCEL
    )
    if not ok:
        print("Email send error (company):", err)

    # Also send transcript to user email (if provided)
    user_email = payload.user_details.get("email")
    if user_email:
        ok_u, err_u = send_email_with_attachment(
            user_email,
            "Sozhaa Tech — Your Chat Transcript",
            html,
            attachment_path=TRANSCRIPT_EXCEL
        )
        if not ok_u:
            print("Email send error (user):", err_u)

    # Return assistant reply
    return {"reply": assistant_text, "timestamp": now_iso()}
