# GBFS Dashboard

Дашборд состояния общественного транспорта (велопрокат, самокаты) на данных открытых фидов [GBFS](https://gbfs.org/documentation/reference/). Три роли: оператор, менеджер, администратор.

Принятые архитектурные решения и их обоснование — в [docs/adr/](docs/adr/). История изменений — в [CHANGELOG.md](CHANGELOG.md).

## Текущая итерация: M2 — чтение, ролл-апы и SSE

Работает приём живого фида Citi Bike в PostgreSQL 18:

* опрос фидов GBFS (ETag, таймаут, повторы с backoff), журнал запусков в `app.ingest_run`;
* нормализация GBFS v1.1 / v2.x / v3.0 в единую модель;
* валидация снимков, невалидные строки уходят в `core.station_quarantine`;
* в `core.station_status` пишутся только изменившиеся станции, `core.station_current` хранит последнее состояние;
* справочники станций ведутся как SCD2;
* схема БД описана моделями SQLAlchemy, миграции — Alembic; весь код приёма работает через ORM;
* API чтения систем, текущего состояния, истории, событий и агрегатов (`/api/v1`, описание в `/docs`);
* события из дельт счётчиков, транзакционный `NOTIFY`, SSE-снимок и последующие дельты;
* R-сервис `analytics`: идемпотентные ролл-апы 10 минут / час / местные сутки;
* TTL-кэш, объединение одинаковых запросов, отдельный пул отчётов и ограниченные очереди;
* пробы `/livez` и `/readyz`, проверки деградации и нагрузочные сценарии.

Приём написан на Python (см. [ADR-0003](docs/adr/0003-priyom-dannyh-na-python.md)) и живёт в одном приложении с API; R остаётся для ролл-апов, профилей загрузки и прогноза.

M2 реализован; контракт API, семантика агрегатов и SSE — в [docs/m2.md](docs/m2.md). Нагрузочный сценарий и результаты — в [benchmarks/README.md](benchmarks/README.md). Аутентификация, роли пользователей и зоны относятся к M3.

## Возможности

| Роль | Статус | Назначение |
|---|---|---|
| Оператор | запланировано (M4) | справка по станциям для консультации пользователей, обзор своей зоны |
| Менеджер | запланировано (M5) | анализ загрузки парка: станции, требующие внимания, профили по часам, индикаторы (полосы Боллинджера и др.), прогноз, отчёты за год, месяц, произвольный интервал |
| Администратор | запланировано (M6) | журнал приёма данных, ошибки, пользователи и права |

## Структура проекта

```
docs/adr/            архитектурные решения (ADR)
services/api/        Python: FastAPI-приложение, воркер приёма, миграции Alembic
  app/core/          настройки (config/settings.py), подключение к БД (singleton)
  app/models/        ORM-модели — единственный источник схемы
  app/schemas/       pydantic-схемы: данные GBFS, ответы API
  app/repositories/  доступ к данным через ORM
  app/services/      нормализация, валидация, приём
  app/clients/       клиент фидов GBFS
  app/api/           роутеры и зависимости FastAPI
  app/worker.py      воркер приёма
  migrations/        ревизии Alembic
  tests/             pytest
pyproject.toml       uv-воркспейс и настройки ruff
uv.lock              зафиксированные версии зависимостей
services/analytics/  R: расчёт ролл-апов и периодический воркер
benchmarks/         нагрузка Locust, SSE и проверка потери БД
fixtures/            сохранённые фиды GBFS для тестов (Citi Bike v2.3, синтетический v3.0)
docker-compose.yml   окружение: PostgreSQL 18, миграции, приём, API
.env.example         шаблон переменных окружения
.lintr               настройки lintr (R)
.github/workflows/   CI: статический анализ
CHANGELOG.md         журнал изменений
```

Планируемые каталоги (появятся вместе с кодом):

```
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

Команда поднимает PostgreSQL, применяет миграции (`migrate`), запускает приём (`ingest`), расчёт ролл-апов (`analytics`) и API (`api`, порт 8000; проверка: `curl localhost:8000/readyz`). Healthcheck API использует `/readyz`: контейнер готов, когда отвечает база. Статус приёма:

```bash
docker compose exec postgres psql -U gbfs -d gbfs -c "select feed, status, rows_in, rows_written, rows_quarantined from app.ingest_run order by id desc limit 10"
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

## Разработка

Зависимости ведёт [uv](https://docs.astral.sh/uv/) (Python 3.12, версии зафиксированы в `uv.lock`):

```bash
uv sync
```

Все параметры сервиса — в [settings.py](services/api/app/core/config/settings.py); читаются из переменных окружения и `.env` (имена `PG*` и `POSTGRES_*` равноправны), поэтому локально отдельная настройка не нужна. `HTTP_TIMEOUT_S` ограничивает ожидание HTTP-ответа, `POLL_TIMEOUT_S` задаёт паузу между опросами (30 секунд по умолчанию). Интервал системы из `app.system_setting` имеет приоритет, при успешном ответе интервал не короче TTL фида. Справочники обновляются отдельно раз в `REFERENCE_REFRESH_S`. Ошибки приёма выводятся в журнал процесса (`docker compose logs ingest`); в `app.ingest_run` хранятся статусы и счётчики.

Тесты. Интеграционные используют PostgreSQL из compose: на каждый тест создаётся отдельная база, схема накатывается настоящим Alembic.

```bash
cd services/api && uv run pytest
```

Приложение локально:

```bash
cd services/api && uv run uvicorn app.main:app --reload
```

```bash
cd services/api && uv run python -m app.worker
```

Миграции. После изменения моделей в `app/models/` ревизия генерируется на пустой базе и правится вручную (схемы, роли, гранты):

```bash
cd services/api && uv run alembic revision --autogenerate -m "описание"
```

```bash
cd services/api && uv run alembic upgrade head
```

Расхождение моделей и БД:

```bash
cd services/api && uv run alembic check
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
| M1 | Приём данных на Python: Citi Bike, нормализация v2/v3, валидация; схема PostgreSQL — выполнено |
| M2 | `station_current`, ролл-апы, API чтения, SSE; нагрузочный замер — выполнено |
| M3 | Python API: аутентификация, роли, зоны |
| M4 | Экран оператора с обработкой ошибок |
| M5 | Экран менеджера: отчёты по интервалам, графики на клиенте |
| M6 | Экран администратора |
| M7 | Охват всего реестра GBFS, нагрузочное тестирование, Helm |

## Лицензия

См. [LICENSE](LICENSE).
