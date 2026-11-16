/* Общая математика графиков: неизвестное не превращается в ноль. */
(function (root) {
  function average(row, field = "bikes") {
    const coverage =
      row[field === "docks" ? "docks_observed_seconds" : "observed_seconds"];
    return coverage > 0 ? row[field + "_seconds"] / coverage : null;
  }
  function bollinger(values, window = 20) {
    const mean = [],
      lower = [],
      upper = [];
    for (let i = 0; i < values.length; i++) {
      const sample = values.slice(Math.max(0, i + 1 - window), i + 1);
      if (
        sample.length < window ||
        sample.some((v) => v === null || !Number.isFinite(v))
      ) {
        mean.push(null);
        lower.push(null);
        upper.push(null);
        continue;
      }
      const center = sample.reduce((a, b) => a + b, 0) / window;
      const sd = Math.sqrt(
        sample.reduce((a, b) => a + (b - center) ** 2, 0) / window,
      );
      mean.push(center);
      lower.push(center - 2 * sd);
      upper.push(center + 2 * sd);
    }
    return { mean, lower, upper };
  }
  function points(rows, field = "bikes") {
    const time = [],
      values = [];
    let previousEnd = null;
    for (const row of rows) {
      const start = Date.parse(row.bucket_start) / 1000;
      if (previousEnd !== null && start > previousEnd) {
        time.push(previousEnd);
        values.push(null);
      }
      time.push(start);
      values.push(average(row, field));
      previousEnd = Date.parse(row.bucket_end) / 1000;
    }
    return [time, values];
  }
  function alignPrevious(rows, times, shift) {
    let index = 0;
    return times.map((time) => {
      const original = time - shift;
      while (
        index < rows.length &&
        Date.parse(rows[index].bucket_end) / 1000 <= original
      )
        index++;
      const row = rows[index];
      return row && Date.parse(row.bucket_start) / 1000 <= original
        ? average(row)
        : null;
    });
  }
  const api = { average, bollinger, points, alignPrevious };
  if (typeof module !== "undefined") module.exports = api;
  else root.chartMath = api;
})(globalThis);
