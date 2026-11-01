# Оркестрация приёма: опрос фидов, нормализация, валидация, запись. Функции apply_* принимают
# уже разобранный JSON и не ходят в сеть, поэтому проверяются на сохранённых фикстурах.

REFERENCE_FEEDS <- c("system_information", "station_information", "vehicle_types", "system_regions")
REFERENCE_REFRESH_S <- 3600
MIN_POLL_S <- 10
MAX_POLL_S <- 300
MAX_BACKOFF_S <- 300

log_msg <- function(...) {
  message(format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"), " ", paste0(...))
}

apply_reference <- function(con, system_id, version, feeds, now = Sys.time()) {
  info <- normalize_system_information(feeds$system_information, system_id)
  db_upsert_system(con, info, version)

  stations <- normalize_station_information(feeds$station_information, system_id)
  db_sync_stations(con, stations, system_id, now)

  vehicle_types <- NULL
  if (!is.null(feeds$vehicle_types)) {
    vehicle_types <- normalize_vehicle_types(feeds$vehicle_types, system_id)
    db_upsert_vehicle_types(con, vehicle_types)
  }
  if (!is.null(feeds$system_regions)) {
    db_upsert_regions(con, normalize_regions(feeds$system_regions, system_id))
  }
  list(stations = nrow(stations), vehicle_types = vehicle_types)
}

apply_station_status <- function(con, system_id, feed, vehicle_types = NULL,
                                 fetched_at = Sys.time()) {
  status <- normalize_station_status(feed, system_id, vehicle_types)
  reason <- validate_station_status(status, db_capacity(con, system_id), fetched_at)
  rejected <- !is.na(reason)
  db_quarantine(con, status[rejected, , drop = FALSE], reason[rejected], fetched_at)
  written <- db_write_status(con, status[!rejected, , drop = FALSE], fetched_at)
  list(rows_in = nrow(status), rows_written = written, rows_quarantined = sum(rejected))
}

new_system_state <- function(setting) {
  state <- new.env(parent = emptyenv())
  state$system_id <- setting$system_id
  state$discovery_url <- setting$discovery_url
  state$poll_interval_s <- setting$poll_interval_s
  state$version <- NULL
  state$feed_urls <- NULL
  state$vehicle_types <- NULL
  state$etags <- list()
  state$reference_at <- as.POSIXct(0, origin = "1970-01-01", tz = "UTC")
  state$next_due <- as.POSIXct(0, origin = "1970-01-01", tz = "UTC")
  state$failures <- 0L
  state
}

# Забирает фид и пишет запуск в журнал. Ошибка возвращается значением, а не исключением.
poll_feed <- function(con, state, feed, url) {
  started_at <- Sys.time()
  response <- fetch_json(url, state$etags[[url]])
  status <- if (!is.null(response$error)) {
    "error"
  } else if (response$status == 304L) {
    "not_modified"
  } else {
    "ok"
  }
  if (!is.null(response$etag)) {
    state$etags[[url]] <- response$etag
  }
  db_log_run(
    con, state$system_id, feed, started_at, status, response$status,
    error = if (is.null(response$error)) NA_character_ else response$error
  )
  response
}

refresh_reference <- function(con, state, now) {
  discovery <- poll_feed(con, state, "gbfs", state$discovery_url)
  if (is.null(discovery$body)) {
    stop("discovery: ", discovery$error %||% "пустой ответ", call. = FALSE)
  }
  parsed <- parse_discovery(discovery$body)
  state$version <- parsed$version
  state$feed_urls <- parsed$feeds

  feeds <- list()
  for (feed in intersect(REFERENCE_FEEDS, names(parsed$feeds))) {
    response <- poll_feed(con, state, feed, parsed$feeds[[feed]])
    if (!is.null(response$body)) {
      feeds[[feed]] <- response$body
    }
  }
  if (is.null(feeds$system_information) || is.null(feeds$station_information)) {
    stop("справочные фиды недоступны", call. = FALSE)
  }
  result <- apply_reference(con, state$system_id, state$version, feeds, now)
  state$vehicle_types <- result$vehicle_types
  state$reference_at <- now
  log_msg(state$system_id, ": справочники обновлены, станций ", result$stations)
}

# Один цикл по системе: при необходимости обновляет справочники, затем снимок состояния.
ingest_system <- function(con, state, now = Sys.time()) {
  if (difftime(now, state$reference_at, units = "secs") > REFERENCE_REFRESH_S ||
        is.null(state$feed_urls)) {
    refresh_reference(con, state, now)
  }
  url <- state$feed_urls[["station_status"]]
  if (is.null(url) || is.na(url)) {
    stop("в фиде нет station_status", call. = FALSE)
  }
  started_at <- Sys.time()
  response <- fetch_json(url, state$etags[[url]])
  if (!is.null(response$etag)) {
    state$etags[[url]] <- response$etag
  }
  if (!is.null(response$error)) {
    db_log_run(con, state$system_id, "station_status", started_at, "error", response$status,
               error = response$error)
    stop("station_status: ", response$error, call. = FALSE)
  }
  if (response$status == 304L) {
    db_log_run(con, state$system_id, "station_status", started_at, "not_modified", 304L)
    return(invisible(NULL))
  }
  counts <- apply_station_status(con, state$system_id, response$body, state$vehicle_types)
  db_log_run(
    con, state$system_id, "station_status", started_at, "ok", response$status,
    counts$rows_in, counts$rows_written, counts$rows_quarantined
  )
  ttl <- to_int(response$body$ttl)
  state$ttl <- if (is.na(ttl)) MIN_POLL_S else ttl
  log_msg(
    state$system_id, ": принято ", counts$rows_in, ", записано ", counts$rows_written,
    ", в карантине ", counts$rows_quarantined
  )
}

schedule_next <- function(state, now, ok) {
  if (ok) {
    state$failures <- 0L
    interval <- state$poll_interval_s
    if (is.null(interval) || is.na(interval)) {
      interval <- min(max(state$ttl %||% MIN_POLL_S, MIN_POLL_S), MAX_POLL_S)
    }
  } else {
    state$failures <- state$failures + 1L
    interval <- min(MIN_POLL_S * 2^state$failures, MAX_BACKOFF_S)
  }
  state$next_due <- now + interval
}

poll_due_systems <- function(con, states, now = Sys.time()) {
  settings <- db_enabled_systems(con)
  for (i in seq_len(nrow(settings))) {
    setting <- settings[i, ]
    id <- setting$system_id
    if (is.null(states[[id]])) {
      states[[id]] <- new_system_state(setting)
    }
    state <- states[[id]]
    state$poll_interval_s <- setting$poll_interval_s
    if (now < state$next_due) {
      next
    }
    ok <- tryCatch({
      ingest_system(con, state, now)
      TRUE
    }, error = function(e) {
      log_msg(id, ": ошибка: ", conditionMessage(e))
      FALSE
    })
    schedule_next(state, Sys.time(), ok)
  }
  states
}
