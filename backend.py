# backend.py

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os, json, datetime, time, traceback, base64

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

# --------------------------
# CONFIG
# --------------------------

GEMINI_API_KEY = "AIzaSyB8YVZz-UYA6ILALFOX1ljdnsYgWLiYE_Q"

BOT_EMAIL = "chatbotsozhaatech@gmail.com"
COMPANY_EMAIL = "groupsozhaa@gmail.com"

SENDGRID_API_KEY = "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"

# WhatsApp Cloud API
WHATSAPP_TOKEN = "EAAQYRWtYvBoBPUA7Vq78oYUlgLREviUKQR8P1bVCopFAVOOG1zxGghHf992n9N4dogZCfIIuMrZC0ByJdc63wZCqwA2uacaTz3XZCpzcANNRKS2QnGeOp8h38exHCiYrYGUZALS6AALJI4eOSUuvWNDv1ZClDTZC0dauf75pZAJSQKPMYZBi5cCFzyk9e4DtrnJGeg3NPkZCWwZA7DaL6dRfccyZBO4qkU7mPuRGGAry"
WHATSAPP_PHONE_NUMBER_ID = "787754397756112"
COMPANY_WA_NUMBER = "+917094062522"
GRAPH_API_BASE = "https://graph.facebook.com/v22.0"

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
    allow_origins=["*"],  # in production restrict to your domain
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
        "Keep replies concise.\n\n"
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
# Send Email with SendGrid
# --------------------------

def send_email_with_attachment(to_email, subject, html_body, attachment_path=None):
    try:
        message = Mail(
            from_email=BOT_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html_body
        )
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                data = f.read()
                encoded = base64.b64encode(data).decode()
                attachment = Attachment(
                    FileContent(encoded),
                    FileName(os.path.basename(attachment_path)),
                    FileType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    Disposition("attachment")
                )
                message.attachment = attachment
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"✅ Email sent to {to_email}, status={response.status_code}")
        return True, None
    except Exception as e:
        print(f"❌ Email send failed to {to_email}: {e}")
        traceback.print_exc()
        return False, str(e)

# --------------------------
# WhatsApp Messaging
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
    if not phone: return None
    return phone.lstrip('+')

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
async def chat_endpoint(payload: ChatPayload, background_tasks: BackgroundTasks):
    history_for_model = [(h["role"], h["message"]) for h in payload.history if h.get("role") and h.get("message")]
    user_msg = payload.message or ""

    # --- Case 1: User ends chat ---
    if "[User ended the chat]" in user_msg:
        assistant_text = "✅ Thank you for chatting with Sozhaa Tech 🚀<br>Our team will contact you soon."

        transcript = []
        for h in payload.history:
            transcript.append({"timestamp": now_iso(), "role": h["role"], "message": h["message"], **payload.user_details, "service": payload.service})
        transcript.append({"timestamp": now_iso(), "role": "user", "message": user_msg, **payload.user_details, "service": payload.service})
        transcript.append({"timestamp": now_iso(), "role": "assistant", "message": assistant_text, **payload.user_details, "service": payload.service})

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
            traceback.print_exc()
            combined = pd.DataFrame(transcript)

        html = build_html_email(payload.user_details, payload.service, combined.to_dict("records")[-200:])

        if os.path.exists(TRANSCRIPT_EXCEL):
            background_tasks.add_task(send_whatsapp_text, COMPANY_WA_NUMBER, "📄 New chat transcript received. Please check email.")

        user_phone = normalize_phone(payload.user_details.get("phone"))
        if user_phone:
            thank_msg = "✅ Thanks for contacting Sozhaa Tech. Our team will contact you soon 🚀"
            background_tasks.add_task(send_whatsapp_text, user_phone, thank_msg)

        send_email_with_attachment(COMPANY_EMAIL, f"Chat Ended — {payload.user_details.get('name')}", html, TRANSCRIPT_EXCEL)

        if payload.user_details.get("email"):
            thank_you_html = html + "<br><br><p>🙏 Thank you for chatting with Sozhaa Tech. Our team will connect with you soon.</p>"
            send_email_with_attachment(payload.user_details["email"], "Sozhaa Tech — Chat Summary", thank_you_html, TRANSCRIPT_EXCEL)

        return {"reply": assistant_text}

    # --- Case 2: Support Request ---
    if "support" in user_msg.lower() or "contact" in user_msg.lower():
        assistant_text = "Please contact us at groupsozhaatech@gmail.com. Our team will reach out shortly 🚀."

        def support_alert():
            try:
                alert_html = f"""
                <h2>⚠ Support Request Alert</h2>
                <p>User requested support at {now_iso()}</p>
                <p><b>Name:</b> {payload.user_details.get('name')}<br/>
                <b>Email:</b> {payload.user_details.get('email')}<br/>
                <b>Phone:</b> {payload.user_details.get('phone')}</p>
                <p><b>Message:</b> {user_msg}</p>
                """
                send_email_with_attachment(COMPANY_EMAIL, "⚠ Sozhaa Tech — Support Request", alert_html)
                if payload.user_details.get("email"):
                    send_email_with_attachment(payload.user_details["email"], "Sozhaa Tech — Support Request Received", "<p>We received your request. Our team will contact you soon 🚀</p>")
            except Exception as e:
                print("support_alert error:", e)
                traceback.print_exc()

        background_tasks.add_task(support_alert)
        return {"reply": assistant_text}

    # --- Case 3: Normal AI Chat ---
    assistant_text = call_gemini(SYSTEM_PROMPT, history_for_model, user_msg)

    transcript = [
        {"timestamp": now_iso(), "role": "user", "message": user_msg, **payload.user_details, "service": payload.service},
        {"timestamp": now_iso(), "role": "assistant", "message": assistant_text, **payload.user_details, "service": payload.service}
    ]

    def save_and_email():
        try:
            append_transcript_json({"user": payload.user_details, "service": payload.service, "transcript": transcript, "captured_at": now_iso()})
            if os.path.exists(TRANSCRIPT_EXCEL):
                existing_df = pd.read_excel(TRANSCRIPT_EXCEL)
            else:
                existing_df = pd.DataFrame(columns=["timestamp","role","message","service","name","email","phone"])
            new_df = pd.DataFrame(transcript)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined.to_excel(TRANSCRIPT_EXCEL, index=False)
        except Exception as e:
            print("Excel save failed in save_and_email:", e)
            traceback.print_exc()
            combined = pd.DataFrame(transcript)

        html = build_html_email(payload.user_details, payload.service, combined.to_dict("records")[-100:])
        send_email_with_attachment(COMPANY_EMAIL, f"Chat update — {payload.user_details.get('name')}", html, TRANSCRIPT_EXCEL)

        if payload.user_details.get("email"):
            send_email_with_attachment(payload.user_details["email"], "Sozhaa Tech — Your Chat Transcript", html, TRANSCRIPT_EXCEL)

    background_tasks.add_task(save_and_email)
    return {"reply": assistant_text}











