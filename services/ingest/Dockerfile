FROM r-base:4.5.1

# Пакеты R ставим из apt: бинарные сборки CRAN (Posit PM) недоступны стабильно,
# а сборка RPostgres и lintr из исходников занимает слишком много времени.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        r-cran-httr2 r-cran-jsonlite r-cran-dbi r-cran-rpostgres r-cran-testthat \
        r-cran-withr r-cran-lintr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY R/ R/
COPY run.R .

CMD ["Rscript", "run.R"]
