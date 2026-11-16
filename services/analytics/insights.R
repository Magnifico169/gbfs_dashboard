# Профили и прогнозы вычисляет R; браузер получает только готовые оценки.
profile_and_forecast <- function(rows, capacity, anchor, zone) {
  rows <- rows[rows$observed_seconds > 0 & rows$bucket_end <= anchor, , drop = FALSE]
  days <- length(unique(format(rows$bucket_start, tz = zone, format = "%Y-%m-%d")))
  if (nrow(rows) == 0L || is.na(capacity) || capacity <= 0) {
    return(list(profile = list(), points = list(), history_days = days))
  }
  rows$hour <- as.integer(format(rows$bucket_start, tz = zone, format = "%H"))
  rows$weekday <- as.integer(format(rows$bucket_start, tz = zone, format = "%u"))
  rows$load <- rows$bikes_seconds / rows$observed_seconds / capacity
  groups <- split(rows, paste(rows$weekday, rows$hour, sep = ":"))
  profile <- unname(lapply(groups, function(group) {
    list(
      weekday = group$weekday[1L], hour = group$hour[1L],
      occupancy = sum(group$bikes_seconds) / sum(group$observed_seconds) / capacity,
      observed_seconds = sum(group$observed_seconds), samples = nrow(group)
    )
  }))
  points <- list()
  # Как минимум неделя и три наблюдения каждого прогнозируемого часа.
  latest <- rows[which.max(rows$bucket_end), , drop = FALSE]
  fresh <- as.numeric(difftime(anchor, latest$bucket_end, units = "secs")) <= 3600
  if (days >= 7L && fresh && latest$observed_seconds >= 1800) {
    for (step in seq_len(6L)) {
      target <- anchor + step * 3600
      hour <- as.integer(format(target, tz = zone, format = "%H"))
      sample <- rows[rows$hour == hour & rows$observed_seconds >= 1800, , drop = FALSE]
      if (nrow(sample) < 3L) break
      weights <- sample$observed_seconds
      center <- weighted.mean(sample$load, weights)
      baseline <- rows[rows$hour == latest$hour, , drop = FALSE]
      if (as.numeric(difftime(anchor, latest$bucket_end, units = "secs")) <= 3600) {
        shift <- latest$load - weighted.mean(baseline$load, baseline$observed_seconds)
        center <- center + shift / step
      }
      center <- max(0, min(1, center))
      spread <- stats::sd(sample$load)
      # Эмпирический прогнозный диапазон, не гарантия и не точный спрос.
      points[[length(points) + 1L]] <- list(
        time = format(target, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ"),
        value = max(0, min(1, center)), lower = max(0, center - 1.96 * spread),
        upper = min(1, center + 1.96 * spread)
      )
    }
  }
  list(profile = profile, points = points, history_days = days)
}

run_insights <- function(connection, system_id, zone, anchor, stale_after_s) {
  DBI::dbWithTransaction(connection, {
    locked <- DBI::dbGetQuery(
      connection,
      "select pg_try_advisory_xact_lock(hashtextextended($1, 4)) as locked",
      params = list(system_id)
    )$locked[1L]
    if (!locked) return(invisible(NULL))
    stations <- DBI::dbGetQuery(connection, paste(
      "select s.station_id, s.capacity, c.bikes_available, c.ebikes_available,",
      "c.bikes_disabled, c.docks_disabled, c.is_renting, c.reported_at, c.checked_at,",
      "(select max(q.fetched_at) from core.station_quarantine q where",
      "q.system_id = s.system_id and q.station_id = s.station_id) as rejected_at",
      "from core.station s left join core.station_current c on",
      "c.system_id = s.system_id and c.station_id = s.station_id",
      "where s.system_id = $1 and s.valid_to is null"
    ), params = list(system_id))
    DBI::dbExecute(connection, "delete from mart.station_insight where system_id = $1",
                   params = list(system_id))
    for (i in seq_len(nrow(stations))) {
      station <- stations[i, , drop = FALSE]
      rows <- DBI::dbGetQuery(connection, paste(
        "select bucket_start, bucket_end, observed_seconds, bikes_seconds,",
        "empty_seconds, full_seconds from mart.station_rollup",
        "where system_id = $1 and station_id = $2 and resolution = 'hour'",
        "and bucket_start >= $3 and bucket_end <= $4 order by bucket_start"
      ), params = list(system_id, station$station_id, anchor - 28 * 86400, anchor))
      result <- profile_and_forecast(rows, station$capacity, anchor, zone)
      age <- as.numeric(difftime(anchor, min(station$reported_at, station$checked_at),
                                 units = "secs"))
      quality <- if (is.na(age)) "unknown" else if (age > stale_after_s) "stale" else "fresh"
      if (!is.na(station$rejected_at) &&
            (is.na(station$checked_at) || station$rejected_at > station$checked_at)) {
        quality <- "invalid"
      }
      load <- if (quality == "fresh" && !is.na(station$capacity) && station$capacity > 0) {
        station$bikes_available / station$capacity
      } else {
        NA_real_
      }
      electric <- if (quality == "fresh" && !is.na(station$bikes_available) &&
                        station$bikes_available > 0) {
        station$ebikes_available / station$bikes_available
      } else {
        NA_real_
      }
      recent <- rows[rows$bucket_end > anchor - 3600, , drop = FALSE]
      disabled_values <- c(station$bikes_disabled, station$docks_disabled)
      disabled <- if (all(is.na(disabled_values))) {
        NA_integer_
      } else {
        sum(disabled_values, na.rm = TRUE)
      }
      DBI::dbExecute(connection, paste(
        "insert into mart.station_insight (system_id, station_id, as_of, quality, age_seconds,",
        "occupancy, empty_seconds, full_seconds, disabled, renting, electric_share, profile)",
        "values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)"
      ), params = list(
        system_id, station$station_id, anchor, quality, age, load,
        sum(recent$empty_seconds), sum(recent$full_seconds),
        disabled, station$is_renting, electric,
        as.character(jsonlite::toJSON(result$profile, auto_unbox = TRUE, na = "null"))
      ))
      DBI::dbExecute(connection, paste(
        "insert into mart.load_forecast (system_id, station_id, anchor, history_days, points)",
        "values ($1,$2,$3,$4,$5::jsonb) on conflict (system_id, station_id, anchor)",
        "do update set history_days = excluded.history_days, points = excluded.points,",
        "updated_at = now()"
      ), params = list(
        system_id, station$station_id, anchor, result$history_days,
        as.character(jsonlite::toJSON(result$points, auto_unbox = TRUE, na = "null"))
      ))
    }
    DBI::dbExecute(connection, paste(
      "delete from mart.load_forecast where system_id = $1 and anchor < $2"
    ), params = list(system_id, anchor - 90 * 86400))
  })
}
