# GBFS Dashboard

Дашборд состояния общественного транспорта (велопрокат, самокаты) на данных открытых фидов [GBFS](https://gbfs.org/documentation/reference/). Три роли: оператор, менеджер, администратор.

Принятые архитектурные решения и их обоснование — в [docs/adr/](docs/adr/). История изменений — в [CHANGELOG.md](CHANGELOG.md).

## Текущая итерация: M1 — приём данных

Работает приём живого фида Citi Bike в PostgreSQL 18:

* опрос фидов GBFS (ETag, таймаут, повторы с backoff), журнал запусков в `app.ingest_run`;
* нормализация GBFS v1.1 / v2.x / v3.0 в единую модель;
* валидация снимков, невалидные строки уходят в `core.station_quarantine`;
* в `core.station_status` пишутся только изменившиеся станции, `core.station_current` хранит последнее состояние;
* справочники станций ведутся как SCD2;
* схема БД и миграции (Alembic).

Пока не реализовано (M2): события из дельт (`core.station_event` создана, но пуста), `NOTIFY`, ролл-апы, API.

## Возможности

| Роль | Статус | Назначение |
|---|---|---|
| Оператор | запланировано (M4) | справка по станциям для консультации пользователей, обзор своей зоны |
| Менеджер | запланировано (M5) | анализ загрузки парка: станции, требующие внимания, профили по часам, индикаторы (полосы Боллинджера и др.), прогноз, отчёты за год, месяц, произвольный интервал |
| Администратор | запланировано (M6) | журнал приёма данных, ошибки, пользователи и права |

## Структура проекта

```
docs/adr/            архитектурные решения (ADR)
db/                  миграции PostgreSQL (Alembic, SQL в db/migrations/versions)
services/ingest/     R: приём фидов GBFS, нормализация, валидация, тесты (testthat)
fixtures/            сохранённые фиды GBFS для тестов (Citi Bike v2.3, синтетический v3.0)
docker-compose.yml   окружение: PostgreSQL 18, миграции, приём
.env.example         шаблон переменных окружения
pyproject.toml       настройки ruff (Python)
.lintr               настройки lintr (R)
.github/workflows/   CI: статический анализ
CHANGELOG.md         журнал изменений
```

Планируемые каталоги (появятся вместе с кодом):

```
services/api/        Python (FastAPI): HTTP API, роли, задачи, SSE
web/                 фронтенд
deploy/              конфигурация развёртывания (позже — Helm)
```

## Запуск

Требуется Docker с Compose v2.

```bash
cp .env.example .env
```

В `.env` замените `POSTGRES_PASSWORD` на свой пароль.

```bash
docker compose up -d --wait
```

Команда поднимает PostgreSQL, применяет миграции (сервис `migrate`) и запускает приём (`ingest`). Статус приёма:

```bash
docker compose exec postgres psql -U gbfs -d gbfs -c "select feed, status, rows_in, rows_written, rows_quarantined from app.ingest_run order by run_id desc limit 10"
```

Один проход приёма без цикла:

```bash
docker compose run --rm ingest Rscript run.R --once
```

PostgreSQL будет доступен на `localhost:5433` (порт меняется через `POSTGRES_PORT`; 5433 выбран, чтобы не конфликтовать с локальным PostgreSQL на 5432).

Проверка:

```bash
docker compose exec postgres psql -U gbfs -d gbfs -c "select version()"
```

Остановка (данные сохраняются в томе `pgdata`):

```bash
docker compose down
```

Остановка с удалением данных:

```bash
docker compose down -v
```

## Тесты

Тесты приёма (testthat) запускаются в контейнере; интеграционные используют PostgreSQL из compose и создают на время прогона отдельную базу:

```bash
set -a; . ./.env; set +a
docker compose build ingest
docker run --rm --network host -e PGHOST=localhost -e PGPORT=$POSTGRES_PORT -e PGUSER=$POSTGRES_USER -e PGPASSWORD=$POSTGRES_PASSWORD -e PGDATABASE=$POSTGRES_DB -e FIXTURES_DIR=/fixtures -e MIGRATIONS_DIR=/migrations -v $PWD/fixtures:/fixtures:ro -v $PWD/db/migrations/versions:/migrations:ro -v $PWD/services/ingest:/app -w /app/tests/testthat gbfs_dashboard-ingest Rscript -e 'testthat::test_dir(".")'
```

## Статический анализ

Проверки идут в GitHub Actions ([lint.yml](.github/workflows/lint.yml)) на каждый push в `main` и pull request:

* Python — [ruff](https://docs.astral.sh/ruff/): `check` и `format --check`, настройки в `pyproject.toml`;
* R — [lintr](https://lintr.r-lib.org/), настройки в `.lintr`; если R-файлов нет, шаг пропускается.

Локально:

```bash
ruff check .
```

```bash
ruff format --check .
```

```bash
Rscript -e 'lintr::lint_dir(".")'
```

## План

| Веха | Результат |
|---|---|
| M0 | Концепт и ADR |
| M1 | Приём данных на R: Citi Bike, нормализация v2/v3, валидация; схема PostgreSQL *(текущая)* |
| M2 | `station_current`, ролл-апы, API чтения, SSE; нагрузочный замер |
| M3 | Python API: аутентификация, роли, зоны |
| M4 | Экран оператора с обработкой ошибок |
| M5 | Экран менеджера: отчёты по интервалам, графики на клиенте |
| M6 | Экран администратора |
| M7 | Охват всего реестра GBFS, нагрузочное тестирование, Helm |

## Лицензия

См. [LICENSE](LICENSE).
