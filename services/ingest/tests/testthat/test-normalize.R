test_that("разбор discovery работает для v2 и v3", {
  v2 <- parse_discovery(read_fixture("citibike_v2_3", "gbfs"))
  expect_equal(v2$version, "2.3")
  expect_true(all(c("station_status", "station_information") %in% names(v2$feeds)))

  v3 <- parse_discovery(read_fixture("synthetic_v3_0", "gbfs"))
  expect_equal(v3$version, "3.0")
  expect_match(v3$feeds[["station_status"]], "station_status.json$")
})

test_that("v2.3: station_status нормализуется, заглушка last_reported даёт NA", {
  vt <- normalize_vehicle_types(read_fixture("citibike_v2_3", "vehicle_types"), "lyft_nyc")
  expect_equal(electric_vehicle_type_ids(vt), "2")

  status <- normalize_station_status(
    read_fixture("citibike_v2_3", "station_status"), "lyft_nyc", vt
  )
  expect_equal(nrow(status), 7L)
  expect_true(all(status$system_id == "lyft_nyc"))
  expect_type(status$bikes_available, "integer")
  expect_type(status$is_renting, "logical")
  expect_true(anyNA(status$reported_at))
  expect_s3_class(status$reported_at, "POSIXct")
  expect_false(all(is.na(status$reported_at)))
})

test_that("v3.0: поля num_vehicles_*, RFC 3339 и локализованные строки", {
  vt <- normalize_vehicle_types(read_fixture("synthetic_v3_0", "vehicle_types"), "synthetic_v3")
  expect_equal(vt$name, c("Classic", "E-bike"))

  status <- normalize_station_status(
    read_fixture("synthetic_v3_0", "station_status"), "synthetic_v3", vt
  )
  expect_equal(status$bikes_available, c(8L, 0L, 5L))
  expect_equal(status$ebikes_available, c(3L, 0L, 1L))
  expect_equal(status$bikes_disabled, c(1L, 0L, 0L))
  expect_equal(status$is_renting, c(TRUE, TRUE, TRUE))

  reported <- as.numeric(status$reported_at)
  expect_equal(reported[[1L]], as.numeric(as.POSIXct("2026-11-01 21:43:30", tz = "UTC")))
  # смещение +01:00 учитывается, дробные секунды отбрасываются
  expect_equal(reported[[3L]], as.numeric(as.POSIXct("2026-11-01 21:43:00", tz = "UTC")))
  expect_equal(reported[[2L]], as.numeric(as.POSIXct("2026-11-01 21:43:00", tz = "UTC")))
})

test_that("v3.0: справочники и локализованные названия", {
  info <- normalize_system_information(read_fixture("synthetic_v3_0", "system_information"), "x")
  expect_equal(info$name, "Synthetic Bikes")
  expect_equal(info$timezone, "Europe/Paris")

  stations <- normalize_station_information(
    read_fixture("synthetic_v3_0", "station_information"), "x"
  )
  expect_equal(stations$name, c("Gare", "Louvre", "Bastille"))
  expect_equal(stations$short_name, c("G1", NA, NA))
  expect_equal(stations$capacity, c(20L, 10L, 12L))

  regions <- normalize_regions(read_fixture("synthetic_v3_0", "system_regions"), "x")
  expect_equal(regions$name, "Centre")
})

test_that("без vehicle_types электрические берутся из num_ebikes_available", {
  status <- normalize_station_status(read_fixture("citibike_v2_3", "station_status"), "lyft_nyc")
  expect_true(any(status$ebikes_available > 0L, na.rm = TRUE))
})

test_that("станции без обязательных атрибутов отбрасываются из справочника", {
  feed <- list(data = list(stations = list(
    list(station_id = "a", name = "A", lat = 1, lon = 2),
    list(station_id = "b", name = "B", lat = 1)
  )))
  stations <- normalize_station_information(feed, "x")
  expect_equal(stations$station_id, "a")
})
