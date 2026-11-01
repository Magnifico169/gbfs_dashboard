-- M1: схема данных. Владение таблицами — по ADR-0002, раздел 3:
-- у каждой таблицы один писатель, различие закреплено ролями БД.

create schema core;
create schema mart;
create schema app;

do $$
begin
  if not exists (select from pg_roles where rolname = 'app_ingest') then
    create role app_ingest nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'app_api') then
    create role app_api nologin;
  end if;
end
$$;

-- app: настройки систем (пишет Python/админка, читает R) и журнал приёма (пишет R)

create table app.system_setting (
  system_id         text primary key,
  discovery_url     text not null,
  enabled           boolean not null default true,
  poll_interval_s   integer check (poll_interval_s > 0),  -- null = брать ttl из фида
  updated_at        timestamptz not null default now()
);

insert into app.system_setting (system_id, discovery_url)
values ('lyft_nyc', 'https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json');

create table app.ingest_run (
  run_id            bigint generated always as identity primary key,
  system_id         text not null,
  feed              text not null,
  started_at        timestamptz not null,
  finished_at       timestamptz not null,
  status            text not null check (status in ('ok', 'not_modified', 'error')),
  http_status       integer,
  rows_in           integer not null default 0,
  rows_written      integer not null default 0,
  rows_quarantined  integer not null default 0,
  error             text
);
create index on app.ingest_run (system_id, feed, started_at desc);

-- core: справочники

create table core.system (
  system_id      text primary key,
  name           text not null,
  operator       text,
  timezone       text not null,
  language       text,
  gbfs_version   text not null,
  updated_at     timestamptz not null default now()
);

create table core.region (
  system_id   text not null,
  region_id   text not null,
  name        text not null,
  primary key (system_id, region_id)
);

create table core.vehicle_type (
  system_id           text not null,
  vehicle_type_id     text not null,
  form_factor         text not null,
  propulsion_type     text not null,
  name                text,
  max_range_meters    double precision,
  primary key (system_id, vehicle_type_id)
);

-- SCD2: при изменении атрибутов станции текущая версия закрывается, открывается новая
create table core.station (
  station_pk    bigint generated always as identity primary key,
  system_id     text not null,
  station_id    text not null,
  name          text not null,
  short_name    text,
  lat           double precision not null,
  lon           double precision not null,
  address       text,
  region_id     text,
  capacity      integer,
  valid_from    timestamptz not null,
  valid_to      timestamptz
);
create unique index station_current_version_uq
  on core.station (system_id, station_id) where valid_to is null;

-- core: состояние станций

-- Общий набор колонок состояния. Необязательные в GBFS счётчики (диски, неисправные) могут быть null.
-- last_reported из фида — reported_at; null означает
-- «станция не отчитывалась» (фид отдаёт заглушку 86400), это не то же самое, что «0 доступно».
create table core.station_current (
  system_id         text not null,
  station_id        text not null,
  reported_at       timestamptz,
  fetched_at        timestamptz not null,  -- когда состояние изменилось
  checked_at        timestamptz not null,  -- когда в последний раз сверяли с фидом
  bikes_available   integer not null,
  ebikes_available  integer,
  bikes_disabled    integer,
  docks_available   integer,
  docks_disabled    integer,
  is_installed      boolean not null,
  is_renting        boolean not null,
  is_returning      boolean not null,
  primary key (system_id, station_id)
);

create table core.station_status (
  system_id         text not null,
  station_id        text not null,
  reported_at       timestamptz,
  fetched_at        timestamptz not null,
  bikes_available   integer not null,
  ebikes_available  integer,
  bikes_disabled    integer,
  docks_available   integer,
  docks_disabled    integer,
  is_installed      boolean not null,
  is_renting        boolean not null,
  is_returning      boolean not null,
  primary key (system_id, station_id, fetched_at)
) partition by range (fetched_at);

create table core.station_status_default partition of core.station_status default;

-- Суточные партиции создаёт приём перед записью; старые удаляются целиком (ADR-0002, п. 2).
create function core.ensure_station_status_partitions(days_ahead integer default 2)
returns void
language plpgsql
security definer
set search_path = core, pg_temp
as $$
declare
  d date;
begin
  for d in
    select generate_series(current_date - 1, current_date + days_ahead, interval '1 day')::date
  loop
    execute format(
      'create table if not exists core.%I partition of core.station_status
         for values from (%L) to (%L)',
      'station_status_' || to_char(d, 'YYYYMMDD'), d, d + 1
    );
  end loop;
end
$$;

-- Таблица создаётся в M1, наполняется с M2 (вывод событий из дельт).
create table core.station_event (
  event_id      bigint generated always as identity primary key,
  system_id     text not null,
  station_id    text not null,
  occurred_at   timestamptz not null,
  event_type    text not null,
  details       jsonb
);
create index on core.station_event (system_id, station_id, occurred_at desc);

create table core.station_quarantine (
  quarantine_id  bigint generated always as identity primary key,
  system_id      text not null,
  station_id     text not null,
  fetched_at     timestamptz not null,
  reason         text not null,
  payload        jsonb not null
);
create index on core.station_quarantine (system_id, fetched_at desc);

-- Права

revoke all on schema core, mart, app from public;
grant usage on schema core, mart, app to app_ingest, app_api;

grant select, insert, update, delete on all tables in schema core to app_ingest;
grant select, insert, update, delete on all tables in schema mart to app_ingest;
grant usage on all sequences in schema core to app_ingest;
grant execute on function core.ensure_station_status_partitions(integer) to app_ingest;

grant select on app.system_setting to app_ingest;
grant select, insert on app.ingest_run to app_ingest;
grant usage on all sequences in schema app to app_ingest;

grant select on all tables in schema core, mart to app_api;
grant select, insert, update, delete on all tables in schema app to app_api;
grant usage on all sequences in schema app to app_api;
