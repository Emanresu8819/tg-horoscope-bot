import os
import io
import textwrap
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import feedparser

TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")  # @username канала или -100ID для приватного
ZODIAC_NAME = os.getenv("ZODIAC_NAME", "Овен")
SOURCE_TYPE = os.getenv("SOURCE_TYPE", "HTML")  # "HTML" или "RSS"
SOURCE_URL = os.getenv("SOURCE_URL")  # страница или RSS
CSS_SELECTOR = os.getenv("CSS_SELECTOR", "")   # CSS-селектор основного текста для HTML
ATTRIBUTION = os.getenv("ATTRIBUTION", "")     # ссылка/подпись источника
POST_TITLE = os.getenv("POST_TITLE", "Еженедельный гороскоп: {zodiac}").format(zodiac=ZODIAC_NAME)

def fetch_text_html(url, selector):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    node = soup.select_one(selector)
    if not node:
        raise RuntimeError("Не найден блок по CSS_SELECTOR")
    text = node.get_text("\n", strip=True)
    return text

def fetch_text_rss(url, limit_chars=2000):
    feed = feedparser.parse(url)
    if not feed.entries:
        raise RuntimeError("RSS пуст")
    entry = feed.entries[0]
    text = entry.get("summary", "") or entry.get("description", "") or entry.get("title", "")
    text = BeautifulSoup(text, "lxml").get_text("\n", strip=True)
    return text[:limit_chars]

def pick_keyphrase(text, max_len=120):
    for sep in [". ", "\n", "!", "?"]:
        idx = text.find(sep)
        if 40 < idx < max_len:
            return text[:idx+1]
    return text[:max_len]

def generate_image(zodiac, keyphrase):
    W, H = 1024, 1024
    img = Image.new("RGB", (W, H), color=(15, 14, 35))
    draw = ImageDraw.Draw(img)

    # простой градиент
    for y in range(H):
        c = int(35 + 60 * y / H)
        draw.line([(0, y), (W, y)], fill=(c, 20, 80))

    # шрифты
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 42)
    except:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    title = f"{zodiac} — Гороскоп недели"
    tw, th = draw.textsize(title, font=title_font)
    draw.text(((W - tw)//2, 90), title, fill=(240, 230, 255), font=title_font)

    wrapped = textwrap.fill(keyphrase, width=26)
    bw, bh = draw.multiline_textsize(wrapped, font=body_font, spacing=6)
    draw.multiline_text(((W - bw)//2, (H - bh)//2), wrapped, fill=(245, 245, 250), font=body_font, spacing=6, align="center")

    footer = "by @your_bot"
    fw, fh = draw.textsize(footer, font=body_font)
    draw.text((W - fw - 30, H - fh - 30), footer, fill=(220, 210, 235), font=body_font)

    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=90)
    bio.seek(0)
    return bio

def send_photo_with_caption(token, chat_id, photo_bytes, caption):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML", "disable_notification": True}
    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()

def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True, "disable_notification": True}
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    if not TG_TOKEN or not TG_CHAT_ID or not SOURCE_URL:
        raise RuntimeError("Не заданы TG_TOKEN, TG_CHAT_ID, SOURCE_URL")

    if SOURCE_TYPE.upper() == "RSS":
        text = fetch_text_rss(SOURCE_URL)
    else:
        if not CSS_SELECTOR:
            raise RuntimeError("Для HTML источника нужен CSS_SELECTOR")
        text = fetch_text_html(SOURCE_URL, CSS_SELECTOR)

    header = POST_TITLE
    body = text
    footer = f"\n\nИсточник: {ATTRIBUTION}" if ATTRIBUTION else ""
    full_text = f"{header}\n\n{body}{footer}"

    keyphrase = pick_keyphrase(body)
    img_bytes = generate_image(ZODIAC_NAME, keyphrase)

    if len(full_text) <= 1000:
        send_photo_with_caption(TG_TOKEN, TG_CHAT_ID, img_bytes, full_text)
    else:
        short_caption = f"{header}\n\n{keyphrase}"
        send_photo_with_caption(TG_TOKEN, TG_CHAT_ID, img_bytes, short_caption)
        send_message(TG_TOKEN, TG_CHAT_ID, full_text)

if __name__ == "__main__":
    main()
