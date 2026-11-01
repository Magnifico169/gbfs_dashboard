FROM python:3.12-slim

WORKDIR /db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini .
COPY migrations migrations

CMD ["alembic", "upgrade", "head"]
