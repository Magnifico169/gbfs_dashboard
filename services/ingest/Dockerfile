FROM r-base:4.5.1

# Бинарные пакеты Posit PM нестабильно доступны, поэтому пакеты R ставим из apt.
# RPostgres в Debian нет: его зависимости берём из apt, сам пакет собираем из CRAN.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        r-base-dev libpq-dev \
        r-cran-httr2 r-cran-jsonlite r-cran-dbi r-cran-testthat r-cran-withr r-cran-lintr \
        r-cran-bit64 r-cran-blob r-cran-hms r-cran-lubridate r-cran-plogr r-cran-cpp11 \
    && Rscript -e 'install.packages("RPostgres", repos = "https://cloud.r-project.org", dependencies = FALSE)' \
    && Rscript -e 'library(RPostgres)' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY R/ R/
COPY run.R .

CMD ["Rscript", "run.R"]
