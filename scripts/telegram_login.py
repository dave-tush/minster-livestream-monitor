"""One-time helper: log in to Telegram and print a TELEGRAM_SESSION_STRING for Dominion Radio.

Run this on YOUR computer (not on the server):

    pip install telethon
    python scripts/telegram_login.py

You need TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org  ->  API development tools.
Telegram will send a login code to the Telegram app of the account you use.

Use a dedicated Telegram account that is a member of the channel if you can. The session string
gives full access to that account, so treat it like a password and never commit it.
"""
import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID") or input("API ID: ").strip())
    api_hash = os.getenv("TELEGRAM_API_HASH") or input("API hash: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:   # asks phone, code, 2FA
        print("\nLogged in. Put this in your environment (keep it secret):\n")
        print(f"TELEGRAM_SESSION_STRING={client.session.save()}\n")


if __name__ == "__main__":
    asyncio.run(main())
