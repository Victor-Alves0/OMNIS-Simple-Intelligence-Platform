from __future__ import annotations

import sys
from pathlib import Path

from telethon import TelegramClient

from app.config.settings import (
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_SESSION_NAME,
    TELEGRAM_SESSIONS_DIR,
)


def main() -> int:
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        print("❌ TELEGRAM_API_ID e/ou TELEGRAM_API_HASH não estão configurados.")
        print("   Defina-os em .env antes de executar este script.")
        return 1

    sessions_dir = Path(TELEGRAM_SESSIONS_DIR or "./volumes/telegram_sessions")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / TELEGRAM_SESSION_NAME

    print(" OMNIS | Criador de sessão Telegram")
    print(f"- Pasta de sessões: {sessions_dir}")
    print(f"- Nome do arquivo : {session_path.name}")
    print("")
    print("Será solicitado o seu telefone + código do Telegram.")
    print("Use o mesmo número configurado para o coletor.")
    print("")

    client = TelegramClient(str(session_path), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    try:
        client.start()
        print("")
        print("✅ Sessão criada/atualizada com sucesso.")
        print(f"   Arquivo salvo em: {session_path}")
    except Exception as exc:
        print(f"❌ Falha ao criar sessão: {exc}")
        return 2
    finally:
        client.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
