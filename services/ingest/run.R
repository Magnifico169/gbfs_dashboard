#!/usr/bin/env Rscript
# Точка входа сервиса приёма. Без аргументов крутится циклом, с --once делает один проход.

for (path in sort(list.files("R", pattern = "\\.R$", full.names = TRUE))) {
  source(path, encoding = "UTF-8")
}

main <- function(args = commandArgs(trailingOnly = TRUE)) {
  con <- db_connect()
  on.exit(DBI::dbDisconnect(con))
  db_set_ingest_role(con)

  states <- list()
  repeat {
    db_ensure_partitions(con)
    states <- poll_due_systems(con, states)
    if ("--once" %in% args) {
      break
    }
    Sys.sleep(1)
  }
}

if (!interactive()) {
  main()
}
