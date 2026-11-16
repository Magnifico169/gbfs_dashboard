source("insights.R")
anchor <- as.POSIXct("2026-11-10 00:00:00", tz = "UTC")
rows <- data.frame(bucket_start = anchor - seq_len(24L * 10L) * 3600,
                   bucket_end = anchor - (seq_len(24L * 10L) - 1L) * 3600,
                   observed_seconds = 3600, bikes_seconds = 18000)
result <- profile_and_forecast(rows, 10, anchor, "America/New_York")
stopifnot(length(result$points) == 6L, result$history_days >= 7L,
          all(vapply(result$points, function(p) p$value == 0.5, logical(1L))))
short <- profile_and_forecast(rows[1:24, ], 10, anchor, "UTC")
stopifnot(length(short$points) == 0L)
unknown <- profile_and_forecast(rows, NA_real_, anchor, "UTC")
stopifnot(length(unknown$profile) == 0L, length(unknown$points) == 0L)
stale <- profile_and_forecast(rows, 10, anchor + 7200, "UTC")
stopifnot(length(stale$points) == 0L)
rows$observed_seconds <- 0
stopifnot(length(profile_and_forecast(rows, 10, anchor, "UTC")$points) == 0L)
cat("R insights tests passed\n")
