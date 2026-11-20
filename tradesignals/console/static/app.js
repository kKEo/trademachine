// Console live link. One EventSource per tab; every named server event becomes a
// "live-<name>" DOM event on <body>, which the page's hx-trigger attributes listen for.
(function () {
  "use strict";

  const link = document.getElementById("link");
  const fmt = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const STALE_AFTER_SEC = 60;
  const lastSeen = new Map();
  let everConnected = false;

  function setLink(state, text) {
    if (!link) return;
    link.dataset.state = state;
    link.textContent = text;
  }

  function fire(name, detail) {
    document.body.dispatchEvent(new CustomEvent("live-" + name, { detail: detail, bubbles: false }));
  }

  function signedPct(v) {
    const s = (v >= 0 ? "+" : "−") + fmt.format(Math.abs(v));
    return s + "%";
  }

  function onQuote(q) {
    const li = document.querySelector('#tape li[data-symbol="' + CSS.escape(q.symbol) + '"]');
    if (!li) return;
    const price = li.querySelector('[data-field="price"]');
    const change = li.querySelector('[data-field="change"]');
    const prev = lastSeen.get(q.symbol);
    lastSeen.set(q.symbol, { price: q.price, at: Date.now() });
    li.dataset.stale = "false";
    if (price) {
      price.textContent = fmt.format(q.price);
      if (prev && prev.price !== q.price) {
        price.classList.remove("tick-up", "tick-down");
        void price.offsetWidth; // restart the animation
        price.classList.add(q.price > prev.price ? "tick-up" : "tick-down");
      }
    }
    if (change) {
      change.textContent = signedPct(q.change_pct);
      change.classList.toggle("up", q.change_pct > 0);
      change.classList.toggle("down", q.change_pct < 0);
    }
  }

  function markStale() {
    const now = Date.now();
    lastSeen.forEach(function (v, symbol) {
      const li = document.querySelector('#tape li[data-symbol="' + CSS.escape(symbol) + '"]');
      if (li) li.dataset.stale = now - v.at > STALE_AFTER_SEC * 1000 ? "true" : "false";
    });
  }

  function connect() {
    const es = new EventSource("/events");
    es.addEventListener("hello", function () {
      setLink("live", "Live");
      if (everConnected) fire("refresh"); // we may have missed events while disconnected
      everConnected = true;
    });
    es.addEventListener("quote", function (e) {
      const q = JSON.parse(e.data);
      onQuote(q);
      fire("quote", q);
    });
    es.addEventListener("bar", function (e) {
      fire("bar", JSON.parse(e.data));
    });
    ["signal", "order", "snapshot", "status", "audit"].forEach(function (name) {
      es.addEventListener(name, function () {
        fire(name);
      });
    });
    es.addEventListener("bye", function () {
      es.close();
      setLink("lost", "Engine stopped");
      fire("status");
    });
    es.onerror = function () {
      if (es.readyState !== EventSource.CLOSED) setLink("lost", "Reconnecting");
      else setLink("lost", "Engine stopped");
    };
  }

  // Actions: any <form data-action="/url"> posts its fields as JSON and reports the answer.
  const notice = document.getElementById("notice");
  let noticeTimer = null;

  function say(text, tone) {
    if (!notice) return;
    notice.textContent = text;
    notice.dataset.tone = tone;
    notice.hidden = false;
    clearTimeout(noticeTimer);
    if (tone === "ok") noticeTimer = setTimeout(function () { notice.hidden = true; }, 8000);
  }

  document.body.addEventListener("submit", async function (e) {
    const form = e.target;
    if (!form.dataset || !form.dataset.action) return;
    e.preventDefault();
    const button = e.submitter || form.querySelector('button[type="submit"]');
    const url = (button && button.dataset.action) || form.dataset.action;
    const keep = !!(button && "keep" in button.dataset); // a check, not an action: keep what was typed
    if (button) button.disabled = true; // a second click must not send a second order
    const data = {};
    const fd = new FormData(form);
    fd.forEach(function (v, k) {
      const all = fd.getAll(k);
      data[k] = all.length > 1 ? all : v;
    });
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(data),
      });
      let body = {};
      try { body = await r.json(); } catch (err) { /* no body */ }
      if (r.ok) {
        say(body.message || "Done.", body.allowed === false ? "bad" : "ok");
        if (keep) return;
        if (form.dataset.then === "reload") { window.location.reload(); return; }
        form.reset();
        const details = form.closest("details");
        if (details) details.open = false;
        fire("refresh");
        fire("audit");
      } else {
        say(body.error || body.detail || "The console refused the request (" + r.status + ").", "bad");
      }
    } catch (err) {
      say("The console did not answer. The engine may have stopped.", "bad");
    } finally {
      if (button) button.disabled = false;
    }
  });

  // Keep the mode stripe in step with the status partial after it is swapped.
  document.body.addEventListener("htmx:afterSwap", function (e) {
    const state = e.target.querySelector ? e.target.querySelector(".state") : null;
    if (state) {
      document.body.dataset.mode = state.dataset.mode;
      document.body.dataset.halted = state.dataset.halted;
    }
  });

  setInterval(markStale, 5000);
  connect();
})();
