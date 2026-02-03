# Gmail RSS Digest (Daily Static Page)

每天 08:00（台北時間）自動抓取 Gmail 標籤內的 RSS 訂閱信，產生「標題、內容擷取、繁中翻譯、繁中摘要」的靜態頁面，並部署到 GitHub Pages。

## 設定步驟

### 1) Gmail 準備
1. 在 Gmail 建立標籤：`RSS`
2. 用篩選器把你的 RSS 訂閱信件自動套用此標籤
3. 建立 Google Cloud 專案並啟用 Gmail API
4. 取得 OAuth Client ID/Secret，並用 OAuth Playground 取得 refresh token
   - OAuth scope: `https://www.googleapis.com/auth/gmail.readonly`

### 2) GitHub Secrets
在 repo 的 `Settings → Secrets and variables → Actions` 新增：
- `GEMINI_API_KEY`
- `GEMINI_TEXT_MODEL`（可選，預設 `gemini-2.5-flash`）
- `GEMINI_IMAGE_MODEL`（可選，預設 `imagen-3.0-generate-002`）
- `GEMINI_IMAGE_SIZE`（可選，預設 `1024x1024`）
- `ENABLE_IMAGE_GEN`（可選，`1` 開啟、`0` 關閉；預設 `1`）
- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`
- `GMAIL_USER`（通常是你的 Gmail 帳號，例如 `you@gmail.com`）
- `GMAIL_LABEL`（可選，預設 `RSS`）

### 3) GitHub Pages
在 repo 的 `Settings → Pages` 中選擇 `GitHub Actions` 作為部署來源。

## 執行方式（本機）
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY=...
export GEMINI_TEXT_MODEL=gemini-2.5-flash
export GEMINI_IMAGE_MODEL=imagen-3.0-generate-002
export GEMINI_IMAGE_SIZE=1024x1024
export ENABLE_IMAGE_GEN=1
export GMAIL_CLIENT_ID=...
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=...
export GMAIL_USER=you@gmail.com
export GMAIL_LABEL=RSS

python src/generate_site.py
```

輸出：`public/index.html`

## 注意事項
- GitHub Actions 排程以 UTC 計算，`0 0 * * *` 對應台北時間 08:00。
- 靜態頁為公開頁面（GitHub Pages 公開）。
- 內容擷取會自動做簡單清理，若原文過長會截斷避免頁面過大。
