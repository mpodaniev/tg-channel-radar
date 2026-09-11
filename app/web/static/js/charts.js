document.addEventListener("DOMContentLoaded", () => {
  if (typeof Chart === "undefined") {
    return;
  }

  const CHARTS = [
    { dataScriptId: "subs-chart-data", canvasId: "subs-chart", type: "line" },
    { dataScriptId: "daily-chart-data", canvasId: "daily-chart", type: "bar" },
    { dataScriptId: "growth-chart-data", canvasId: "growth-chart", type: "line" },
  ];

  for (const { dataScriptId, canvasId, type } of CHARTS) {
    const dataScript = document.getElementById(dataScriptId);
    const canvas = document.getElementById(canvasId);
    if (!dataScript || !canvas) {
      continue;
    }

    const payload = JSON.parse(dataScript.textContent);
    new Chart(canvas, {
      type,
      data: {
        labels: payload.labels,
        datasets: payload.series.map((series) => ({
          label: series.label,
          data: series.data,
          spanGaps: true,
        })),
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
      },
    });
  }
});
