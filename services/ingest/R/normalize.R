# Нормализация фидов GBFS v1.1 / v2.x / v3.0 в единую модель.
#
# Что меняется между версиями: в v3 локализуемые строки становятся массивом {text, language},
# метки времени — строкой RFC 3339 вместо unix-секунд, а num_bikes_available/num_bikes_disabled
# в station_status переименованы в num_vehicles_available/num_vehicles_disabled. Типы значений
# разбираются по факту, имена полей — по списку синонимов.

epoch_sentinel <- as.POSIXct("2000-01-01", tz = "UTC")

field <- function(x, name, default = NA) {
  value <- x[[name]]
  if (is.null(value)) default else value
}

# v2: строка; v3: список {text, language}. Берём английский, иначе первый вариант.
localized_text <- function(x) {
  if (is.null(x) || length(x) == 0L) {
    return(NA_character_)
  }
  if (is.character(x)) {
    return(x[[1L]])
  }
  languages <- vapply(x, function(item) as.character(field(item, "language", "")), character(1L))
  texts <- vapply(x, function(item) as.character(field(item, "text", NA_character_)), character(1L))
  texts[[if (any(languages == "en")) which(languages == "en")[[1L]] else 1L]]
}

to_int <- function(x) {
  if (is.null(x) || length(x) == 0L) {
    return(NA_integer_)
  }
  suppressWarnings(as.integer(x[[1L]]))
}

to_num <- function(x) {
  if (is.null(x) || length(x) == 0L) {
    return(NA_real_)
  }
  suppressWarnings(as.numeric(x[[1L]]))
}

to_chr <- function(x) {
  if (is.null(x) || length(x) == 0L) {
    return(NA_character_)
  }
  as.character(x[[1L]])
}

# v2 отдаёт 0/1 и true/false вперемешку, v3 — только boolean.
to_flag <- function(x) {
  if (is.null(x) || length(x) == 0L) {
    return(NA)
  }
  as.logical(x[[1L]])
}

# unix-секунды (v1/v2) или строка RFC 3339 (v3) -> POSIXct UTC. Заглушки вроде 86400,
# которые отдаёт Citi Bike для станций без отчётов, превращаются в NA.
to_utc <- function(x) {
  if (is.null(x) || length(x) == 0L || is.na(x[[1L]])) {
    return(as.POSIXct(NA_real_, tz = "UTC"))
  }
  value <- x[[1L]]
  parsed <- if (is.numeric(value)) {
    as.POSIXct(value, origin = "1970-01-01", tz = "UTC")
  } else {
    text <- sub("\\.[0-9]+", "", as.character(value))
    text <- sub("Z$", "+0000", text)
    text <- sub("([+-][0-9]{2}):([0-9]{2})$", "\\1\\2", text)
    as.POSIXct(strptime(text, "%Y-%m-%dT%H:%M:%S%z", tz = "UTC"))
  }
  if (is.na(parsed) || parsed < epoch_sentinel) as.POSIXct(NA_real_, tz = "UTC") else parsed
}

# Первое присутствующее из полей-синонимов (v2-имя, затем v3-имя).
first_field <- function(x, ...) {
  for (name in c(...)) {
    if (!is.null(x[[name]])) {
      return(x[[name]])
    }
  }
  NULL
}

column <- function(items, type, fn) {
  vapply(items, fn, type, USE.NAMES = FALSE)
}

parse_discovery <- function(feed) {
  data <- feed$data
  feeds <- data$feeds
  if (is.null(feeds)) {
    language <- if ("en" %in% names(data)) "en" else names(data)[[1L]]
    feeds <- data[[language]]$feeds
  }
  list(
    version = to_chr(feed$version),
    feeds = stats::setNames(
      column(feeds, character(1L), function(f) to_chr(f$url)),
      column(feeds, character(1L), function(f) to_chr(f$name))
    )
  )
}

normalize_system_information <- function(feed, system_id) {
  data <- feed$data
  list(
    system_id = system_id,
    name = localized_text(data$name),
    operator = localized_text(data$operator),
    timezone = to_chr(data$timezone),
    language = to_chr(data$language)
  )
}

normalize_regions <- function(feed, system_id) {
  items <- feed$data$regions
  regions <- data.frame(
    system_id = rep(system_id, length(items)),
    region_id = column(items, character(1L), function(r) to_chr(r$region_id)),
    name = column(items, character(1L), function(r) localized_text(r$name))
  )
  regions[!is.na(regions$region_id) & !is.na(regions$name), , drop = FALSE]
}

normalize_vehicle_types <- function(feed, system_id) {
  items <- feed$data$vehicle_types
  vehicle_types <- data.frame(
    system_id = rep(system_id, length(items)),
    vehicle_type_id = column(items, character(1L), function(v) to_chr(v$vehicle_type_id)),
    form_factor = column(items, character(1L), function(v) to_chr(v$form_factor)),
    propulsion_type = column(items, character(1L), function(v) to_chr(v$propulsion_type)),
    name = column(items, character(1L), function(v) localized_text(v$name)),
    max_range_meters = column(items, numeric(1L), function(v) to_num(v$max_range_meters))
  )
  complete <- !is.na(vehicle_types$vehicle_type_id) &
    !is.na(vehicle_types$form_factor) &
    !is.na(vehicle_types$propulsion_type)
  vehicle_types[complete, , drop = FALSE]
}

electric_vehicle_type_ids <- function(vehicle_types) {
  vehicle_types$vehicle_type_id[vehicle_types$propulsion_type %in% c("electric_assist", "electric")]
}

normalize_station_information <- function(feed, system_id) {
  items <- feed$data$stations
  stations <- data.frame(
    system_id = rep(system_id, length(items)),
    station_id = column(items, character(1L), function(s) to_chr(s$station_id)),
    name = column(items, character(1L), function(s) localized_text(s$name)),
    short_name = column(items, character(1L), function(s) localized_text(s$short_name)),
    lat = column(items, numeric(1L), function(s) to_num(s$lat)),
    lon = column(items, numeric(1L), function(s) to_num(s$lon)),
    address = column(items, character(1L), function(s) to_chr(s$address)),
    region_id = column(items, character(1L), function(s) to_chr(s$region_id)),
    capacity = column(items, integer(1L), function(s) to_int(s$capacity))
  )
  complete <- !is.na(stations$station_id) &
    !is.na(stations$name) &
    !is.na(stations$lat) &
    !is.na(stations$lon)
  stations[complete, , drop = FALSE]
}

# Электрических машин на станции: по vehicle_types_available, если известны типы
# транспорта, иначе по устаревшему num_ebikes_available (v1.1, Citi Bike).
electric_available <- function(station, vehicle_types) {
  by_type <- station$vehicle_types_available
  if (is.null(by_type) || is.null(vehicle_types)) {
    return(to_int(station$num_ebikes_available))
  }
  electric_ids <- electric_vehicle_type_ids(vehicle_types)
  counts <- vapply(by_type, function(item) {
    if (to_chr(item$vehicle_type_id) %in% electric_ids) to_int(item$count) else 0L
  }, integer(1L))
  sum(counts)
}

normalize_station_status <- function(feed, system_id, vehicle_types = NULL) {
  items <- feed$data$stations
  data.frame(
    system_id = rep(system_id, length(items)),
    station_id = column(items, character(1L), function(s) to_chr(s$station_id)),
    reported_at = do.call(c, c(
      list(as.POSIXct(character(0L), tz = "UTC")),
      lapply(items, function(s) to_utc(s$last_reported))
    )),
    bikes_available = column(items, integer(1L), function(s) {
      to_int(first_field(s, "num_bikes_available", "num_vehicles_available"))
    }),
    ebikes_available = column(items, integer(1L), function(s) electric_available(s, vehicle_types)),
    bikes_disabled = column(items, integer(1L), function(s) {
      to_int(first_field(s, "num_bikes_disabled", "num_vehicles_disabled"))
    }),
    docks_available = column(items, integer(1L), function(s) to_int(s$num_docks_available)),
    docks_disabled = column(items, integer(1L), function(s) to_int(s$num_docks_disabled)),
    is_installed = column(items, logical(1L), function(s) to_flag(s$is_installed)),
    is_renting = column(items, logical(1L), function(s) to_flag(s$is_renting)),
    is_returning = column(items, logical(1L), function(s) to_flag(s$is_returning))
  )
}
