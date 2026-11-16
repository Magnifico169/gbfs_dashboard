import csv
import io
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from test_m3 import PASSWORD, SYSTEM
from test_m3 import auth_env as auth_env

from app.models import LoadForecast, StationInsight, StationRollup
from app.repositories.reports import ReportRepository
from app.services.access import Access
from app.services.reporting import metrics, parse_period, segments


def ui_login(client, name):
    return client.post("/login", data={"username": name, "password": PASSWORD, "system_id": SYSTEM})


def test_operator_pages_zone_and_errors(auth_env):
    client, _, _, _, _ = auth_env
    assert client.get("/").status_code == 200
    assert client.get("/ui/operator").status_code == 401
    response = ui_login(client, "north")
    assert response.status_code == 200, response.text
    assert "s2" not in response.text
    assert "HttpOnly" in str(client.cookies) or client.cookies.get("gbfs_access")
    assert client.get("/static/app.js").status_code == 200
    station = client.get("/ui/operator/station", params={"station_id": "s1"})
    assert station.status_code == 200, station.text
    assert "Ближайшие с транспортом" in station.text
    forbidden = client.get("/ui/operator/station", params={"station_id": "s2"})
    assert forbidden.status_code == 404
    assert forbidden.headers["x-request-id"] in forbidden.text
    assert client.get("/ui/manager").status_code == 403
    partial = client.get("/ui/operator?q=does-not-exist", headers={"HX-Request": "true"})
    assert "По запросу станции не найдены" in partial.text
    assert "<!doctype" not in partial.text
    assert client.post("/ui/refresh", headers={"Origin": "https://evil.example"}).status_code == 403
    old = client.cookies.get("gbfs_refresh")
    refreshed = client.post("/ui/refresh")
    assert refreshed.status_code == 204
    assert client.cookies.get("gbfs_refresh") != old
    assert all(
        "HttpOnly" in header and "SameSite=strict" in header
        for header in refreshed.headers.get_list("set-cookie")
    )
    client.post("/ui/logout")
    assert not client.cookies.get("gbfs_access")
    assert client.get("/ui/operator").status_code == 401


def test_manager_pages_empty_report_and_role_boundaries(auth_env):
    client, _, _, _, now = auth_env
    response = ui_login(client, "manager")
    assert response.status_code == 200, response.text
    assert "Недостаточно данных" not in response.text  # сеть предлагает выбор станции
    assert client.get("/ui/operator").status_code == 403
    assert client.get("/ui/stream", params={"system_id": SYSTEM}).status_code == 403
    params = {
        "station_id": "s1",
        "start": (now - timedelta(days=1)).isoformat(),
        "end": now.isoformat(),
        "compare": "true",
    }
    result = client.get("/ui/manager", params=params)
    assert result.status_code == 200, result.text
    assert "Недостаточно данных" in result.text
    assert "нижняя оценка" in result.text
    assert client.get("/ui/manager", params={**params, "station_id": "s2"}).status_code == 404
    assert client.get("/ui/manager", params={**params, "start": "not-a-date"}).status_code == 422
    exported = client.get("/ui/report.csv", params=params)
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
    assert "lower_bound" in rows[1]
    assert "estimated" in rows[2]


def test_reports_edges_weighting_rls_and_insights(auth_env):
    client, database, _, _, now = auth_env
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    with database.transaction() as session:
        for station_id in ("s1", "s2"):
            for level, seconds in (("10m", 600), ("hour", 3600), ("day", 86400)):
                for offset in range(0, 86400, seconds):
                    session.add(
                        StationRollup(
                            system_id=SYSTEM,
                            station_id=station_id,
                            resolution=level,
                            bucket_start=start + timedelta(seconds=offset),
                            bucket_end=start + timedelta(seconds=offset + seconds),
                            observed_seconds=seconds,
                            bikes_seconds=5 * seconds,
                            docks_seconds=3 * seconds,
                            docks_observed_seconds=seconds / 2,
                            empty_seconds=seconds / 10,
                            full_seconds=0,
                            samples=1,
                            bikes_added=seconds / 60,
                            bikes_removed=0,
                        )
                    )
            session.add(
                StationInsight(
                    system_id=SYSTEM,
                    station_id=station_id,
                    as_of=now,
                    quality="fresh",
                    occupancy=0.5,
                    empty_seconds=0,
                    full_seconds=0,
                    profile=[],
                )
            )
            session.add(
                LoadForecast(
                    system_id=SYSTEM, station_id=station_id, anchor=now, history_days=0, points=[]
                )
            )
    access = Access("manager", SYSTEM, ("r1",))
    with database.session() as session:
        access.apply(session)
        report = ReportRepository(session).report(
            SYSTEM, start + timedelta(seconds=120), start + timedelta(hours=23, minutes=58)
        )
        assert [row["station_id"] for row in report["ranking"]] == ["s1"]
        assert report["total"]["observed_seconds"] == pytest.approx(86160)
        assert report["total"]["bikes_mean"] == pytest.approx(5)
        assert report["total"]["docks_mean"] == pytest.approx(6)
        assert report["estimated"]
        assert [row["station_id"] for row in report["insights"]] == ["s1"]
        assert len(list(session.scalars(select(LoadForecast)))) == 1
    with database.session() as session:
        access.apply(session)
        with pytest.raises(ProgrammingError):
            session.execute(text("select * from core.station_current"))
    ui_login(client, "manager")
    page = client.get(
        "/ui/manager",
        params={
            "start": start.isoformat(),
            "end": (start + timedelta(days=1)).isoformat(),
            "station_id": "s1",
        },
    )
    assert page.status_code == 200, page.text
    assert "5.0" in page.text


def test_dst_and_nonoverlapping_segments():
    left = datetime(2026, 11, 1, 4, tzinfo=UTC)
    right = datetime(2026, 11, 2, 5, tzinfo=UTC)
    assert segments(left, right, "day", "America/New_York") == [("day", left, right)]
    parts = segments(
        left + timedelta(minutes=3), right - timedelta(minutes=3), "day", "America/New_York"
    )
    assert parts[0][1] == left + timedelta(minutes=3)
    assert parts[-1][2] == right - timedelta(minutes=3)
    assert all(a[2] == b[1] for a, b in pairwise(parts))
    assert sum((end - start).total_seconds() for _, start, end in parts) == 25 * 3600 - 360
    with pytest.raises(ValueError, match="DST"):
        parse_period("2026-11-01T01:30", "2026-11-01T03:00", "America/New_York")
    assert (
        metrics(
            {
                "observed_seconds": 0,
                "docks_observed_seconds": 0,
                "bikes_seconds": 0,
                "docks_seconds": 0,
            }
        )["bikes_mean"]
        is None
    )


def test_expired_browser_token_and_api_stays_bearer_only(auth_env):
    client, _, _, _, now = auth_env
    ui_login(client, "manager")
    response = client.get(
        f"/api/v1/systems/{SYSTEM}/report",
        params={"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()},
    )
    assert response.status_code == 401
    client.cookies.delete("gbfs_access")
    response = client.get("/ui/manager")
    assert response.status_code == 401
    assert 'data-session-expired="true"' in response.text
    assert client.post("/ui/refresh").status_code == 204
    assert client.get("/ui/manager").status_code == 200


def test_html_failure_keeps_request_id(auth_env):
    client, _, _, _, _ = auth_env
    ui_login(client, "north")
    invalid = client.get("/ui/operator?page=-1")
    assert invalid.status_code == 422
    assert invalid.headers["x-request-id"] in invalid.text
    restored = client.get(
        "/ui/operator", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert "<!DOCTYPE html>" in restored.text or "<!doctype html>" in restored.text
