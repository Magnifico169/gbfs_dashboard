const test = require("node:test");
const assert = require("node:assert/strict");
const {
  average,
  bollinger,
  points,
} = require("../app/web/static/chart-math.js");
test("weighted means distinguish unknown coverage from zero", () => {
  assert.equal(average({ observed_seconds: 0, bikes_seconds: 0 }), null);
  assert.equal(average({ observed_seconds: 600, bikes_seconds: 0 }), 0);
  assert.equal(
    average({ docks_observed_seconds: 300, docks_seconds: 1800 }, "docks"),
    6,
  );
});
test("Bollinger resets across missing observations", () => {
  const result = bollinger([1, 1, 1, null, 2, 2, 2], 3);
  assert.deepEqual(result.mean, [null, null, 1, null, null, null, 2]);
  assert.equal(result.upper[2], 1);
});
test("unobserved time creates a graph gap", () => {
  const result = points([
    {
      bucket_start: "2026-01-01T00:00Z",
      bucket_end: "2026-01-01T00:10Z",
      observed_seconds: 600,
      bikes_seconds: 3000,
    },
    {
      bucket_start: "2026-01-01T01:00Z",
      bucket_end: "2026-01-01T01:10Z",
      observed_seconds: 600,
      bikes_seconds: 600,
    },
  ]);
  assert.deepEqual(result[1], [5, null, 1]);
});

test("comparison aligns shifted buckets without inventing missing coverage", () => {
  const { alignPrevious } = require("../app/web/static/chart-math.js");
  const start = Date.parse("2026-01-01T00:00Z") / 1000;
  const rows = [
    {
      bucket_start: "2026-01-01T00:00Z",
      bucket_end: "2026-01-01T01:00Z",
      observed_seconds: 3600,
      bikes_seconds: 18000,
    },
    {
      bucket_start: "2026-01-01T02:00Z",
      bucket_end: "2026-01-01T03:00Z",
      observed_seconds: 0,
      bikes_seconds: 0,
    },
  ];
  assert.deepEqual(
    alignPrevious(
      rows,
      [start + 1800 + 3600, start + 5400 + 3600, start + 9000 + 3600],
      3600,
    ),
    [5, null, null],
  );
});
