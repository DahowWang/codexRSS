#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Tuple

import requests
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
GEMINI_TEXT_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_OPENAI_IMAGE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/images/generations"


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value or ""


def refresh_gmail_access_token() -> str:
    client_id = env("GMAIL_CLIENT_ID", required=True)
    client_secret = env("GMAIL_CLIENT_SECRET", required=True)
    refresh_token = env("GMAIL_REFRESH_TOKEN", required=True)

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    resp = requests.post(GMAIL_TOKEN_URL, data=data, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Failed to refresh Gmail token: {resp.status_code} {resp.text}")
    return resp.json()["access_token"]


def gmail_get(path: str, access_token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{GMAIL_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Gmail API error {resp.status_code}: {resp.text}")
    return resp.json()


def list_messages(access_token: str, user: str, query: str) -> List[str]:
    message_ids: List[str] = []
    page_token = None
    while True:
        params = {"q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        data = gmail_get(f"/users/{user}/messages", access_token, params=params)
        for msg in data.get("messages", []) or []:
            message_ids.append(msg["id"])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def get_message(access_token: str, user: str, msg_id: str) -> Dict[str, Any]:
    return gmail_get(f"/users/{user}/messages/{msg_id}", access_token, params={"format": "full"})


def get_header(headers: List[Dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def clean_html_to_text(raw_html: str) -> str:
    # Remove scripts/styles, then tags.
    cleaned = re.sub(r"<script.*?>.*?</script>", " ", raw_html, flags=re.S | re.I)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def decode_body(data: str) -> str:
    if not data:
        return ""
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="ignore")


def extract_text_from_payload(payload: Dict[str, Any]) -> str:
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime_type == "text/plain":
        return decode_body(body.get("data", ""))
    if mime_type == "text/html":
        return clean_html_to_text(decode_body(body.get("data", "")))

    parts = payload.get("parts", []) or []
    texts: List[str] = []
    for part in parts:
        texts.append(extract_text_from_payload(part))
    combined = "\n".join(t for t in texts if t.strip())
    return combined.strip()


def gemini_translate_and_summarize(text: str) -> Dict[str, str]:
    api_key = env("GEMINI_API_KEY", required=True)
    model = env("GEMINI_TEXT_MODEL", default="gemini-2.5-flash")

    prompt = (
        "Translate the input into Traditional Chinese (繁體中文) and write a concise summary in Traditional Chinese. "
        "If the input is already Traditional Chinese, keep it as-is.\n\n"
        "Return ONLY strict JSON with keys: language, translated_text, summary_zh_tw. "
        "Do NOT include any markdown, code fences, or extra text.\n"
        "language should be a short label like 'zh-TW', 'zh-CN', 'en', etc. "
        "If the input is already Traditional Chinese, translated_text must equal the original content. "
        "Keep the summary within 6 bullet points or 6 sentences.\n\n"
        f"INPUT:\n{text}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800,
            "responseMimeType": "application/json",
        },
    }

    resp = requests.post(
        GEMINI_TEXT_API_URL.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=60,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Gemini API error {resp.status_code}: {resp.text}")

    data = resp.json()
    output_text = ""
    for candidate in data.get("candidates", []) or []:
        if candidate.get("finishReason") == "SAFETY":
            return {
                "language": "unknown",
                "translated_text": text,
                "summary_zh_tw": "（內容因安全性限制無法摘要）",
            }
        content = candidate.get("content", {})
        for part in content.get("parts", []) or []:
            output_text += part.get("text", "")

    output_text = output_text.strip()
    if not output_text:
        return {
            "language": "unknown",
            "translated_text": text,
            "summary_zh_tw": "（內容摘要失敗）",
        }

    # Strip common code fences if present.
    output_text = re.sub(r"^```(?:json)?\s*", "", output_text)
    output_text = re.sub(r"\s*```$", "", output_text)

    def parse_loose_json(text_value: str) -> Dict[str, str]:
        try:
            return json.loads(text_value)
        except json.JSONDecodeError:
            start = text_value.find("{")
            end = text_value.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text_value[start : end + 1])
            raise

    try:
        return parse_loose_json(output_text)
    except json.JSONDecodeError:
        # Last attempt: find any JSON-looking object in the text.
        match = re.search(r"\{.*\}", output_text, flags=re.S)
        if match:
            return parse_loose_json(match.group(0))
        print("Gemini raw text (first 1000 chars):")
        print(output_text[:1000])
        return {
            "language": "unknown",
            "translated_text": text,
            "summary_zh_tw": "（內容摘要解析失敗）",
        }


def normalize_text(text: str, max_chars: int = 12000) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[內容已截斷]"
    return text


def extract_category(subject: str) -> str:
    match = re.match(r"^[\[【(]([^\]）】\)]+)[\]）】\)]\s*", subject)
    if match:
        return match.group(1).strip()
    return "未分類"


def extract_source(from_header: str) -> Tuple[str, str]:
    name, email_addr = parseaddr(from_header)
    name = name.strip().strip('"') or email_addr
    domain = ""
    if "@" in email_addr:
        domain = email_addr.split("@", 1)[1].lower()
    return name or "未知來源", domain or "unknown"


def build_thumb_placeholder(subject: str) -> str:
    colors = [
        ("#e0f2fe", "#bae6fd", "#93c5fd"),
        ("#ede9fe", "#ddd6fe", "#c4b5fd"),
        ("#ffe4e6", "#fecdd3", "#fda4af"),
        ("#dcfce7", "#bbf7d0", "#86efac"),
        ("#fef3c7", "#fde68a", "#fcd34d"),
    ]
    idx = sum(ord(c) for c in subject) % len(colors)
    c1, c2, c3 = colors[idx]
    return f"<div class=\\\"thumb thumb--placeholder\\\" style=\\\"background: linear-gradient(140deg, {c1} 0%, {c2} 45%, {c3} 100%);\\\"></div>"


def build_thumb_html(image_rel_path: str, subject: str) -> str:
    if image_rel_path:
        return f"<img class=\\\"thumb\\\" src=\\\"{html.escape(image_rel_path)}\\\" alt=\\\"\\\">"
    show_placeholder = env("SHOW_THUMB_PLACEHOLDER", default="0") == "1"
    return build_thumb_placeholder(subject) if show_placeholder else ""


def build_image_prompt(subject: str, summary: str) -> str:
    clean_subject = re.sub(r"^[\[【(].*?[\]】)]\s*", "", subject).strip()
    clean_summary = re.sub(r"\s+", " ", summary).strip()
    if len(clean_summary) > 400:
        clean_summary = clean_summary[:400]
    return (
        "Minimal, soft, calm Apple News-style illustration. "
        "Abstract, gentle gradients, no text, no logos, no watermark, no people. "
        f"Inspired by: {clean_subject}. "
        f"Summary: {clean_summary}"
    )


def generate_image(prompt: str, out_path: str) -> None:
    api_key = env("GEMINI_API_KEY", required=True)
    model = env("GEMINI_IMAGE_MODEL", default="imagen-3.0-generate-002")
    size = env("GEMINI_IMAGE_SIZE", default="1024x1024").strip()

    payload = {
        "model": model,
        "prompt": prompt,
        "response_format": "b64_json",
        "n": 1,
    }
    if size:
        payload["size"] = size

    resp = requests.post(
        GEMINI_OPENAI_IMAGE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Gemini Image API error {resp.status_code}: {resp.text}")

    data = resp.json()
    b64 = data.get("data", [{}])[0].get("b64_json")
    if not b64:
        raise SystemExit("Gemini Image API returned no image data")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(b64))


def render_html(entries: List[Dict[str, str]], target_date: dt.date) -> str:
    date_str = target_date.strftime("%Y-%m-%d")
    title = f"RSS/Gmail 摘要 - {date_str}"

    section = f"""
    <div class=\\"section\\">
      <div class=\\"section-title\\">Today</div>
      <div class=\\"section-date\\">{html.escape(date_str)}</div>
    </div>
    """

    rows = []
    for idx, e in enumerate(entries, 1):
        rows.append(
            f"""
            <article class=\"story\">
              <details class=\"story-toggle\">
                <summary>
                  <div class=\"story-head\">
                    <div class=\"story-main\">
                      <div class=\"story-source\">{html.escape(e['source'])} · {html.escape(e['category'])}</div>
                      <div class=\"story-title\">{idx}. {html.escape(e['subject'])}</div>
                      <div class=\"story-summary\">{html.escape(e['summary_zh_tw'])}</div>
                    </div>
                    {e.get('thumb_html', '')}
                  </div>
                </summary>
                <div class=\"meta\">
                  <span>{html.escape(e['from'])}</span>
                  <span>{html.escape(e['date'])}</span>
                  <span class=\"tag\">來源：{html.escape(e['source'])}</span>
                  <span class=\"tag\">分類：{html.escape(e['category'])}</span>
                </div>
                <div class=\"full\">
                  <h3>繁體中文全文</h3>
                  <pre>{html.escape(e['translated_text'])}</pre>
                  <h3>原始內容擷取</h3>
                  <pre>{html.escape(e['original_text'])}</pre>
                </div>
              </details>
            </article>
            """
        )

    if not rows:
        rows.append(
            "<section class=\"card\"><h2>沒有符合條件的郵件</h2>"
            "<p>今天沒有擷取到前一天的 RSS 郵件。</p></section>"
        )

    body = section + "\n".join(rows)

    return f"""<!doctype html>
<html lang=\"zh-Hant\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f5f5f7;
      --card: #ffffff;
      --ink: #111827;
      --accent: #0a84ff;
      --muted: #6b7280;
      --line: #ececec;
      --pill: #f2f2f7;
    }}
    body {{
      margin: 0;
      font-family: "SF Pro Display", "SF Pro Text", "Helvetica Neue", "PingFang TC", "Noto Sans TC", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 18px 20px 6px;
      text-align: left;
      max-width: 980px;
      margin: 0 auto;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 28px;
      font-weight: 700;
    }}
    .sub {{
      color: var(--muted);
      font-size: 13px;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      padding: 6px 16px 48px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .section {{
      margin: 4px 0 10px;
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 6px 6px 0;
    }}
    .section-title {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }}
    .section-date {{
      font-size: 12px;
      color: var(--muted);
    }}
    .story {{
      background: var(--card);
      border-radius: 16px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.06);
      padding: 12px 14px;
      border: 1px solid var(--line);
    }}
    .meta {{
      color: var(--muted);
      font-size: 12px;
      margin: 10px 0 6px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px 10px;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      color: #374151;
      background: var(--pill);
    }}
    .story-toggle summary {{
      cursor: pointer;
      list-style: none;
    }}
    .story-toggle summary::-webkit-details-marker {{
      display: none;
    }}
    .story-head {{
      display: flex;
      gap: 12px;
      align-items: center;
    }}
    .story-main {{
      flex: 1 1 auto;
      min-width: 0;
    }}
    .story-source {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.6px;
    }}
    .story-title {{
      font-size: 16px;
      font-weight: 700;
      color: var(--ink);
      margin-bottom: 6px;
    }}
    .story-summary {{
      font-size: 14px;
      line-height: 1.55;
      color: #111827;
    }}
    .thumb {{
      width: 76px;
      height: 76px;
      border-radius: 12px;
      background: #e5e7eb;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
      flex: 0 0 auto;
      object-fit: cover;
    }}
    .thumb--placeholder {{
      background: linear-gradient(140deg, #dbeafe 0%, #bfdbfe 45%, #a5b4fc 100%);
    }}
    .full h3 {{
      margin: 14px 0 6px;
      font-size: 12px;
      letter-spacing: 0.8px;
      color: var(--muted);
      text-transform: uppercase;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SF Mono", "Menlo", "Noto Sans TC", monospace;
      background: #f9fafb;
      padding: 12px;
      border-radius: 12px;
      font-size: 13px;
      border: 1px solid var(--line);
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class=\"sub\">每日 08:00（台北時間）自動更新</div>
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def main() -> None:
    user = env("GMAIL_USER", required=True)
    label = env("GMAIL_LABEL", default="RSS")

    now = dt.datetime.now(TZ)
    target_date = (now - dt.timedelta(days=1)).date()
    start = dt.datetime.combine(target_date, dt.time.min, tzinfo=TZ)
    end_exclusive = start + dt.timedelta(days=1)

    start_ts = int(start.timestamp())
    end_ts = int(end_exclusive.timestamp())

    query = f"label:{label} after:{start_ts} before:{end_ts}"

    access_token = refresh_gmail_access_token()
    message_ids = list_messages(access_token, user, query)

    entries: List[Dict[str, str]] = []
    images_dir = os.path.join(os.path.dirname(__file__), "..", "public", "images")
    enable_images = env("ENABLE_IMAGE_GEN", default="1") == "1"

    for msg_id in message_ids:
        msg = get_message(access_token, user, msg_id)
        payload = msg.get("payload", {})
        headers = payload.get("headers", []) or []

        subject = get_header(headers, "Subject") or "(無標題)"
        from_ = get_header(headers, "From") or ""
        date_ = get_header(headers, "Date") or ""

        raw_text = extract_text_from_payload(payload)
        raw_text = normalize_text(raw_text)
        if not raw_text:
            continue

        ai = gemini_translate_and_summarize(raw_text)
        translated_text = normalize_text(ai.get("translated_text", ""))
        summary_zh_tw = normalize_text(ai.get("summary_zh_tw", ""), max_chars=4000)

        image_rel_path = ""
        if enable_images:
            image_name = f"{msg_id}.png"
            image_path = os.path.join(images_dir, image_name)
            image_rel_path = f"images/{image_name}"
            if not os.path.exists(image_path):
                prompt = build_image_prompt(subject, summary_zh_tw or translated_text)
                generate_image(prompt, image_path)

        entries.append(
            {
                "id": msg_id,
                "subject": subject,
                "from": from_,
                "date": date_,
                "original_text": raw_text,
                "translated_text": translated_text or raw_text,
                "summary_zh_tw": summary_zh_tw or "(無摘要)",
                "category": extract_category(subject),
                "source": extract_source(from_)[0],
                "thumb_html": build_thumb_html(image_rel_path, subject),
            }
        )

    html_out = render_html(entries, target_date)
    out_path = os.path.join(os.path.dirname(__file__), "..", "public", "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    # Save raw data for debugging if needed.
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "latest.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({"date": str(target_date), "count": len(entries)}, f, ensure_ascii=False, indent=2)

    print(f"Generated {out_path} with {len(entries)} entries")


if __name__ == "__main__":
    main()
