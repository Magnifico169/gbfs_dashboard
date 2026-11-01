for (path in sort(list.files("../../R", pattern = "\\.R$", full.names = TRUE))) {
  source(path, encoding = "UTF-8")
}

fixtures_dir <- function() {
  Sys.getenv("FIXTURES_DIR", normalizePath("../../../../fixtures", mustWork = FALSE))
}

read_fixture <- function(set, name) {
  path <- file.path(fixtures_dir(), set, paste0(name, ".json"))
  jsonlite::fromJSON(path, simplifyVector = FALSE)
}
