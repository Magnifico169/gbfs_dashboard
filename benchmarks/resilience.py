"""Отключение TCP-доступа только к временному контейнеру gbfs-m2-test-pg."""

import json
import shutil
import subprocess
import time

import httpx

CONTAINER = "gbfs-m2-test-pg"
BASE_URL = "http://127.0.0.1:58000"
DOCKER = shutil.which("docker")
if DOCKER is None:
    raise RuntimeError("docker not found")


def require(condition):
    if not condition:
        raise RuntimeError("Resilience check failed")


def docker(*args):
    return subprocess.run(  # noqa: S603 — аргументы заданы внутри тестового сценария
        [DOCKER, *args], check=True, capture_output=True, text=True
    ).stdout


def sql(query):
    return docker("exec", CONTAINER, "psql", "-U", "gbfs", "-d", "gbfs", "-c", query)


config = json.loads(docker("inspect", CONTAINER))[0]
require(config["HostConfig"]["PortBindings"]["5432/tcp"][0]["HostPort"] == "55439")
with httpx.Client(base_url=BASE_URL, timeout=10) as client:
    path = "/api/v1/systems/m2_benchmark/stations?limit=1"
    baseline = client.get(path).status_code
    try:
        docker(
            "exec",
            CONTAINER,
            "sh",
            "-c",
            'cp "$PGDATA/pg_hba.conf" "$PGDATA/pg_hba.m2-backup"; '
            '{ printf "host all all 0.0.0.0/0 reject\\n"; '
            'cat "$PGDATA/pg_hba.m2-backup"; } > "$PGDATA/pg_hba.conf"',
        )
        sql("select pg_reload_conf()")
        sql("select pg_terminate_backend(pid) from pg_stat_activity where client_addr is not null")
        time.sleep(4)
        unavailable = client.get(path)
        ready = client.get("/readyz").status_code
        live = client.get("/livez").status_code
        require(unavailable.status_code == 503 and unavailable.headers["Retry-After"] == "3")
        require(ready == 503 and live == 200)
    finally:
        docker("exec", CONTAINER, "sh", "-c", 'cp "$PGDATA/pg_hba.m2-backup" "$PGDATA/pg_hba.conf"')
        sql("select pg_reload_conf()")
    recovered = client.get(path).status_code
    require(recovered == 200)
    print(
        json.dumps(
            {
                "baseline": baseline,
                "database_outage": unavailable.status_code,
                "readiness": ready,
                "liveness": live,
                "recovered": recovered,
            }
        )
    )
