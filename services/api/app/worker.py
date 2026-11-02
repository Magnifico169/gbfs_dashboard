"""Воркер приёма: без аргументов крутится циклом, с --once делает один проход."""

import argparse
import logging
import time

from app.clients.gbfs import GbfsClient
from app.core.config.settings import get_settings
from app.core.database import get_database
from app.services.ingestion import Poller


def main() -> None:
    parser = argparse.ArgumentParser(description="Приём фидов GBFS в PostgreSQL")
    parser.add_argument("--once", action="store_true", help="один проход без цикла")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    client = GbfsClient(settings)
    poller = Poller(get_database(), client, settings)
    try:
        while True:
            poller.poll_due_systems()
            if args.once:
                break
            time.sleep(settings.worker_tick_s)
    finally:
        client.close()
        get_database().dispose()


if __name__ == "__main__":
    main()
