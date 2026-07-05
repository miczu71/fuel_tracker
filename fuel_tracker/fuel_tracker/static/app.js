/* Fuel Tracker — logika UI. Wszystkie fetch-e względne (ingress-safe). */
window.FT = (function () {
  "use strict";

  const css = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const fmt = (v, digits = 2) =>
    v === null || v === undefined ? "–" :
      Number(v).toLocaleString("pl-PL", {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      });

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
    return r.json();
  }

  async function sendJSON(url, method, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function baseChartOpts() {
    Chart.defaults.font.family =
      'system-ui, -apple-system, "Segoe UI", sans-serif';
    Chart.defaults.color = css("--muted");
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { grid: { display: false }, border: { color: css("--baseline") } },
        y: {
          grid: { color: css("--grid") },
          border: { display: false },
          beginAtZero: false,
        },
      },
      plugins: { legend: { display: false } },
    };
  }

  function lineChart(canvasId, labels, data, colorVar, label) {
    const opts = baseChartOpts();
    new Chart(document.getElementById(canvasId), {
      type: "line",
      data: {
        labels,
        datasets: [{
          label,
          data,
          borderColor: css(colorVar),
          backgroundColor: css(colorVar),
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.25,
        }],
      },
      options: opts,
    });
  }

  // ── Pulpit ──────────────────────────────────────────────────────────────
  async function initDashboard() {
    const s = await getJSON("api/summary");
    const set = (id, v, d) => (document.getElementById(id).textContent = fmt(v, d));
    set("s-avg", s.avg_consumption); set("s-last", s.last_consumption);
    set("s-costkm", s.cost_per_km); set("s-total", s.total_cost, 0);
    set("s-exp", s.expenses_total, 0);
    set("s-price", s.last_fillup ? s.last_fillup.price_per_l : null);

    if (s.monthly_budget) {
      const card = document.getElementById("budget-card");
      card.hidden = false;
      document.getElementById("b-month").textContent = s.month;
      document.getElementById("b-spent").textContent = fmt(s.month_fuel_cost, 0);
      document.getElementById("b-total").textContent = fmt(s.monthly_budget, 0);
      const pct = Math.min(100, 100 * s.month_fuel_cost / s.monthly_budget);
      const fill = document.getElementById("b-fill");
      fill.style.width = pct + "%";
      if (s.month_fuel_cost > s.monthly_budget) fill.classList.add("over");
      document.getElementById("b-left").textContent =
        s.budget_left_month >= 0
          ? `Zostało ${fmt(s.budget_left_month, 0)} PLN`
          : `Przekroczono o ${fmt(-s.budget_left_month, 0)} PLN`;
    }

    lineChart("chart-consumption",
      s.consumption_series.map((p) => p.date.slice(0, 10)),
      s.consumption_series.map((p) => p.value),
      "--series-1", "L/100km");

    lineChart("chart-price",
      s.price_series.map((p) => p.date.slice(0, 10)),
      s.price_series.map((p) => p.value),
      "--series-2", "PLN/L");

    const opts = baseChartOpts();
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    opts.scales.y.beginAtZero = true;
    opts.plugins.legend = { display: true, position: "bottom" };
    new Chart(document.getElementById("chart-monthly"), {
      type: "bar",
      data: {
        labels: s.monthly.map((m) => m.month),
        datasets: [
          {
            label: "Paliwo", data: s.monthly.map((m) => m.fuel),
            backgroundColor: css("--series-1"),
            borderColor: css("--surface"), borderWidth: 1,
            borderRadius: 3,
          },
          {
            label: "Wydatki", data: s.monthly.map((m) => m.expenses),
            backgroundColor: css("--series-3"),
            borderColor: css("--surface"), borderWidth: 1,
            borderRadius: 3,
          },
        ],
      },
      options: opts,
    });
  }

  // ── Tankowania ──────────────────────────────────────────────────────────
  async function initFillups(base) {
    const rows = await getJSON("api/fillups");
    const tbody = document.querySelector("#fillups-table tbody");
    tbody.innerHTML = "";
    for (const f of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${f.date}${f.draft ? ' <span class="muted">(szkic)</span>' : ""}</td>
        <td class="num">${fmt(f.odometer, 0)}</td>
        <td class="num">${fmt(f.volume_l)}</td>
        <td class="num">${fmt(f.price_per_l)}</td>
        <td class="num">${fmt(f.total_cost)}</td>
        <td class="num">${f.consumption ? fmt(f.consumption) : "–"}</td>
        <td>${f.full_tank ? "✔" : "–"}${f.missed_previous ? " ⚠" : ""}</td>
        <td>${f.station || ""}</td>
        <td>
          <a class="btn" href="${base}/fillup-form?id=${f.id}">Edytuj</a>
          <button class="btn danger" data-del="${f.id}">Usuń</button>
        </td>`;
      tbody.appendChild(tr);
    }
    tbody.addEventListener("click", async (e) => {
      const id = e.target.dataset && e.target.dataset.del;
      if (!id) return;
      if (!confirm("Usunąć ten wpis?")) return;
      await fetch(`api/fillups/${id}`, { method: "DELETE" });
      initFillups(base);
    });
  }

  // ── Formularz tankowania ────────────────────────────────────────────────
  async function initFillupForm(base) {
    const form = document.getElementById("fillup-form");
    const editId = form.dataset.editId;
    const err = document.getElementById("form-error");

    // Dowolne 2 z 3 pól wyliczają trzecie; ostatnio edytowane pola mają priorytet.
    const V = form.volume_l, P = form.price_per_l, T = form.total_cost;
    let lastEdited = [];
    const touch = (name) => {
      lastEdited = [name, ...lastEdited.filter((n) => n !== name)].slice(0, 2);
      const v = parseFloat(V.value), p = parseFloat(P.value), t = parseFloat(T.value);
      const has = (x) => !Number.isNaN(x) && x > 0;
      if (lastEdited.length < 2) return;
      const set = new Set(lastEdited);
      if (set.has("volume_l") && set.has("price_per_l") && has(v) && has(p))
        T.value = (v * p).toFixed(2);
      else if (set.has("volume_l") && set.has("total_cost") && has(v) && has(t))
        P.value = (t / v).toFixed(2);
      else if (set.has("price_per_l") && set.has("total_cost") && has(p) && has(t))
        V.value = (t / p).toFixed(2);
    };
    for (const el of [V, P, T])
      el.addEventListener("input", () => touch(el.name));

    if (editId) {
      document.getElementById("form-title").textContent = "Edycja tankowania";
      const f = await getJSON(`api/fillups/${editId}`);
      form.date.value = f.date.replace(" ", "T");
      form.odometer.value = f.odometer;
      V.value = f.volume_l; P.value = f.price_per_l; T.value = f.total_cost;
      form.full_tank.checked = !!f.full_tank;
      form.missed_previous.checked = !!f.missed_previous;
      form.station.value = f.station || "";
      form.notes.value = f.notes || "";
    } else {
      const pre = await getJSON("api/prefill");
      form.date.value = pre.date;
      if (pre.odometer) {
        form.odometer.value = pre.odometer;
        document.getElementById("odo-hint").textContent = "(z myskoda)";
      }
      if (pre.station) form.station.value = pre.station;
      if (pre.price_per_l) P.value = pre.price_per_l;
    }

    const stations = await getJSON("api/fillups");
    const seen = new Set();
    const dl = document.getElementById("stations");
    for (const f of stations) {
      if (f.station && !seen.has(f.station)) {
        seen.add(f.station);
        const o = document.createElement("option");
        o.value = f.station;
        dl.appendChild(o);
      }
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      const body = {
        date: form.date.value,
        odometer: form.odometer.value,
        volume_l: V.value, price_per_l: P.value, total_cost: T.value,
        full_tank: form.full_tank.checked,
        missed_previous: form.missed_previous.checked,
        station: form.station.value, notes: form.notes.value,
      };
      try {
        if (editId) await sendJSON(`api/fillups/${editId}`, "PUT", body);
        else await sendJSON("api/fillups", "POST", body);
        window.location.href = `${base}/fillups`;
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      }
    });
  }

  // ── Wydatki ─────────────────────────────────────────────────────────────
  async function initExpenses() {
    const [cats, rows] = await Promise.all([
      getJSON("api/categories"), getJSON("api/expenses"),
    ]);
    const sel = document.getElementById("category-select");
    sel.innerHTML = cats.map((c) => `<option value="${c.id}">${c.name}</option>`).join("");

    const totals = {};
    for (const e of rows)
      totals[e.category || "Inne"] = (totals[e.category || "Inne"] || 0) + e.cost;
    document.getElementById("category-totals").innerHTML =
      Object.entries(totals).sort((a, b) => b[1] - a[1])
        .map(([n, v]) => `<span class="chip">${n}: <b>${fmt(v, 0)} PLN</b></span>`)
        .join("") || '<span class="muted">Brak wydatków</span>';

    const tbody = document.querySelector("#expenses-table tbody");
    tbody.innerHTML = rows.map((e) => `
      <tr>
        <td>${e.date}</td><td>${e.category || ""}</td>
        <td>${e.description || ""}</td>
        <td class="num">${fmt(e.cost)}</td>
        <td><button class="btn danger" data-del="${e.id}">Usuń</button></td>
      </tr>`).join("");
    tbody.addEventListener("click", async (ev) => {
      const id = ev.target.dataset && ev.target.dataset.del;
      if (!id || !confirm("Usunąć wydatek?")) return;
      await fetch(`api/expenses/${id}`, { method: "DELETE" });
      initExpenses();
    });

    const form = document.getElementById("expense-form");
    form.date.value = new Date().toISOString().slice(0, 16);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const errEl = document.getElementById("expense-error");
      errEl.hidden = true;
      try {
        await sendJSON("api/expenses", "POST", {
          date: form.date.value, cost: form.cost.value,
          odometer: form.odometer.value,
          category_id: form.category_id.value,
          description: form.description.value,
        });
        form.reset();
        form.date.value = new Date().toISOString().slice(0, 16);
        initExpenses();
      } catch (ex) {
        errEl.textContent = ex.message;
        errEl.hidden = false;
      }
    });
  }

  // ── Ustawienia / import ─────────────────────────────────────────────────
  function initSettings() {
    const csvForm = document.getElementById("csv-form");
    csvForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const rep = document.getElementById("csv-report");
      rep.hidden = false;
      rep.textContent = "Importuję…";
      const fd = new FormData(csvForm);
      const r = await fetch("api/import/csv", { method: "POST", body: fd });
      rep.textContent = JSON.stringify(await r.json(), null, 2);
    });

    document.getElementById("drivvo-btn").addEventListener("click", async () => {
      const rep = document.getElementById("drivvo-report");
      rep.hidden = false;
      rep.textContent = "Importuję z Drivvo…";
      const r = await fetch("api/import/drivvo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          include_refuellings: document.getElementById("drivvo-refuel").checked,
        }),
      });
      rep.textContent = JSON.stringify(await r.json(), null, 2);
    });

    document.getElementById("verify-btn").addEventListener("click", async () => {
      const out = document.getElementById("verify-result");
      out.innerHTML = '<span class="muted">Sprawdzam…</span>';
      const v = await getJSON("api/verify");
      const names = { count: "Liczba tankowań", cost: "Suma PLN", volume: "Suma litrów" };
      out.innerHTML = `
        <div class="table-wrap"><table class="table">
          <thead><tr><th></th><th class="num">Add-on</th>
          <th class="num">Drivvo (HA)</th><th>Zgodność</th></tr></thead>
          <tbody>${Object.entries(v.checks).map(([k, c]) => `
            <tr><td>${names[k]}</td>
            <td class="num">${fmt(c.local)}</td>
            <td class="num">${c.drivvo === null ? "brak" : fmt(c.drivvo)}</td>
            <td class="${c.match ? "verify-ok" : "verify-bad"}">
              ${c.match ? "✔ OK" : "✘ różnica"}</td></tr>`).join("")}
          </tbody></table></div>
        <p class="${v.all_match ? "verify-ok" : "verify-bad"}">
          ${v.all_match
            ? "✔ Wszystko się zgadza — można przepiąć sensory."
            : "✘ Sumy się różnią — nie przepinaj sensorów, sprawdź import."}</p>`;
    });
  }

  return { initDashboard, initFillups, initFillupForm, initExpenses, initSettings };
})();
