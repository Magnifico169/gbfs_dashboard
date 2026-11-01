now <- as.POSIXct("2026-11-01 22:00:00", tz = "UTC")

make_status <- function(...) {
  base <- data.frame(
    system_id = "x", station_id = "s1", reported_at = now - 60,
    bikes_available = 5L, ebikes_available = 2L, bikes_disabled = 1L,
    docks_available = 10L, docks_disabled = 0L,
    is_installed = TRUE, is_renting = TRUE, is_returning = TRUE
  )
  overrides <- list(...)
  for (name in names(overrides)) {
    base[[name]] <- overrides[[name]]
  }
  base
}

test_that("корректная строка проходит", {
  expect_true(is.na(validate_station_status(make_status(), c(s1 = 20L), now)))
})

test_that("отрицательный счётчик отбраковывается", {
  expect_equal(validate_station_status(make_status(docks_available = -1L), NULL, now), "negative_counter")
})

test_that("превышение вместимости отбраковывается", {
  status <- make_status(bikes_available = 15L, bikes_disabled = 10L)
  expect_equal(validate_station_status(status, c(s1 = 20L), now), "exceeds_capacity")
  expect_true(is.na(validate_station_status(status, c(s1 = 30L), now)))
  expect_true(is.na(validate_station_status(status, NULL, now)))
})

test_that("неизвестная вместимость не мешает", {
  expect_true(is.na(validate_station_status(make_status(), c(other = 5L), now)))
})

test_that("last_reported из будущего отбраковывается, небольшое опережение допустимо", {
  expect_equal(
    validate_station_status(make_status(reported_at = now + 3600), NULL, now),
    "reported_in_future"
  )
  expect_true(is.na(validate_station_status(make_status(reported_at = now + 60), NULL, now)))
})

test_that("NA в отчёте допустим: станция просто не отчитывалась", {
  status <- make_status(reported_at = as.POSIXct(NA_real_, tz = "UTC"))
  expect_true(is.na(validate_station_status(status, NULL, now)))
})

test_that("пропущенное обязательное поле и дубликаты", {
  expect_equal(
    validate_station_status(make_status(is_renting = NA), NULL, now),
    "missing_field:is_renting"
  )
  duplicated_rows <- rbind(make_status(), make_status())
  expect_equal(validate_station_status(duplicated_rows, NULL, now), c(NA, "duplicate_station"))
})

test_that("электрических не может быть больше, чем всех", {
  expect_equal(
    validate_station_status(make_status(ebikes_available = 9L), NULL, now),
    "ebikes_exceed_bikes"
  )
})
