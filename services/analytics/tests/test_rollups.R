source("rollups.R")

t0 <- as.POSIXct("2026-11-01 00:00:00", tz = "UTC")
facts <- data.frame(
  station_id = c("s1", "s1"), fetched_at = t0 + c(0, 300),
  observed_until = t0 + c(300, 600), bikes_available = c(10L, 0L),
  docks_available = c(0L, 10L), is_installed = TRUE, is_renting = TRUE, is_returning = TRUE
)
events <- data.frame(station_id = "s1", occurred_at = t0 + 300,
                     event_type = "bikes_removed", count = 10L)
values <- calculate_rollups(facts, events, t0, t0 + 600, "UTC", 0)
ten <- values[values$resolution == "10m", ]
stopifnot(nrow(ten) == 1L, ten$observed_seconds == 600, ten$bikes_seconds == 3000,
          ten$empty_seconds == 300, ten$full_seconds == 300,
          ten$bikes_removed == 10L, ten$bikes_min == 0L, ten$bikes_max == 10L)
stopifnot(identical(values, calculate_rollups(facts, events, t0, t0 + 600, "UTC", 0)))

# Состояние не менялось, но подтверждалось каждый опрос: весь интервал известен.
steady <- facts[1L, ]
steady$observed_until <- t0 + 3600
steady$docks_available <- NA_integer_
values <- calculate_rollups(steady, events[FALSE, ], t0, t0 + 3600, "UTC", 0)
hour <- values[values$resolution == "hour", ]
stopifnot(hour$observed_seconds == 3600, hour$bikes_seconds == 36000,
          hour$docks_observed_seconds == 0, is.na(hour$docks_min))

# Потерянные наблюдения не превращаются в доступный транспорт.
facts$fetched_at <- t0 + c(0, 1800)
facts$observed_until <- facts$fetched_at
values <- calculate_rollups(facts, events[FALSE, ], t0, t0 + 3600, "UTC", 60)
stopifnot(values$observed_seconds[values$resolution == "hour"] == 120)
facts$observed_until <- as.POSIXct(NA)
values <- calculate_rollups(facts, events[FALSE, ], t0, t0 + 3600, "UTC", 60)
stopifnot(all(values$observed_seconds == 0), all(is.na(values$bikes_min)))

for (date in c("2026-03-29", "2026-10-25")) {
  start <- as.POSIXct(date, tz = "Europe/Paris")
  end <- as.POSIXct(as.character(as.Date(date) + 1L), tz = "Europe/Paris")
  expected <- if (date == "2026-03-29") 23 else 25
  bounds <- bucket_boundaries(start, end - 1, "hour", "Europe/Paris")
  stopifnot(sum(bounds >= as.numeric(start) & bounds < as.numeric(end)) == expected)
  day <- bucket_boundaries(start, end - 1, "day", "Europe/Paris")
  stopifnot(day[2L] - day[1L] == expected * 3600)
}
start <- as.POSIXct("2026-11-01", tz = "Asia/Kolkata")
bounds <- bucket_boundaries(start, start + 3600, "hour", "Asia/Kolkata")
stopifnot(bounds[1L] == as.numeric(start))
cat("R rollup tests passed\n")
