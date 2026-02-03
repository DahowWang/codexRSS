#!/usr/bin/env python3
import json
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    creds_path = os.getenv("GMAIL_CREDENTIALS_FILE", "client_secret.json")
    creds_file = Path(creds_path)
    if not creds_file.exists():
        raise SystemExit(
            f"找不到 {creds_path}。請把 Google OAuth 的 client_secret.json 放在專案根目錄，"
            "或用 GMAIL_CREDENTIALS_FILE 指向檔案路徑。"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)

    data = {
        "GMAIL_CLIENT_ID": creds.client_id,
        "GMAIL_CLIENT_SECRET": creds.client_secret,
        "GMAIL_REFRESH_TOKEN": creds.refresh_token,
        "GMAIL_USER": input("Gmail 帳號（例如 you@gmail.com）： ").strip(),
    }

    print("\n請把以下值貼到 GitHub Secrets：\n")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
