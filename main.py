import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
import os
import io
import pdfplumber
import re
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

SEBI_URL = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=yes"
HEADERS = {"User-Agent": "Mozilla/5.0"}
LAST_UPDATE_FILE = "last_update.txt"

# Hugging Face
HF_API_KEY = os.getenv("HF_API_KEY")
HF_MODEL = os.getenv("HF_MODEL", "facebook/bart-large-cnn")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ---------- HELPERS ----------
def escape_markdown(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(rf'([{re.escape(escape_chars)}])', r'\\\1', text)

# ---------- FETCH LATEST SEBI ENTRY ----------
def get_latest_update():
    try:
        resp = requests.get(SEBI_URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", id="sample_1") or soup.find("table", class_="table")
        if not table:
            print("⚠️ Could not find updates table.")
            return None

        first_row = table.find("tbody").find("tr")
        columns = first_row.find_all("td")
        if len(columns) < 3:
            print("⚠️ Unexpected table format.")
            return None

        date = columns[0].get_text(strip=True)
        category = columns[1].get_text(strip=True)
        title_tag = columns[2].find("a")
        title = title_tag.get_text(strip=True)
        detail_link = urljoin(SEBI_URL, title_tag["href"]) if title_tag else None

        return {"date": date, "category": category, "title": title, "detail_link": detail_link}
    except Exception as e:
        print(f"⚠️ Error fetching latest update: {e}")
        return None

# ---------- EXTRACT PDF LINK ----------
def extract_pdf_link_from_page(detail_url):
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        embed_tag = soup.find("embed", type="application/pdf")
        iframe_tag = soup.find("iframe")

        raw_url = embed_tag["src"] if embed_tag and ".pdf" in embed_tag.get("src", "") else (
            iframe_tag["src"] if iframe_tag else None)

        if not raw_url:
            print("⚠️ No PDF found.")
            return None

        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query)
        if "file" in query:
            return query["file"][0]

        return urljoin(detail_url, raw_url)
    except Exception as e:
        print(f"⚠️ Error extracting PDF link: {e}")
        return None

# ---------- DOWNLOAD & READ PDF ----------
def download_pdf_bytes(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if b"%PDF" not in resp.content[:1024]:
            print("⚠️ Not a valid PDF.")
            return None
        return resp.content
    except Exception as e:
        print(f"⚠️ Error downloading PDF: {e}")
        return None

def extract_text_from_pdf_bytes(pdf_bytes):
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        print(f"⚠️ PDF parsing error: {e}")
        return ""

# ---------- OPTIONAL SUMMARIZER ----------
def summarize_text(text):
    if not HF_API_KEY:
        return "⚠️ No Hugging Face API key set."
    try:
        url = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
        headers = {"Authorization": f"Bearer {HF_API_KEY}"}
        payload = {"inputs": text[:2000]}  # Limit input
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            return response.json()[0].get("summary_text", "⚠️ No summary returned.")
        else:
            return f"⚠️ Summary failed: {response.text}"
    except Exception as e:
        return f"⚠️ Summary error: {e}"

# ---------- TELEGRAM ----------
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram token or chat ID missing.")
        return False

    try:
        escaped_msg = escape_markdown(message)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": escaped_msg,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False
        }
        resp = requests.post(url, json=payload, timeout=10)

        if resp.status_code == 200:
            print("✅ Message sent to Telegram.")
            return True
        else:
            print(f"❌ Telegram API Error: {resp.status_code}")
            print(f"🔁 Response: {resp.text}")
            return False
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")
        return False

# ---------- TRACKING ----------
def load_last_update():
    if not os.path.exists(LAST_UPDATE_FILE):
        return None
    with open(LAST_UPDATE_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

def save_last_update(title):
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        f.write(title)

# ---------- MAIN ----------
def main():
    print("🚀 Checking SEBI for updates...")

    latest = get_latest_update()
    if not latest:
        return print("⚠️ No update fetched.")

    print(f"✅ New update found: {latest['title']}")
    if load_last_update() == latest["title"]:
        return print("ℹ️ Already processed.")

    if not latest["detail_link"]:
        print("⚠️ No detail link.")
        return

    pdf_url = extract_pdf_link_from_page(latest["detail_link"])
    if not pdf_url:
        print("⚠️ No PDF URL extracted.")
        return

    pdf_bytes = download_pdf_bytes(pdf_url)
    if not pdf_bytes:
        return

    text = extract_text_from_pdf_bytes(pdf_bytes)
    if not text:
        return

    summary = summarize_text(text)

    # Compose message
    message = f"""📢 *SEBI Update:* {latest['title']}

📝 *Summary:* 
{summary}

🔗 [Read PDF]({pdf_url})
📅 Date: {latest['date']}
📂 Category: {latest['category']}"""

    # Send
    sent = send_telegram(message)
    if sent:
        save_last_update(latest["title"])

if __name__ == "__main__":
    main()
