source("rollups.R")

connect_database <- function() {
  connection <- DBI::dbConnect(
    RPostgres::Postgres(), host = Sys.getenv("PGHOST", "postgres"),
    port = as.integer(Sys.getenv("PGPORT", "5432")),
    dbname = Sys.getenv("PGDATABASE", "gbfs"), user = Sys.getenv("PGUSER", "gbfs"),
    password = Sys.getenv("PGPASSWORD"), connect_timeout = 5L,
    options = "-c role=app_analytics -c statement_timeout=120000", timezone = "UTC"
  )
  connection
}

process_chunk <- function(connection, system_id, zone, until, stale_after_s) {
  DBI::dbWithTransaction(connection, {
    locked <- DBI::dbGetQuery(
      connection,
      "select pg_try_advisory_xact_lock(hashtextextended($1, 2)) as locked",
      params = list(system_id)
    )
    locked <- locked$locked[1L]
    if (!locked) return(until)
    cursor <- DBI::dbGetQuery(
      connection,
      "select processed_until from mart.rollup_cursor where system_id = $1",
      params = list(system_id)
    )
    if (nrow(cursor) == 0L) {
      earliest <- DBI::dbGetQuery(
        connection,
        "select min(fetched_at) as earliest from core.station_status where system_id = $1",
        params = list(system_id)
      )
      earliest <- earliest$earliest[1L]
      if (is.na(earliest)) return(until)
      start <- local_midnight(earliest, zone)
    } else {
      start <- local_midnight(cursor$processed_until[1L] - 600, zone)
    }
    # Ограничиваем размер транзакции двумя местными сутками; backfill идёт порциями.
    start_date <- as.Date(format(start, tz = zone, format = "%Y-%m-%d"))
    end <- min(until, as.POSIXct(as.character(start_date + 2), tz = zone))
    if (end <= start) return(until)
    facts <- DBI::dbGetQuery(connection, paste(
      "select station_id, fetched_at, observed_until, bikes_available, docks_available,",
      "is_installed, is_renting, is_returning from core.station_status",
      "where system_id = $1 and fetched_at >= $2 and fetched_at < $3",
      "union all (select distinct on (station_id) station_id, fetched_at, observed_until,",
      "bikes_available, docks_available, is_installed, is_renting, is_returning",
      "from core.station_status where system_id = $1 and fetched_at < $2",
      "order by station_id, fetched_at desc)"
    ), params = list(system_id, start, end))
    events <- DBI::dbGetQuery(connection, paste(
      "select station_id, occurred_at, event_type, (details->>'count')::integer as count",
      "from core.station_event where system_id = $1 and occurred_at >= $2 and occurred_at < $3",
      "and event_type in ('bikes_added', 'bikes_removed')"
    ), params = list(system_id, start, end))
    DBI::dbExecute(connection, paste(
      "delete from mart.station_rollup where system_id = $1",
      "and bucket_start >= $2 and bucket_start < $3"
    ), params = list(system_id, start, end))
    station_ids <- union(as.character(facts$station_id), as.character(events$station_id))
    chunks <- split(station_ids, ceiling(seq_along(station_ids) / 100))
    # COPY не поддерживается для таблиц с RLS: staging получает схему из модели БД.
    DBI::dbExecute(connection, paste(
      "create temporary table rollup_batch",
      "(like mart.station_rollup including defaults) on commit drop"
    ))
    for (ids in chunks) {
      values <- calculate_rollups(facts[facts$station_id %in% ids, , drop = FALSE],
                                  events[events$station_id %in% ids, , drop = FALSE],
                                  start, end, zone, stale_after_s)
      if (nrow(values) == 0L) next
      values$system_id <- system_id
      DBI::dbAppendTable(connection, DBI::Id(schema = "pg_temp", table = "rollup_batch"), values)
      DBI::dbExecute(connection, paste(
        "insert into mart.station_rollup select * from pg_temp.rollup_batch"
      ))
      DBI::dbExecute(connection, "truncate pg_temp.rollup_batch")
    }
    DBI::dbExecute(connection, paste(
      "insert into mart.rollup_cursor (system_id, processed_until) values ($1, $2)",
      "on conflict (system_id) do update set processed_until = excluded.processed_until,",
      "updated_at = now()"
    ), params = list(system_id, end))
    DBI::dbGetQuery(
      connection, "select pg_notify('snapshot_ready', $1)",
      params = list(jsonlite::toJSON(list(system_id = system_id), auto_unbox = TRUE))
    )
    end
  })
}

run_rollups <- function(connection, until = Sys.time(), stale_after_s = 300) {
  systems <- DBI::dbGetQuery(connection, "select id, timezone from core.system order by id")
  for (i in seq_len(nrow(systems))) {
    tryCatch({
      repeat {
        processed <- process_chunk(connection, systems$id[i], systems$timezone[i],
                                   until, stale_after_s)
        if (processed >= until) break
      }
    }, error = function(error) {
      message("Rollup failed for ", systems$id[i], ": ", conditionMessage(error))
    })
  }
}
