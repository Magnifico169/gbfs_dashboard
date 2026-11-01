# HTTP-слой: таймаут, повторы с экспоненциальным backoff, условные запросы по ETag.

USER_AGENT <- "gbfs-dashboard-ingest/0.1"
TRANSIENT_STATUSES <- c(429L, 500L, 502L, 503L, 504L)

# Возвращает list(status, body, etag, error). status — HTTP-код (NA при сетевой ошибке),
# body — разобранный JSON (NULL для 304 и ошибок).
fetch_json <- function(url, etag = NULL, timeout_s = 15, max_tries = 3L) {
  request <- httr2::request(url) |>
    httr2::req_user_agent(USER_AGENT) |>
    httr2::req_timeout(timeout_s) |>
    httr2::req_retry(
      max_tries = max_tries,
      is_transient = function(resp) httr2::resp_status(resp) %in% TRANSIENT_STATUSES,
      backoff = function(attempt) 2^attempt + stats::runif(1L)
    ) |>
    httr2::req_error(is_error = function(resp) FALSE)
  if (!is.null(etag)) {
    request <- httr2::req_headers(request, `If-None-Match` = etag)
  }

  result <- list(status = NA_integer_, body = NULL, etag = etag, error = NULL)
  tryCatch({
    response <- httr2::req_perform(request)
    result$status <- httr2::resp_status(response)
    if (result$status == 200L) {
      result$body <- jsonlite::fromJSON(httr2::resp_body_string(response), simplifyVector = FALSE)
      result$etag <- httr2::resp_header(response, "ETag")
    } else if (result$status != 304L) {
      result$error <- paste("HTTP", result$status)
    }
  }, error = function(e) {
    result$error <<- conditionMessage(e)
  })
  result
}
