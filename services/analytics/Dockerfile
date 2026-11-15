FROM rocker/r-ver:4.5.1
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e 'install.packages(c("DBI", "RPostgres", "jsonlite"), repos="https://cloud.r-project.org")'
WORKDIR /app
COPY services/analytics/ .
CMD ["Rscript", "worker.R"]
