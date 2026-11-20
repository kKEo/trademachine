// Charts for the console (TradingView Lightweight Charts v4). Colours come from the CSS tokens.
(function () {
  "use strict";

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function baseOptions() {
    return {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: token("--muted"),
        fontFamily: getComputedStyle(document.body).fontFamily,
        fontSize: 12,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: token("--line") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: true },
      crosshair: { mode: 0 },
      handleScale: { axisPressedMouseMove: false },
    };
  }

  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(url + " -> " + r.status);
    return r.json();
  }

  function showEmpty(el, text) {
    el.innerHTML = "";
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = text;
    el.appendChild(p);
  }

  async function equityChart(el) {
    let chart = null;
    let series = null;
    async function load() {
      const rows = await getJSON("/api/equity");
      if (rows.length < 2) {
        if (!chart) showEmpty(el, "The equity line appears after the first two account snapshots.");
        return;
      }
      if (!chart) {
        el.innerHTML = "";
        chart = LightweightCharts.createChart(el, baseOptions());
        series = chart.addAreaSeries({
          lineColor: token("--sel"),
          topColor: token("--sel") + "55",
          bottomColor: token("--sel") + "00",
          lineWidth: 2,
          priceLineVisible: false,
        });
        series.setData(rows);
        chart.timeScale().fitContent();
      } else {
        series.setData(rows);
      }
    }
    document.body.addEventListener("live-snapshot", load);
    document.body.addEventListener("live-refresh", load);
    await load();
  }

  // ------------------------------------------------------------------ price chart
  const LINES = { sma10: "--sel", sma30: "--warn", ema20: "--alt" };
  const num = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function markerFor(m) {
    if (m.type === "signal") {
      if (m.kind === "long") return { time: m.time, position: "belowBar", shape: "arrowUp", color: token("--up"), text: "long" };
      if (m.kind === "short") return { time: m.time, position: "aboveBar", shape: "arrowDown", color: token("--down"), text: "short" };
      return { time: m.time, position: "aboveBar", shape: "square", color: token("--muted"), text: "flat" };
    }
    const buy = m.kind === "buy";
    return { time: m.time, position: buy ? "belowBar" : "aboveBar", shape: "circle", color: token(buy ? "--up" : "--down"), text: m.text };
  }

  async function priceChart(el) {
    const symbol = el.dataset.symbol;
    const timeframe = el.dataset.timeframe;
    const rsiEl = document.getElementById("rsi-chart");
    const legend = document.getElementById("legend");
    const note = document.getElementById("chart-note");
    const layers = {};
    document.querySelectorAll("[data-layer]").forEach(function (box) {
      layers[box.dataset.layer] = box.checked;
      box.addEventListener("change", function () {
        layers[box.dataset.layer] = box.checked;
        applyLayers();
      });
    });

    let chart = null, candles = null, rsiChart = null, rsiSeries = null;
    const lines = {};
    let data = null;
    let limit = 500;
    let seconds = 60;
    let loading = false;
    let timer = null;

    function build() {
      el.innerHTML = "";
      chart = LightweightCharts.createChart(el, baseOptions());
      candles = chart.addCandlestickSeries({
        upColor: token("--up"), downColor: token("--down"),
        wickUpColor: token("--up"), wickDownColor: token("--down"),
        borderVisible: false,
      });
      Object.keys(LINES).forEach(function (key) {
        lines[key] = chart.addLineSeries({
          color: token(LINES[key]), lineWidth: 2, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false,
        });
      });
      const opts = baseOptions();
      opts.timeScale.visible = false;
      opts.layout.attributionLogo = false; // the price pane above already carries the attribution
      rsiChart = LightweightCharts.createChart(rsiEl, opts);
      rsiSeries = rsiChart.addLineSeries({
        color: token("--sel"), lineWidth: 2, priceLineVisible: false,
        autoscaleInfoProvider: function () { return { priceRange: { minValue: 0, maxValue: 100 } }; },
      });
      [30, 70].forEach(function (level) {
        rsiSeries.createPriceLine({ price: level, color: token("--muted"), lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
      });
      // keep both panes on the same bars
      let syncing = false;
      function sync(from, to) {
        from.timeScale().subscribeVisibleLogicalRangeChange(function (range) {
          if (syncing || !range) return;
          syncing = true;
          to.timeScale().setVisibleLogicalRange(range);
          syncing = false;
          if (from === chart && range.from < 5 && data && data.bars.length >= limit && limit < 2000) {
            limit = Math.min(2000, limit + 500); // scrolled to the left edge: fetch more history
            load();
          }
        });
      }
      sync(chart, rsiChart);
      sync(rsiChart, chart);
      chart.subscribeCrosshairMove(function (p) {
        showLegend(p && p.time ? p.time : null);
      });
    }

    function showLegend(time) {
      if (!legend || !data || !data.bars.length) return;
      const bar = (time && data.byTime.get(time)) || data.bars[data.bars.length - 1];
      const parts = [["O", bar.open], ["H", bar.high], ["L", bar.low], ["C", bar.close]];
      Object.keys(LINES).forEach(function (key) {
        const v = layers[key] && data.values[key] && data.values[key].get(bar.time);
        if (v !== undefined && v !== false && v !== null) parts.push([key.replace("sma", "Avg ").replace("ema", "EMA "), v]);
      });
      legend.innerHTML = "";
      parts.forEach(function (p) {
        const span = document.createElement("span");
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = p[0];
        span.appendChild(k);
        span.appendChild(document.createTextNode(num.format(p[1])));
        legend.appendChild(span);
      });
    }

    function applyLayers() {
      if (!data || !chart) return;
      Object.keys(LINES).forEach(function (key) {
        lines[key].applyOptions({ visible: !!layers[key] });
      });
      const markers = data.markers.filter(function (m) {
        return m.type === "signal" ? layers.signals : layers.fills;
      });
      candles.setMarkers(markers.map(markerFor));
      showLegend(null);
    }

    async function load() {
      if (loading) return;
      loading = true;
      try {
        const d = await getJSON("/api/bars/" + encodeURIComponent(symbol) + "?tf=" + timeframe + "&limit=" + limit);
        if (note) {
          note.hidden = !d.note;
          note.textContent = d.note || "";
        }
        if (!d.bars.length) {
          if (!chart) showEmpty(el, "No bars for " + symbol + " on " + timeframe + " yet. They appear as the feed delivers them.");
          return;
        }
        const first = !chart;
        if (first) build();
        seconds = d.seconds;
        d.byTime = new Map(d.bars.map(function (b) { return [b.time, b]; }));
        d.values = {};
        Object.keys(d.indicators).forEach(function (key) {
          d.values[key] = new Map(d.indicators[key].map(function (p) { return [p.time, p.value]; }));
        });
        data = d;
        candles.setData(d.bars);
        Object.keys(LINES).forEach(function (key) {
          lines[key].setData(d.indicators[key] || []);
        });
        // whitespace points keep the RSI pane on the same bar index as the price pane
        const rsi = d.values.rsi14 || new Map();
        rsiSeries.setData(d.bars.map(function (b) {
          return rsi.has(b.time) ? { time: b.time, value: rsi.get(b.time) } : { time: b.time };
        }));
        applyLayers();
        if (first) {
          const n = d.bars.length;
          const focus = Number(el.dataset.focus || 0);
          let at = -1;
          if (focus) {
            at = d.bars.findIndex(function (b) { return b.time + d.seconds > focus; });
            if (at < 0 || d.bars[0].time > focus) {
              at = -1;
              if (limit < 2000) {
                limit = 2000; // the signal is older than the first page of bars
                loading = false;
                chart.remove(); rsiChart.remove();
                chart = null;
                return load();
              }
            }
          }
          if (at >= 0) chart.timeScale().setVisibleLogicalRange({ from: at - 70, to: at + 50 });
          else chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 4 });
        }
      } finally {
        loading = false;
      }
    }

    function reloadSoon() {
      clearTimeout(timer);
      timer = setTimeout(function () { load().catch(console.error); }, 400);
    }

    document.body.addEventListener("live-quote", function (e) {
      const q = e.detail;
      if (!q || q.symbol !== symbol || !data || !candles) return;
      const t = Math.floor(q.ts / seconds) * seconds;
      const last = data.bars[data.bars.length - 1];
      if (t < last.time) return;
      let bar;
      if (t === last.time) {
        bar = { time: t, open: last.open, high: Math.max(last.high, q.price), low: Math.min(last.low, q.price), close: q.price };
        data.bars[data.bars.length - 1] = bar;
      } else {
        bar = { time: t, open: q.price, high: q.price, low: q.price, close: q.price };
        data.bars.push(bar);
      }
      data.byTime.set(t, bar);
      candles.update(bar);
    });
    document.body.addEventListener("live-bar", function (e) {
      if (e.detail && e.detail.symbol === symbol) reloadSoon();
    });
    ["live-signal", "live-order", "live-refresh"].forEach(function (name) {
      document.body.addEventListener(name, reloadSoon);
    });
    await load();
  }

  window.consoleCharts = { token: token, baseOptions: baseOptions, getJSON: getJSON, showEmpty: showEmpty };

  document.querySelectorAll('[data-chart="price"]').forEach(function (el) {
    priceChart(el).catch(function (err) {
      console.error(err);
      showEmpty(el, "The price chart could not be loaded. Reload the page to try again.");
    });
  });

  document.querySelectorAll('[data-chart="equity"]').forEach(function (el) {
    equityChart(el).catch(function (err) {
      console.error(err);
      showEmpty(el, "The equity chart could not be loaded. Reload the page to try again.");
    });
  });
})();
