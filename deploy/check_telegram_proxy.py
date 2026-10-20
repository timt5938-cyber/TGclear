"""Check MTProto connectivity through the configured local SOCKS gateway."""

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    client = TelegramClient(
        StringSession(), 1, "0" * 32,
        proxy={"proxy_type": "socks5", "addr": "127.0.0.1", "port": 2080, "rdns": True},
        timeout=8, connection_retries=1,
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=16)
        if not client.is_connected():
            raise RuntimeError("MTProto did not connect")
        print("MTProto over local SOCKS: connected")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
