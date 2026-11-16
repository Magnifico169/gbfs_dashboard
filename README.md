# GBFS Dashboard

Дашборд состояния общественного транспорта (велопрокат, самокаты) на данных открытых фидов [GBFS](https://gbfs.org/documentation/reference/). Три роли: оператор, менеджер, администратор.

Принятые архитектурные решения и их обоснование — в [docs/adr/](docs/adr/). История изменений — в [CHANGELOG.md](CHANGELOG.md).

## Текущая итерация: M5 — экраны оператора и менеджера

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
* собственные логины, Argon2id, короткие JWT и ротация refresh-сеансов;
* роли оператора, менеджера и администратора, зоны по регионам, PostgreSQL RLS;
* административный API пользователей и зон, журнал действий, изоляция кэша и SSE;
* экран оператора: поиск, карта, карточка, ближайшие станции, SSE и резервный опрос;
* экран менеджера: календарные и произвольные интервалы, сравнение, рейтинг, CSV и печать в PDF;
* R-профили и прогноз загрузки, внимание к станциям, клиентские индикаторы и тренд электрического парка;
* пробы `/livez` и `/readyz`, проверки деградации и нагрузочные сценарии.

Приём написан на Python (см. [ADR-0003](docs/adr/0003-priyom-dannyh-na-python.md)) и живёт в одном приложении с API; R остаётся для ролл-апов, профилей загрузки и прогноза.

M2 реализован; контракт API, семантика агрегатов и SSE — в [docs/m2.md](docs/m2.md). Нагрузочный сценарий и результаты — в [benchmarks/README.md](benchmarks/README.md). M3 реализован; права, настройка пользователей и зон, срок отзыва токенов — в [docs/m3.md](docs/m3.md).

M4 и M5 реализованы; интерфейс, точность отчётов и ограничения прогноза — в [docs/m4-m5.md](docs/m4-m5.md).

## Возможности

| Роль | Статус | Назначение |
|---|---|---|
| Оператор | реализовано (M4) | справка по станциям для консультации пользователей, обзор своей зоны |
| Менеджер | реализовано (M5) | анализ загрузки парка: станции, требующие внимания, профили по часам, индикаторы (полосы Боллинджера и др.), прогноз, отчёты за год, месяц, произвольный интервал |
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
  app/web/           Jinja-шаблоны, HTMX, стили, локальные uPlot и Leaflet
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
deploy/              конфигурация развёртывания (позже — Helm)
```

## Запуск

Требуется Docker с Compose v2.

```bash
cp .env.example .env
```

В `.env` задайте `POSTGRES_PASSWORD` и `AUTH_SECRET` — общий секрет подписи JWT (не меньше 32 байт). Секрет можно сгенерировать командой `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`.

```bash
docker compose up -d --wait
```

Команда поднимает PostgreSQL, применяет миграции (`migrate`), запускает приём (`ingest`), расчёт ролл-апов (`analytics`) и API (`api`, порт 8000; проверка: `curl localhost:8000/readyz`). Healthcheck API использует `/readyz`: контейнер готов, когда отвечает база. Пользователи могут создать аккаунт по ссылке «Зарегистрироваться» на странице входа. Новые аккаунты получают роль оператора и зону «Вся система» в выбранной системе. Роли и зоны меняет администратор. Создайте первого администратора (пароль запрашивается скрыто):

```bash
docker compose exec api python -m app.bootstrap admin
```

Браузерный вход — [http://localhost:8000](http://localhost:8000). Укажите логин, пароль и идентификатор назначенной системы (для Citi Bike — `lyft_nyc`). После входа откроется экран вашей роли. Для обновления существующего окружения: `docker compose up -d --build --wait` (миграции до `0005`, новые API и analytics).

Программный вход — `POST /api/v1/auth/login`; данные требуют заголовок `Authorization: Bearer <access_token>`. Пользователи и зоны настраиваются через `/api/v1/admin`; порядок действий описан в [M3](docs/m3.md). Пробы остаются публичными. Статус приёма:

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
cd services/api && DB_ROLE=app_api uv run uvicorn app.main:app --reload
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

Дополнительные проверки M4/M5:

```bash
node --test services/api/tests/test_chart_math.cjs
cd services/analytics && Rscript tests/test_insights.R
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
| M3 | Python API: аутентификация, роли, зоны — выполнено |
| M4 | Экран оператора с обработкой ошибок — выполнено |
| M5 | Экран менеджера: отчёты по интервалам, графики на клиенте — выполнено |
| M6 | Экран администратора |
| M7 | Охват всего реестра GBFS, нагрузочное тестирование, Helm |

## Лицензия

См. [LICENSE](LICENSE).
