source("database.R")

test_database <- function() {
  admin <- DBI::dbConnect(
    RPostgres::Postgres(), host = Sys.getenv("PGHOST"),
    port = as.integer(Sys.getenv("PGPORT")), dbname = Sys.getenv("PGDATABASE"),
    user = Sys.getenv("PGUSER"), password = Sys.getenv("PGPASSWORD"), timezone = "UTC"
  )
  connection <- connect_database()
  system_id <- paste0("r_test_", Sys.getpid())
  on.exit({
    for (table in c("mart.station_rollup", "mart.rollup_cursor", "core.station_event",
                    "core.station_status")) {
      DBI::dbExecute(admin, paste("delete from", table, "where system_id = $1"),
                     params = list(system_id))
    }
    DBI::dbDisconnect(connection)
    DBI::dbDisconnect(admin)
  })
  t0 <- as.POSIXct("2026-11-01 00:00:00", tz = "UTC")
  facts <- data.frame(
    system_id = system_id, station_id = "s1", fetched_at = t0 + c(0, 300),
    reported_at = t0 + c(0, 300), observed_until = t0 + c(300, 600),
    bikes_available = c(10L, 0L), docks_available = c(0L, 10L),
    is_installed = TRUE, is_renting = TRUE, is_returning = TRUE
  )
  DBI::dbAppendTable(admin, DBI::Id(schema = "core", table = "station_status"), facts)
  end <- process_chunk(connection, system_id, "UTC", t0 + 600, 0)
  stopifnot(end == t0 + 600)
  values <- DBI::dbGetQuery(connection, paste(
    "select resolution, bikes_seconds, observed_seconds, empty_seconds from mart.station_rollup",
    "where system_id = $1 order by resolution"
  ), params = list(system_id))
  stopifnot(nrow(values) == 3L, all(values$bikes_seconds == 3000),
            all(values$observed_seconds == 600), all(values$empty_seconds == 300))
  process_chunk(connection, system_id, "UTC", t0 + 600, 0)
  repeated <- DBI::dbGetQuery(connection, paste(
    "select resolution, bikes_seconds, observed_seconds, empty_seconds from mart.station_rollup",
    "where system_id = $1 order by resolution"
  ), params = list(system_id))
  stopifnot(identical(values, repeated))
  cat("R database integration passed\n")
}
test_database()
