# Валидация снимков station_status (ADR-0002, п. 5). Возвращает причину отбраковки для каждой
# строки или NA, если строка прошла. Отбракованные строки уходят в core.station_quarantine
# и не попадают в факты и агрегаты.

required_status_columns <- c("bikes_available", "is_installed", "is_renting", "is_returning")
counter_columns <- c(
  "bikes_available", "ebikes_available", "bikes_disabled", "docks_available", "docks_disabled"
)
future_tolerance_s <- 300

zero_if_na <- function(x) {
  x[is.na(x)] <- 0L
  x
}

apply_rule <- function(reason, condition, text) {
  hit <- is.na(reason) & !is.na(condition) & condition
  reason[hit] <- text
  reason
}

validate_station_status <- function(status, capacity = NULL, now = Sys.time()) {
  reason <- rep(NA_character_, nrow(status))

  reason <- apply_rule(
    reason, is.na(status$station_id) | status$station_id == "", "missing_station_id"
  )
  for (name in required_status_columns) {
    reason <- apply_rule(reason, is.na(status[[name]]), paste0("missing_field:", name))
  }
  reason <- apply_rule(reason, duplicated(status$station_id), "duplicate_station")

  negative <- Reduce(`|`, lapply(status[counter_columns], function(x) !is.na(x) & x < 0L))
  reason <- apply_rule(reason, negative, "negative_counter")

  if (!is.null(capacity)) {
    station_capacity <- unname(capacity[status$station_id])
    over_bikes <- status$bikes_available + zero_if_na(status$bikes_disabled)
    over_docks <- zero_if_na(status$docks_available) + zero_if_na(status$docks_disabled)
    reason <- apply_rule(
      reason,
      station_capacity > 0L & (over_bikes > station_capacity | over_docks > station_capacity),
      "exceeds_capacity"
    )
  }

  reason <- apply_rule(
    reason, status$ebikes_available > status$bikes_available, "ebikes_exceed_bikes"
  )
  apply_rule(reason, status$reported_at > now + future_tolerance_s, "reported_in_future")
}
