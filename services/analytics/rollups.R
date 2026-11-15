# Чистый расчёт по интервалам подтверждённого состояния, без доступа к БД.
utc_time <- function(value) as.POSIXct(value, origin = "1970-01-01", tz = "UTC")

local_midnight <- function(value, zone) {
  as.POSIXct(format(value, tz = zone, format = "%Y-%m-%d"), tz = zone)
}

bucket_boundaries <- function(start, end, resolution, zone) {
  if (!zone %in% OlsonNames()) stop("Unknown system timezone: ", zone)
  first_day <- as.Date(format(start, tz = zone, format = "%Y-%m-%d"))
  last_day <- as.Date(format(end, tz = zone, format = "%Y-%m-%d")) + 1
  midnights <- as.POSIXct(as.character(seq(first_day, last_day, by = "day")), tz = zone)
  if (resolution == "day") return(as.numeric(midnights))
  step <- if (resolution == "10m") 600 else 3600
  # Отдельная сетка на каждый местный день: переходы DST дают 23/25 часов.
  result <- unlist(lapply(seq_len(length(midnights) - 1L), function(i) {
    seq(as.numeric(midnights[i]), as.numeric(midnights[i + 1L]), by = step)
  }))
  sort(unique(c(result, as.numeric(tail(midnights, 1L)))))
}

calculate_rollups <- function(facts, events, start, end, zone, stale_after_s = 300) {
  output <- list()
  position <- 0L
  station_ids <- union(as.character(facts$station_id), as.character(events$station_id))
  for (station_id in station_ids) {
    rows <- facts[facts$station_id == station_id, , drop = FALSE]
    rows <- rows[order(rows$fetched_at), , drop = FALSE]
    movement <- events[events$station_id == station_id, , drop = FALSE]
    begins <- as.numeric(rows$fetched_at)
    following <- c(begins[-1L], as.numeric(end))
    if (length(begins) == 0L) following <- numeric()
    finishes <- pmin(following, as.numeric(rows$observed_until) + stale_after_s,
                     as.numeric(end), na.rm = FALSE)
    for (resolution in c("10m", "hour", "day")) {
      boundaries <- bucket_boundaries(start, end, resolution, zone)
      for (i in seq_len(length(boundaries) - 1L)) {
        left <- boundaries[i]
        right <- boundaries[i + 1L]
        if (left >= as.numeric(end) || right <= as.numeric(start)) next
        seconds <- pmax(0, pmin(finishes, right) - pmax(begins, left, as.numeric(start)))
        seconds[is.na(seconds)] <- 0
        valid <- seconds > 0
        dock_valid <- valid & !is.na(rows$docks_available)
        selected_events <- movement[as.numeric(movement$occurred_at) >= left &
                                      as.numeric(movement$occurred_at) <
                                        min(right, as.numeric(end)),
                                    , drop = FALSE]
        samples <- sum(begins >= left & begins < min(right, as.numeric(end)))
        if (!any(valid) && samples == 0L && nrow(selected_events) == 0L) next
        added <- selected_events$count[selected_events$event_type == "bikes_added"]
        removed <- selected_events$count[selected_events$event_type == "bikes_removed"]
        position <- position + 1L
        output[[position]] <- data.frame(
          station_id = station_id, resolution = resolution,
          bucket_start = utc_time(left), bucket_end = utc_time(right),
          observed_seconds = sum(seconds),
          bikes_seconds = sum(rows$bikes_available * seconds, na.rm = TRUE),
          docks_seconds = sum(rows$docks_available * seconds, na.rm = TRUE),
          docks_observed_seconds = sum(seconds[dock_valid]),
          empty_seconds = sum(seconds[valid & rows$bikes_available == 0 &
                                        rows$is_installed & rows$is_renting]),
          full_seconds = sum(seconds[dock_valid & !is.na(rows$docks_available) &
                                       rows$docks_available == 0 & rows$is_installed &
                                       rows$is_returning], na.rm = TRUE),
          bikes_min = if (any(valid)) min(rows$bikes_available[valid]) else NA_integer_,
          bikes_max = if (any(valid)) max(rows$bikes_available[valid]) else NA_integer_,
          docks_min = if (any(dock_valid)) min(rows$docks_available[dock_valid]) else NA_integer_,
          docks_max = if (any(dock_valid)) max(rows$docks_available[dock_valid]) else NA_integer_,
          samples = as.integer(samples),
          bikes_added = as.integer(sum(added, na.rm = TRUE)),
          bikes_removed = as.integer(sum(removed, na.rm = TRUE))
        )
      }
    }
  }
  if (length(output) == 0L) return(data.frame())
  do.call(rbind, output)
}
