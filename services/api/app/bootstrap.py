"""Создание первого администратора: python -m app.bootstrap USERNAME."""

import argparse
from getpass import getpass

from sqlalchemy import select, text

from app.core.database import get_database
from app.models.auth import UserAccount
from app.repositories.accounts import AccountRepository
from app.schemas.auth import UserCreate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass("Пароль (не меньше 12 символов): ")
    if password != getpass("Повтор пароля: "):
        parser.error("Пароли не совпадают")
    body = UserCreate(username=args.username, password=password, role="admin")
    database = get_database()
    try:
        with database.transaction() as session:
            session.execute(text("select pg_advisory_xact_lock(73432003)"))
            if session.scalar(select(UserAccount.id).where(UserAccount.role_id == "admin")):
                parser.error("Администратор уже создан; используйте API")
            AccountRepository(session, None).create_user(body)
        print("Администратор создан")
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
