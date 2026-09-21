/* Budget view: the "% used" ring and the six-month line chart (Allocated vs Used).
   Colours come from CSS custom properties (app.css) so the chart follows the theme. Data: #budget-data (JSON). */
(function () {
  "use strict";

  var dataEl = document.getElementById("budget-data");
  if (!dataEl || typeof Chart === "undefined") return;
  var data = JSON.parse(dataEl.textContent);
  var css = App.cssVar;

  // The ring sits on the dark-teal card: light arc on a translucent white track.
  var ring = document.querySelector("[data-ring] canvas");
  if (ring) App.drawRing(ring, data.percent, { fill: "#8FD9DE", warnFill: "#FFB59A", track: "rgba(255,255,255,0.22)" });

  var canvas = document.getElementById("six-month-chart");
  if (!canvas || !data.series || !data.series.has_data) return;

  var ink = css("--ink", "#1B2B2E"), muted = css("--ink-muted", "#5D6B6E"), hairline = css("--hairline", "#E4E8E9");
  var series = [
    { label: "Budget allocated", values: data.series.allocated, color: css("--chart-allocated", "#0094A0") },
    { label: "Amount used", values: data.series.used, color: css("--chart-used", "#D9541E") }
  ];

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = muted;

  new Chart(canvas, {
    type: "line",
    data: {
      labels: data.series.labels,
      datasets: series.map(function (s) {
        return {
          label: s.label, data: s.values, borderColor: s.color, backgroundColor: s.color, borderWidth: 2,
          tension: 0, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: s.color,
          pointBorderColor: "#fff", pointBorderWidth: 2, pointHitRadius: 24, fill: false
        };
      })
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: App.reduceMotion ? false : { duration: 500 },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { display: false }, border: { color: hairline } },
        y: {
          beginAtZero: true, border: { display: false }, grid: { color: hairline, lineWidth: 1 },
          ticks: { maxTicksLimit: 5, callback: function (value) { return App.formatZAR(value); } }
        }
      },
      plugins: {
        legend: { display: false },               // the HTML legend above the chart carries identity
        tooltip: {
          backgroundColor: "#fff", titleColor: muted, bodyColor: ink, borderColor: hairline, borderWidth: 1,
          padding: 10, boxPadding: 4, usePointStyle: false,
          callbacks: {
            title: function (items) { return data.series.long_labels[items[0].dataIndex]; },
            label: function (item) { return " " + App.formatZAR(item.parsed.y) + "  " + item.dataset.label; },
            labelColor: function (item) { return { borderColor: item.dataset.borderColor, backgroundColor: item.dataset.borderColor, borderWidth: 0, borderRadius: 2 }; }
          }
        }
      }
    }
  });
})();
