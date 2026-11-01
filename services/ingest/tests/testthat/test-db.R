# Интеграционные тесты: нужна доступная PostgreSQL (переменные PG*), на каждый прогон
# создаётся и удаляется отдельная база.

admin <- NULL
test_db <- paste0("gbfs_test_", Sys.getpid())

migrations_dir <- function() {
  Sys.getenv("MIGRATIONS_DIR", normalizePath("../../../../db/migrations/versions"))
}

setup_db <- function() {
  skip_if(Sys.getenv("PGHOST") == "", "PostgreSQL не настроена (PGHOST)")
  admin <<- db_connect()
  DBI::dbExecute(admin, paste0("create database ", test_db))
  env <- as.list(Sys.getenv())
  env[["PGDATABASE"]] <- test_db
  con <- db_connect(env)
  # В тестах схема накатывается напрямую из SQL ревизий Alembic (R-образ без Python).
  for (path in sort(list.files(migrations_dir(), pattern = "\\.sql$", full.names = TRUE))) {
    DBI::dbExecute(con, paste(readLines(path, encoding = "UTF-8"), collapse = "\n"))
  }
  db_set_ingest_role(con)
  db_ensure_partitions(con)
  con
}

teardown_db <- function(con) {
  DBI::dbDisconnect(con)
  DBI::dbExecute(admin, paste0("drop database if exists ", test_db, " with (force)"))
  DBI::dbDisconnect(admin)
}

count_rows <- function(con, table) {
  DBI::dbGetQuery(con, paste("select count(*)::int as n from", table))$n
}

test_that("приём v2.3: справочники, факты, неизменные станции не пишутся повторно", {
  con <- setup_db()
  withr::defer(teardown_db(con))

  set <- "citibike_v2_3"
  feeds <- list(
    system_information = read_fixture(set, "system_information"),
    station_information = read_fixture(set, "station_information"),
    vehicle_types = read_fixture(set, "vehicle_types")
  )
  reference <- apply_reference(con, "lyft_nyc", "2.3", feeds)
  expect_equal(reference$stations, 7L)
  expect_equal(count_rows(con, "core.station"), 7L)
  expect_equal(count_rows(con, "core.vehicle_type"), 2L)

  status <- read_fixture(set, "station_status")
  first <- apply_station_status(con, "lyft_nyc", status, reference$vehicle_types)
  expect_equal(first$rows_in, 7L)
  expect_equal(first$rows_written + first$rows_quarantined, 7L)
  expect_equal(count_rows(con, "core.station_status"), first$rows_written)
  expect_equal(count_rows(con, "core.station_current"), first$rows_written)

  again <- apply_station_status(con, "lyft_nyc", status, reference$vehicle_types)
  expect_equal(again$rows_written, 0L)
  expect_equal(count_rows(con, "core.station_status"), first$rows_written)

  changed <- status
  changed$data$stations[[1L]]$num_bikes_available <- 99L
  moved <- apply_station_status(con, "lyft_nyc", changed, reference$vehicle_types)
  expect_equal(moved$rows_written + moved$rows_quarantined, 1L)
})

test_that("приём v3.0 и карантин невалидных строк", {
  con <- setup_db()
  withr::defer(teardown_db(con))

  set <- "synthetic_v3_0"
  feeds <- list(
    system_information = read_fixture(set, "system_information"),
    station_information = read_fixture(set, "station_information"),
    vehicle_types = read_fixture(set, "vehicle_types"),
    system_regions = read_fixture(set, "system_regions")
  )
  reference <- apply_reference(con, "synthetic_v3", "3.0", feeds)
  expect_equal(count_rows(con, "core.region"), 1L)

  status <- read_fixture(set, "station_status")
  status$data$stations[[2L]]$num_docks_available <- -3L
  status$data$stations[[3L]]$num_vehicles_available <- 40L
  counts <- apply_station_status(con, "synthetic_v3", status, reference$vehicle_types)
  expect_equal(counts, list(rows_in = 3L, rows_written = 1L, rows_quarantined = 2L))

  reasons <- DBI::dbGetQuery(
    con, "select station_id, reason from core.station_quarantine order by station_id"
  )
  expect_equal(reasons$reason, c("negative_counter", "exceeds_capacity"))
})

test_that("SCD2: изменение атрибутов станции открывает новую версию", {
  con <- setup_db()
  withr::defer(teardown_db(con))

  info <- read_fixture("synthetic_v3_0", "station_information")
  stations <- normalize_station_information(info, "synthetic_v3")
  db_sync_stations(con, stations, "synthetic_v3", Sys.time() - 3600)
  stations$capacity[[1L]] <- 25L
  db_sync_stations(con, stations, "synthetic_v3", Sys.time())

  versions <- DBI::dbGetQuery(
    con,
    "select capacity, valid_to is null as is_current from core.station
     where station_id = 's1' order by valid_from"
  )
  expect_equal(versions$capacity, c(20L, 25L))
  expect_equal(versions$is_current, c(FALSE, TRUE))
  expect_equal(count_rows(con, "core.station"), 4L)
})

test_that("роль app_ingest не может менять настройки систем (их пишет админка)", {
  con <- setup_db()
  withr::defer(teardown_db(con))

  expect_error(
    DBI::dbExecute(con, "insert into app.system_setting (system_id, discovery_url) values ('a', 'b')"),
    "permission denied"
  )
})
