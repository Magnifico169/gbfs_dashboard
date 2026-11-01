# GBFS Dashboard

Дашборд состояния общественного транспорта (велопрокат, самокаты) на данных открытых фидов [GBFS](https://gbfs.org/documentation/reference/). Три роли: оператор, менеджер, администратор.

Принятые архитектурные решения и их обоснование — в [docs/adr/](docs/adr/). История изменений — в [CHANGELOG.md](CHANGELOG.md).

## Текущая итерация: M0 — концепт

Прикладного кода пока нет. В репозитории есть:

* архитектурные решения (ADR);
* `docker-compose.yml` с PostgreSQL 18 — база, на которой строится всё остальное.

## Возможности

| Роль | Статус | Назначение |
|---|---|---|
| Оператор | запланировано (M4) | справка по станциям для консультации пользователей, обзор своей зоны |
| Менеджер | запланировано (M5) | анализ загрузки парка: станции, требующие внимания, профили по часам, индикаторы (полосы Боллинджера и др.), прогноз, отчёты за год, месяц, произвольный интервал |
| Администратор | запланировано (M6) | журнал приёма данных, ошибки, пользователи и права |

## Структура проекта

```
docs/adr/            архитектурные решения (ADR)
docker-compose.yml   окружение разработки (пока только PostgreSQL 18)
.env.example         шаблон переменных окружения
pyproject.toml       настройки ruff (Python)
.lintr               настройки lintr (R)
.github/workflows/   CI: статический анализ
CHANGELOG.md         журнал изменений
```

Планируемые каталоги (появятся вместе с кодом):

```
services/ingest/     R: приём фидов GBFS, нормализация, валидация, ролл-апы
services/api/        Python (FastAPI): HTTP API, роли, задачи, SSE
web/                 фронтенд
db/migrations/       миграции PostgreSQL
fixtures/            сохранённые фиды GBFS для тестов
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
| M0 | Концепт и ADR *(текущая)* |
| M1 | Приём данных на R: Citi Bike, нормализация v2/v3, валидация; схема PostgreSQL |
| M2 | `station_current`, ролл-апы, API чтения, SSE; нагрузочный замер |
| M3 | Python API: аутентификация, роли, зоны |
| M4 | Экран оператора с обработкой ошибок |
| M5 | Экран менеджера: отчёты по интервалам, графики на клиенте |
| M6 | Экран администратора |
| M7 | Охват всего реестра GBFS, нагрузочное тестирование, Helm |

## Лицензия

См. [LICENSE](LICENSE).
