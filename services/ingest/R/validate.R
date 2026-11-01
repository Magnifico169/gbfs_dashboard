# Валидация снимков station_status (ADR-0002, п. 5). Возвращает причину отбраковки для каждой
# строки или NA, если строка прошла. Отбракованные строки уходят в core.station_quarantine
# и не попадают в факты и агрегаты.

REQUIRED_STATUS_COLUMNS <- c("bikes_available", "is_installed", "is_renting", "is_returning")
COUNTER_COLUMNS <- c(
  "bikes_available", "ebikes_available", "bikes_disabled", "docks_available", "docks_disabled"
)
FUTURE_TOLERANCE_S <- 300

zero_if_na <- function(x) {
  x[is.na(x)] <- 0L
  x
}

validate_station_status <- function(status, capacity = NULL, now = Sys.time()) {
  reason <- rep(NA_character_, nrow(status))
  fill <- function(condition, text) {
    hit <- is.na(reason) & !is.na(condition) & condition
    reason[hit] <<- text[hit]
  }

  fill(is.na(status$station_id) | status$station_id == "", rep("missing_station_id", nrow(status)))
  for (name in REQUIRED_STATUS_COLUMNS) {
    fill(is.na(status[[name]]), rep(paste0("missing_field:", name), nrow(status)))
  }
  fill(duplicated(status$station_id), rep("duplicate_station", nrow(status)))

  negative <- Reduce(`|`, lapply(status[COUNTER_COLUMNS], function(x) !is.na(x) & x < 0L))
  fill(negative, rep("negative_counter", nrow(status)))

  if (!is.null(capacity)) {
    station_capacity <- unname(capacity[status$station_id])
    over_bikes <- status$bikes_available + zero_if_na(status$bikes_disabled)
    over_docks <- zero_if_na(status$docks_available) + zero_if_na(status$docks_disabled)
    fill(
      station_capacity > 0L & (over_bikes > station_capacity | over_docks > station_capacity),
      rep("exceeds_capacity", nrow(status))
    )
  }

  fill(status$ebikes_available > status$bikes_available, rep("ebikes_exceed_bikes", nrow(status)))
  fill(
    status$reported_at > now + FUTURE_TOLERANCE_S,
    rep("reported_in_future", nrow(status))
  )
  reason
}
