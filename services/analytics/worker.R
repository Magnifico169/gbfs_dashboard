source("database.R")

interval <- as.numeric(Sys.getenv("ROLLUP_INTERVAL_S", "600"))
stale_after <- as.numeric(Sys.getenv("STALE_AFTER_S", "300"))
stopifnot(is.finite(interval), interval > 0, is.finite(stale_after), stale_after > 0)
repeat {
  tryCatch({
    connection <- connect_database()
    tryCatch(run_rollups(connection, stale_after_s = stale_after),
             finally = DBI::dbDisconnect(connection))
  }, error = function(error) message("Analytics unavailable: ", conditionMessage(error)))
  Sys.sleep(interval)
}
