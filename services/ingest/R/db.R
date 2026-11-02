# Слой доступа к PostgreSQL. Приём пишет только в core/mart и журнал app.ingest_run;
# после подключения соединение переключается на роль app_ingest, чтобы это обеспечивала БД.
# Схему создаёт Alembic (db/), приём её не мигрирует.

ingest_role <- "app_ingest"

db_connect <- function(env = Sys.getenv()) {
  DBI::dbConnect(
    RPostgres::Postgres(),
    host = env[["PGHOST"]],
    port = as.integer(env[["PGPORT"]]),
    dbname = env[["PGDATABASE"]],
    user = env[["PGUSER"]],
    password = env[["PGPASSWORD"]]
  )
}

db_set_ingest_role <- function(con) {
  DBI::dbExecute(con, paste0("set role ", ingest_role))
  invisible(con)
}

db_enabled_systems <- function(con) {
  DBI::dbGetQuery(
    con,
    "select system_id, discovery_url, poll_interval_s from app.system_setting where enabled"
  )
}

db_ensure_partitions <- function(con, days_ahead = 2L) {
  DBI::dbGetQuery(con, "select core.ensure_station_status_partitions($1)", list(days_ahead))
  invisible(NULL)
}

db_log_run <- function(con, system_id, feed, started_at, status, http_status = NA_integer_,
                       rows_in = 0L, rows_written = 0L, rows_quarantined = 0L,
                       error = NA_character_) {
  DBI::dbExecute(con, "
    insert into app.ingest_run
      (system_id, feed, started_at, finished_at, status, http_status,
       rows_in, rows_written, rows_quarantined, error)
    values ($1, $2, $3, now(), $4, $5, $6, $7, $8, $9)",
    list(
      system_id, feed, started_at, status, http_status,
      rows_in, rows_written, rows_quarantined, error
    )
  )
  invisible(NULL)
}

db_stage <- function(con, name, data) {
  DBI::dbWriteTable(con, name, data, temporary = TRUE, overwrite = TRUE)
}

db_upsert_system <- function(con, info, gbfs_version) {
  DBI::dbExecute(con, "
    insert into core.system (system_id, name, operator, timezone, language, gbfs_version)
    values ($1, $2, $3, $4, $5, $6)
    on conflict (system_id) do update set
      name = excluded.name, operator = excluded.operator, timezone = excluded.timezone,
      language = excluded.language, gbfs_version = excluded.gbfs_version, updated_at = now()",
    list(
      info$system_id, info$name, info$operator, info$timezone, info$language, gbfs_version
    )
  )
  invisible(NULL)
}

db_upsert_regions <- function(con, regions) {
  if (nrow(regions) == 0L) {
    return(invisible(0L))
  }
  db_stage(con, "tmp_region", regions)
  DBI::dbExecute(con, "
    insert into core.region (system_id, region_id, name)
    select system_id, region_id, name from tmp_region
    on conflict (system_id, region_id) do update set name = excluded.name")
}

db_upsert_vehicle_types <- function(con, vehicle_types) {
  if (nrow(vehicle_types) == 0L) {
    return(invisible(0L))
  }
  db_stage(con, "tmp_vehicle_type", vehicle_types)
  DBI::dbExecute(con, "
    insert into core.vehicle_type
      (system_id, vehicle_type_id, form_factor, propulsion_type, name, max_range_meters)
    select system_id, vehicle_type_id, form_factor, propulsion_type, name, max_range_meters
    from tmp_vehicle_type
    on conflict (system_id, vehicle_type_id) do update set
      form_factor = excluded.form_factor, propulsion_type = excluded.propulsion_type,
      name = excluded.name, max_range_meters = excluded.max_range_meters")
}

# SCD2: изменившуюся или пропавшую из фида станцию закрываем, изменившуюся открываем заново.
db_sync_stations <- function(con, stations, system_id, now) {
  if (nrow(stations) == 0L) {
    return(invisible(0L))
  }
  DBI::dbWithTransaction(con, {
    db_stage(con, "tmp_station", stations)
    closed <- DBI::dbExecute(con, "
      update core.station s set valid_to = $2
      where s.system_id = $1 and s.valid_to is null
        and not exists (
          select 1 from tmp_station t
          where t.system_id = s.system_id and t.station_id = s.station_id
            and (t.name, t.short_name, t.lat, t.lon, t.address, t.region_id, t.capacity)
                is not distinct from
                (s.name, s.short_name, s.lat, s.lon, s.address, s.region_id, s.capacity)
        )", list(system_id, now))
    DBI::dbExecute(con, "
      insert into core.station
        (system_id, station_id, name, short_name, lat, lon, address, region_id, capacity,
         valid_from)
      select t.system_id, t.station_id, t.name, t.short_name, t.lat, t.lon, t.address,
             t.region_id, t.capacity, $1
      from tmp_station t
      where not exists (
        select 1 from core.station s
        where s.system_id = t.system_id and s.station_id = t.station_id and s.valid_to is null
      )", list(now))
    closed
  })
}

# Вместимость актуальных версий станций: именованный вектор station_id -> capacity.
db_capacity <- function(con, system_id) {
  rows <- DBI::dbGetQuery(
    con,
    "select station_id, capacity from core.station
     where system_id = $1 and valid_to is null and capacity is not null",
    list(system_id)
  )
  stats::setNames(rows$capacity, rows$station_id)
}

db_quarantine <- function(con, status, reason, fetched_at) {
  if (nrow(status) == 0L) {
    return(invisible(0L))
  }
  payload <- vapply(seq_len(nrow(status)), function(i) {
    as.character(jsonlite::toJSON(status[i, ], auto_unbox = TRUE, na = "null", POSIXt = "ISO8601"))
  }, character(1L))
  rows <- data.frame(
    system_id = status$system_id,
    station_id = ifelse(is.na(status$station_id), "", status$station_id),
    fetched_at = fetched_at,
    reason = reason,
    payload = payload
  )
  DBI::dbAppendTable(con, DBI::Id(schema = "core", table = "station_quarantine"), rows)
}

state_columns <- c(
  "bikes_available", "ebikes_available", "bikes_disabled", "docks_available", "docks_disabled",
  "is_installed", "is_renting", "is_returning"
)

# Пишет в факты только станции, чьё состояние изменилось (reported_at не сравниваем:
# фид может обновлять метку без изменения счётчиков). Возвращает число записанных строк.
db_write_status <- function(con, status, fetched_at) {
  if (nrow(status) == 0L) {
    return(0L)
  }
  state_values <- function(alias) {
    paste0(alias, ".", state_columns, collapse = ", ")
  }
  DBI::dbWithTransaction(con, {
    db_stage(con, "tmp_status", status)
    DBI::dbExecute(con, paste0("
      create temp table tmp_changed on commit drop as
      select t.* from tmp_status t
      left join core.station_current c
        on c.system_id = t.system_id and c.station_id = t.station_id
      where c.station_id is null
        or (", state_values("t"), ") is distinct from (", state_values("c"), ")"))
    all_columns <- paste(
      c("system_id", "station_id", "reported_at", state_columns),
      collapse = ", "
    )
    insert_status <- paste0(
      "insert into core.station_status (", all_columns, ", fetched_at) ",
      "select ", all_columns, ", $1 from tmp_changed"
    )
    written <- DBI::dbExecute(con, insert_status, list(fetched_at))
    upsert_current <- paste0(
      "insert into core.station_current (", all_columns, ", fetched_at, checked_at) ",
      "select ", all_columns, ", $1, $1 from tmp_changed ",
      "on conflict (system_id, station_id) do update set ",
      "reported_at = excluded.reported_at, fetched_at = excluded.fetched_at, ",
      "checked_at = excluded.checked_at, ",
      paste0(state_columns, " = excluded.", state_columns, collapse = ", ")
    )
    DBI::dbExecute(con, upsert_current, list(fetched_at))
    touch_current <- paste(
      "update core.station_current c",
      "set checked_at = $1, reported_at = t.reported_at",
      "from tmp_status t",
      "where c.system_id = t.system_id and c.station_id = t.station_id and c.checked_at < $1"
    )
    DBI::dbExecute(con, touch_current, list(fetched_at))
    written
  })
}
