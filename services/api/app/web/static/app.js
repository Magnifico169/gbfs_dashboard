"use strict";
let refreshing;
async function renew() {
  if (!refreshing) {
    const run = async () => {
      const response = await fetch("/ui/refresh", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok) {
        if ([401, 403].includes(response.status)) location.assign("/login");
        throw new Error("Не удалось обновить сеанс");
      }
      location.reload();
    };
    refreshing = (
      navigator.locks ? navigator.locks.request("gbfs-refresh", run) : run()
    ).finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}
const instances = new Map();
const mapViews = new Map();
function clean() {
  for (const [element, destroy] of instances)
    if (!element.isConnected) {
      destroy();
      instances.delete(element);
    }
}
function message(text) {
  const element = document.querySelector("#connection");
  element.hidden = !text;
  element.textContent = text;
}
function chart(element, data, series, time = true) {
  if (
    !data[0].length ||
    !data.slice(1).some((row) => row.some((v) => v !== null))
  ) {
    element.textContent = "Нет подтверждённых наблюдений для графика.";
    return;
  }
  const zone = element.dataset.timezone || "UTC";
  const date = (value) =>
    value == null
      ? "—"
      : new Date(value * 1000).toLocaleString("ru-RU", { timeZone: zone });
  const plot = new uPlot(
    {
      width: Math.max(280, element.clientWidth),
      height: 280,
      scales: { x: { time } },
      series: [
        {
          label: time ? "Время" : "Час",
          value: time ? (_, v) => date(v) : undefined,
        },
        ...series.map((s) => ({
          ...s,
          spanGaps: false,
          value: (_, v) => (v == null ? "нет данных" : v.toFixed(2)),
        })),
      ],
      axes: [
        {
          values: time ? (_, ticks) => ticks.map(date) : undefined,
          space: 140,
          size: 65,
        },
        {},
      ],
    },
    data,
    element,
  );
  const observer = new ResizeObserver(() =>
    plot.setSize({ width: Math.max(280, element.clientWidth), height: 280 }),
  );
  observer.observe(element);
  instances.set(element, () => {
    observer.disconnect();
    plot.destroy();
  });
}
function drawChart(element) {
  const payload = JSON.parse(
    document.getElementById(element.dataset.chart).textContent,
  );
  const kind = element.dataset.kind;
  if (kind === "station") {
    const [time, bikes] = chartMath.points(payload.items);
    const [, docks] = chartMath.points(payload.items, "docks");
    chart(
      element,
      [time, bikes, docks],
      [
        { label: "Транспорт", stroke: "#087f72" },
        { label: "Свободные места", stroke: "#c18538" },
      ],
    );
    return;
  }
  const report = payload.report;
  if (kind === "electric") {
    const data = report.series.map((row) => ({
      ...row,
      bikes_seconds: row.ebikes_seconds * 100,
      observed_seconds: row.bikes_with_ebikes_seconds,
    }));
    const values = chartMath.points(data);
    chart(element, values, [{ label: "Электрических, %", stroke: "#087f72" }]);
    return;
  }
  if (kind === "demand") {
    if (report.resolution === "day") {
      element.textContent =
        "Для часового спроса выберите интервал короче 42 дней.";
      return;
    }
    const hours = Array(24).fill(null);
    for (const row of report.series) {
      if (!row.observed_seconds) continue;
      const hour = Number(
        new Intl.DateTimeFormat("en-GB", {
          hour: "2-digit",
          hourCycle: "h23",
          timeZone: element.dataset.timezone,
        }).format(new Date(row.bucket_start)),
      );
      hours[hour] = (hours[hour] ?? 0) + row.bikes_removed;
    }
    chart(
      element,
      [Array.from({ length: 24 }, (_, i) => i), hours],
      [{ label: "Выбытия (нижняя оценка)", stroke: "#087f72" }],
      false,
    );
    return;
  }
  if (kind === "report") {
    let [time, values] = chartMath.points(report.series);
    let bands = chartMath.bollinger(values);
    let data = [time, values, bands.mean, bands.lower, bands.upper];
    const series = [
      { label: "Доступно", stroke: "#087f72" },
      { label: "Среднее 20", stroke: "#a86d27" },
      { label: "Нижняя полоса", stroke: "#a7b3ad", dash: [5, 4] },
      { label: "Верхняя полоса", stroke: "#a7b3ad", dash: [5, 4] },
    ];
    if (payload.previous) {
      const shift =
        (Date.parse(report.start) - Date.parse(payload.previous.start)) / 1000;
      // Сравниваем средние покрывающих корзин на общей относительной шкале.
      // Несовпадающие границы периода не должны разрывать обе линии.
      if (!time.length) {
        [time] = chartMath.points(payload.previous.series);
        time = time.map((t) => t + shift);
        data = [time, ...Array.from({ length: 4 }, () => time.map(() => null))];
      }
      data.push(chartMath.alignPrevious(payload.previous.series, time, shift));
      series.push({
        label: "Предыдущий период",
        stroke: "#6f75a9",
        dash: [6, 3],
      });
    }
    chart(element, data, series);
    return;
  }
  if (kind === "profile") {
    const colors = [
      "#087f72",
      "#547f9c",
      "#b98435",
      "#a8615c",
      "#7873a6",
      "#578953",
      "#777777",
    ];
    const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
    const values = Array.from({ length: 7 }, (_, day) =>
      Array.from({ length: 24 }, (_, hour) => {
        const rows = report.profile.filter(
          (p) => p.weekday === day + 1 && p.hour === hour,
        );
        const weight = rows.reduce((sum, r) => sum + r.observed_seconds, 0);
        return weight
          ? (100 *
              rows.reduce(
                (sum, r) => sum + r.occupancy * r.observed_seconds,
                0,
              )) /
              weight
          : null;
      }),
    );
    chart(
      element,
      [Array.from({ length: 24 }, (_, i) => i), ...values],
      labels.map((label, i) => ({ label, stroke: colors[i] })),
      false,
    );
    return;
  }
  if (kind === "forecast") {
    const points = (report.forecast?.points || []).filter(
      (p) => Date.parse(p.time) > Date.parse(report.end),
    );
    chart(
      element,
      [
        points.map((p) => Date.parse(p.time) / 1000),
        ...["value", "lower", "upper"].map((key) =>
          points.map((p) => p[key] * 100),
        ),
      ],
      [
        { label: "Прогноз, %", stroke: "#087f72" },
        { label: "Нижняя, %", stroke: "#a7b3ad" },
        { label: "Верхняя, %", stroke: "#a7b3ad" },
      ],
    );
  }
}
function drawMap(element) {
  const rows = JSON.parse(
    document.getElementById(element.dataset.map).textContent,
  );
  const map = L.map(element).setView([40.73, -73.98], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const bounds = [];
  for (const row of rows) {
    if (!Number.isFinite(row.lat) || !Number.isFinite(row.lon)) continue;
    bounds.push([row.lat, row.lon]);
    const link = document.createElement("a");
    link.textContent = row.name + " · " + (row.issue || "Доступна");
    link.href =
      "/ui/operator/station?" +
      new URLSearchParams({
        system_id: element.dataset.system,
        station_id: row.station_id,
      });
    L.circleMarker([row.lat, row.lon], {
      radius: 5,
      weight: 1,
      fillOpacity: 0.85,
      color:
        row.quality !== "fresh" ? "#858d8b" : row.issue ? "#b77927" : "#087f72",
    })
      .addTo(map)
      .bindPopup(link);
  }
  const view = mapViews.get(element.dataset.viewKey);
  if (view) map.setView(view.center, view.zoom);
  else if (bounds.length)
    map.fitBounds(bounds, { padding: [20, 20], maxZoom: 15 });
  instances.set(element, () => {
    mapViews.set(element.dataset.viewKey, {
      center: map.getCenter(),
      zoom: map.getZoom(),
    });
    map.remove();
  });
}
function initialize() {
  clean();
  if (document.querySelector("[data-session-expired]"))
    renew().catch((error) => message(error.message));
  for (const element of document.querySelectorAll(".chart, .station-map")) {
    if (element.dataset.initialized) continue;
    element.dataset.initialized = "true";
    try {
      element.classList.contains("chart")
        ? drawChart(element)
        : drawMap(element);
    } catch {
      element.textContent =
        "Визуализация недоступна. Данные доступны в таблице и CSV.";
    }
  }
}
let live,
  retry,
  polling,
  attempts = 0,
  streamSystem,
  lastUpdate;
function connect(system) {
  live?.close();
  clearTimeout(retry);
  streamSystem = system;
  live = new EventSource(
    "/ui/stream?" + new URLSearchParams({ system_id: system }),
  );
  const update = () => {
    attempts = 0;
    lastUpdate = Date.now();
    message("");
    document.body.dispatchEvent(new Event("refresh-board"));
  };
  live.addEventListener("snapshot", update);
  // HTML берётся из того же ограниченного кэша; частые дельты объединяются.
  live.addEventListener("delta", () => {
    if (!lastUpdate || Date.now() - lastUpdate >= 3000) update();
  });
  live.addEventListener("auth_expired", () => {
    live.close();
    renew().catch((e) => message(e.message));
  });
  const reconnect = () => {
    live.close();
    clearTimeout(retry);
    message(
      "Нет связи с потоком. Используется опрос каждые 30 секунд; последнее обновление: " +
        (lastUpdate
          ? new Date(lastUpdate).toLocaleTimeString("ru-RU")
          : "ещё не получено"),
    );
    retry = setTimeout(
      () => connect(system),
      Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) *
        (0.8 + Math.random() * 0.4),
    );
  };
  live.addEventListener("reset", reconnect);
  live.addEventListener("unavailable", reconnect);
  live.onerror = reconnect;
}
document.addEventListener("DOMContentLoaded", () => {
  initialize();
  const board = document.querySelector("[data-stream-system]");
  if (board) {
    connect(board.dataset.streamSystem);
    polling = setInterval(
      () => document.body.dispatchEvent(new Event("refresh-board")),
      30000,
    );
  }
});
document.body.addEventListener("htmx:afterSwap", () => {
  initialize();
  const system = document.querySelector(
    "#station-filter select[name=system_id]",
  )?.value;
  if (system && system !== streamSystem) connect(system);
});
document.body.addEventListener("htmx:beforeSwap", (event) => {
  if (event.detail.xhr.status >= 400) {
    event.detail.shouldSwap = false;
    if (event.detail.xhr.status === 401) {
      renew().catch((e) => message(e.message));
      return;
    }
    const error = document.querySelector("#request-error");
    error.hidden = false;
    error.textContent = `Не удалось обновить данные (HTTP ${event.detail.xhr.status}). Последние значения сохранены. request_id: ${event.detail.xhr.getResponseHeader("X-Request-ID") || "не получен"}.`;
    if ([403, 404].includes(event.detail.xhr.status)) location.reload();
  } else if (event.detail.xhr.status >= 200)
    document.querySelector("#request-error").hidden = true;
});
document.body.addEventListener("htmx:sendError", () =>
  message(
    "Сеть недоступна. Показаны последние полученные данные; повторный опрос через 30 секунд.",
  ),
);
document.addEventListener("change", (event) => {
  if (
    event.target.matches("[data-preset]") &&
    event.target.value !== "custom"
  ) {
    const form = event.target.form;
    form.elements.start.value = "";
    form.elements.end.value = "";
    form.elements.start.required = false;
    form.elements.end.required = false;
    form.requestSubmit();
  }
});
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-reload]")) location.reload();
  if (event.target.closest("[data-print]")) window.print();
  const button = event.target.closest("[data-sort]");
  if (!button) return;
  const cell = button.closest("th"),
    table = button.closest("table"),
    index = cell.cellIndex;
  const direction = cell.getAttribute("aria-sort") === "descending" ? 1 : -1;
  for (const th of table.querySelectorAll("th"))
    th.removeAttribute("aria-sort");
  cell.setAttribute("aria-sort", direction === 1 ? "ascending" : "descending");
  const rows = [...table.tBodies[0].rows];
  rows.sort((a, b) => {
    const x = a.cells[index],
      y = b.cells[index];
    if (!x || !y) return 0;
    if (button.dataset.sort === "number") {
      if (!x.dataset.value) return y.dataset.value ? 1 : 0;
      if (!y.dataset.value) return -1;
      return direction * (Number(x.dataset.value) - Number(y.dataset.value));
    }
    return direction * x.textContent.localeCompare(y.textContent, "ru");
  });
  table.tBodies[0].append(...rows);
});
window.addEventListener("pagehide", () => {
  live?.close();
  clearTimeout(retry);
  clearInterval(polling);
});

document.body.addEventListener("htmx:configRequest", (event) => {
  if (event.detail.elt.id === "operator-board") {
    event.detail.parameters.page =
      new URL(location.href).searchParams.get("page") || "1";
  }
});
