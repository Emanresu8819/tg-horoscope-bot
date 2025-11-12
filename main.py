import os
import io
import textwrap
import hashlib
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import feedparser

# Опциональный импорт readability (на случай сбоев в зависимостях)
try:
    from readability import Document
    HAVE_READABILITY = True
except Exception:
    Document = None
    HAVE_READABILITY = False

# ---------- Конфигурация из окружения ----------
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")            # например, @real_pisces или -100XXXXXXXXXX
ZODIAC_NAME = os.getenv("ZODIAC_NAME", "Рыбы")
SOURCE_TYPE = os.getenv("SOURCE_TYPE", "RSS")   # "HTML" или "RSS"
SOURCE_URL = os.getenv("SOURCE_URL")
CSS_SELECTOR = os.getenv("CSS_SELECTOR", "")
ATTRIBUTION = os.getenv("ATTRIBUTION", "")
POST_TITLE = os.getenv("POST_TITLE", "Самый точный гороскоп: {zodiac}").format(zodiac=ZODIAC_NAME)
STATE_FILE = os.getenv("STATE_FILE", "state_last_hash.txt")  # для дедупликации (опционально)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ---------- Утилиты ----------
def content_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def http_get(url, timeout=30):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    # Если кодировка сомнительная/не задана — определить автоматически
    try:
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1", "ascii"):
            r.encoding = r.apparent_encoding or "utf-8"
    except Exception:
        # В крайнем случае пусть будет utf-8 — позже поможем Soup-ом
        r.encoding = "utf-8"
    return r

def soup_from_response(r: requests.Response) -> BeautifulSoup:
    """
    Создаём Soup из байтов, чтобы BS сам определил кодировку (по мета-тегам/headers).
    Это снижает шанс получить “кракозябры”.
    """
    return BeautifulSoup(r.content, "lxml")

def html_to_text(html_str: str) -> str:
    soup = BeautifulSoup(html_str, "lxml")
    # Удаляем шум
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.extract()
    # Типичные блоки комментариев
    for node in soup.select('[class*="comment"], [id*="comment"]'):
        node.extract()
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)

def fetch_text_html(url, selector=None):
    r = http_get(url)
    soup = soup_from_response(r)  # автоопределение кодировки
    # 1) Если дали селектор — пробуем взять узел сразу из Soup
    if selector:
        node = soup.select_one(selector)
        if node:
            return node.get_text("\n", strip=True)

    # 2) Если селектора нет или он не сработал — пробуем readability
    # Для readability нужен строковый HTML с корректной кодировкой.
    html_str = None
    try:
        # Берём кодировку, определённую Soup-ом или requests
        enc = getattr(soup, "original_encoding", None) or r.encoding or "utf-8"
        html_str = r.content.decode(enc, errors="replace")
    except Exception:
        # Надёжный запасной вариант
        html_str = r.text

    if HAVE_READABILITY:
        try:
            doc = Document(html_str)
            summary_html = doc.summary()
            return html_to_text(summary_html)
        except Exception:
            pass

    # 3) Фолбэк: просто вычистим текст из всего документа
    return html_to_text(html_str)

def fetch_text_rss(url, fallback_selector=None, limit_chars=3000):
    feed = feedparser.parse(url)
    # Если это не RSS или пусто — считаем как обычную HTML-страницу
    if not feed.entries:
        return fetch_text_html(url, fallback_selector)[:limit_chars]

    entry = feed.entries[0]
    text = ""
    if entry.get("content"):
        try:
            text = entry["content"][0].get("value", "")
        except Exception:
            text = ""
    if not text:
        text = entry.get("summary", "") or entry.get("description", "") or ""

    # Очистим HTML-теги из кусочка RSS
    text = html_to_text(text)

    # Если коротко — попробуем перейти на полную версию по ссылке
    link = entry.get("link")
    if link and len(text) < 300:
        try:
            text_full = fetch_text_html(link, fallback_selector)
            if len(text_full) > len(text):
                text = text_full
        except Exception:
            pass

    return text[:limit_chars]

def pick_keyphrase(text, max_len=140):
    for sep in [". ", "… ", "\n", "! ", "? "]:
        idx = text.find(sep)
        if 40 < idx < max_len:
            return text[:idx+1]
    return text[:max_len]

# ---- Измерение текста (Pillow 10+ совместимость) ----
def measure_text(draw, text, font):
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    except AttributeError:
        return draw.textsize(text, font=font)

def measure_multiline_text(draw, text, font, spacing=6, align="left"):
    try:
        left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align=align)
        return right - left, bottom - top
    except AttributeError:
        return draw.multiline_textsize(text, font=font, spacing=spacing)

def generate_image(zodiac, keyphrase):
    W, H = 1024, 1024
    img = Image.new("RGB", (W, H), color=(15, 14, 35))
    draw = ImageDraw.Draw(img)
    # Градиент
    for y in range(H):
        c = int(35 + 60 * y / H)
        draw.line([(0, y), (W, y)], fill=(c, 20, 80))
    # Шрифты
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 72)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 42)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 28)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    # Заголовок
    title = f"{zodiac} — Гороскоп дня"
    tw, th = measure_text(draw, title, title_font)
    draw.text(((W - tw)//2, 80), title, fill=(240, 230, 255), font=title_font)
    # Ключевая фраза
    wrapped = textwrap.fill(keyphrase, width=26)
    bw, bh = measure_multiline_text(draw, wrapped, body_font, spacing=6, align="center")
    draw.multiline_text(((W - bw)//2, (H - bh)//2), wrapped,
                        fill=(245, 245, 250), font=body_font, spacing=6, align="center")
    # Подпись
    footer = "by @real_pisces"
    fw, fh = measure_text(draw, footer, small_font)
    draw.text((W - fw - 30, H - fh - 30), footer, fill=(220, 210, 235), font=small_font)
    bio = io.BytesIO()
    img.save(bio, format="JPEG", quality=90)
    bio.seek(0)
    return bio

def tg_send_photo(token, chat_id, photo_bytes, caption):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption[:1024], "disable_notification": True}
    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()

def tg_send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True, "disable_notification": True}
    r = requests.post(url, data=data, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    if not TG_TOKEN:
        raise RuntimeError("TG_TOKEN не задан (добавьте в GitHub Secrets).")
    if not TG_CHAT_ID:
        raise RuntimeError("TG_CHAT_ID не задан (например, @real_pisces).")
    if not SOURCE_URL:
        raise RuntimeError("SOURCE_URL не задан.")

    # Получаем и чистим текст
    if SOURCE_TYPE.upper() == "RSS":
        text = fetch_text_rss(SOURCE_URL, fallback_selector=CSS_SELECTOR or None)
    else:
        text = fetch_text_html(SOURCE_URL, selector=CSS_SELECTOR or None)

    if not text or len(text) < 30:
        raise RuntimeError("Не удалось извлечь содержимое гороскопа или оно слишком короткое.")

    header = POST_TITLE
    body = text
    footer = f"\n\nИсточник: {ATTRIBUTION}" if ATTRIBUTION else ""
    full_text = f"{header}\n\n{body}{footer}"

    # Дедупликация (опционально)
    state_path = Path(STATE_FILE)
    current_hash = content_hash(body.strip())
    if state_path.exists():
        last_hash = state_path.read_text().strip()
        if last_hash == current_hash:
            print("Контент не изменился — публикация пропущена.")
            return

    keyphrase = pick_keyphrase(body)
    img_bytes = generate_image(ZODIAC_NAME, keyphrase)

    if len(full_text) <= 1000:
        tg_send_photo(TG_TOKEN, TG_CHAT_ID, img_bytes, full_text)
    else:
        short_caption = f"{header}\n\n{keyphrase}"
        tg_send_photo(TG_TOKEN, TG_CHAT_ID, img_bytes, short_caption)
        tg_send_message(TG_TOKEN, TG_CHAT_ID, full_text)

    # сохраняем хеш
    try:
        state_path.write_text(current_hash)
    except Exception as e:
        print("Не удалось сохранить состояние:", e)

if __name__ == "__main__":
    main()
