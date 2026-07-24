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

  // ── Przełącznik pojazdu (0.11.0) ────────────────────────────────────────
  // Auto aktualnie przeglądane żyje w query stringu (?vehicle_id=), bo nie
  // ma sesji Flask — withVehicle() dopisuje go do fetch-y DANYCH POJAZDU
  // (tankowania/wydatki/paragony/importy). NIE używać dla api/vehicles*,
  // api/settings, api/categories — te są globalne.
  function vehicleId() {
    return document.body.dataset.vehicleId || "";
  }

  function withVehicle(url) {
    const vid = vehicleId();
    if (!vid) return url;
    return url + (url.includes("?") ? "&" : "?") + "vehicle_id=" + vid;
  }

  async function initVehicleSwitcher() {
    const sw = document.getElementById("vehicle-switcher");
    if (!sw) return;
    const vid = vehicleId();
    const params = new URLSearchParams(window.location.search);
    const stored = localStorage.getItem("ftVehicleId");
    // Brak ?vehicle_id= w URL, ale localStorage pamięta inne auto niż to,
    // które serwer właśnie wyrenderował (aktywne) — dociągnij zapamiętane.
    if (!params.has("vehicle_id") && stored && stored !== vid) {
      params.set("vehicle_id", stored);
      window.location.search = params.toString();
      return;
    }
    if (vid) localStorage.setItem("ftVehicleId", vid);

    let vehicles = [];
    try {
      vehicles = (await getJSON("api/vehicles")).filter((v) => !v.archived);
    } catch (e) { return; }
    sw.innerHTML = vehicles.map((v) =>
      `<option value="${v.id}" ${String(v.id) === vid ? "selected" : ""}>` +
      `${v.name}${v.active ? " (aktywny)" : ""}</option>`).join("");

    sw.addEventListener("change", () => {
      localStorage.setItem("ftVehicleId", sw.value);
      const p = new URLSearchParams(window.location.search);
      p.set("vehicle_id", sw.value);
      window.location.search = p.toString();
    });
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
    const s = await getJSON(withVehicle("api/summary"));
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
            label: "Paliwo (karta)", data: s.monthly.map((m) => m.fuel),
            backgroundColor: css("--series-1"),
            borderColor: css("--surface"), borderWidth: 1,
            borderRadius: 3,
          },
          {
            label: "Paliwo prywatne", data: s.monthly.map((m) => m.fuel_own),
            backgroundColor: css("--series-2"),
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
    const rows = await getJSON(withVehicle("api/fillups"));
    const tbody = document.querySelector("#fillups-table tbody");
    tbody.innerHTML = "";
    for (const f of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${f.date}${f.draft ? ' <span class="muted">(szkic)</span>' : ""}</td>
        <td class="num">${fmt(f.odometer, 0)}</td>
        <td class="num">${fmt(f.volume_l)}</td>
        <td class="num">${fmt(f.price_per_l)}</td>
        <td class="num">${fmt(f.total_cost)}${f.paid_by === "own" ? ' <span class="badge own">moje</span>' : ""}${f.currency && f.currency !== "PLN" ? ` <span class="badge">${f.total_cost_orig != null ? fmt(f.total_cost_orig) + " " : ""}${f.currency}</span>` : ""}</td>
        <td class="num">${f.consumption ? fmt(f.consumption) : "–"}</td>
        <td>${f.full_tank ? "✔" : "–"}${f.missed_previous ? " ⚠" : ""}</td>
        <td>${f.station || ""}</td>
        <td>
          ${f.attachment_id ? `<a class="btn" title="Paragon" href="api/attachments/${f.attachment_id}" target="_blank">📷</a>` : ""}
          <a class="btn" href="${withVehicle(base + "/fillup-form?id=" + f.id)}">Edytuj</a>
          <button class="btn danger" data-del="${f.id}">Usuń</button>
        </td>`;
      tbody.appendChild(tr);
    }
    tbody.addEventListener("click", async (e) => {
      const id = e.target.dataset && e.target.dataset.del;
      if (!id) return;
      if (!confirm("Usunąć ten wpis?")) return;
      await fetch(withVehicle(`api/fillups/${id}`), { method: "DELETE" });
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

    // Waluta: przy != PLN pola Cena/Kwota są w walucie oryginalnej,
    // kurs NBP dociągany z api/rate (ręczna korekta możliwa).
    const rateLabel = document.getElementById("rate-label");
    const rateHint = document.getElementById("rate-hint");
    const syncCurrency = async (keepRate) => {
      const c = form.currency.value;
      document.getElementById("cur-price").textContent = `(${c})`;
      document.getElementById("cur-total").textContent = `(${c})`;
      rateLabel.hidden = c === "PLN";
      if (c === "PLN") { form.exchange_rate.value = ""; return; }
      if (keepRate && form.exchange_rate.value) return;
      rateHint.textContent = "(pobieram z NBP…)";
      try {
        const r = await getJSON(
          `api/rate?currency=${c}&date=${form.date.value.slice(0, 10)}`);
        form.exchange_rate.value = r.rate;
        rateHint.textContent = `(NBP z ${r.effective_date})`;
      } catch {
        rateHint.textContent = "(NBP niedostępne — wpisz ręcznie)";
      }
    };
    form.currency.addEventListener("change", () => syncCurrency(false));
    form.date.addEventListener("change", () => syncCurrency(false));

    // Skan paragonu: zdjęcie → api/receipts/parse (llmvision) → prefill pól.
    // Nigdy auto-zapis — użytkownik weryfikuje i klika Zapisz.
    const scanInput = document.getElementById("receipt-input");
    const scanStatus = document.getElementById("scan-status");
    const extrasBox = document.getElementById("receipt-extras");
    let attachmentId = null;
    let receiptExtras = null;
    scanInput.addEventListener("change", async () => {
      const file = scanInput.files[0];
      if (!file) return;
      scanStatus.textContent = "Analizuję paragon…";
      const fd = new FormData();
      fd.append("file", file);
      try {
        const r = await fetch(withVehicle("api/receipts/parse"),
          { method: "POST", body: fd });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
          // Zdjęcie mogło się zapisać mimo błędnej analizy — podpinamy je.
          if (data.attachment_id) attachmentId = data.attachment_id;
          throw new Error(data.error || `HTTP ${r.status}`);
        }
        attachmentId = data.attachment_id;
        const p = data.parsed;
        if (p.date) form.date.value = p.date;
        if (p.odometer) form.odometer.value = p.odometer;
        if (p.volume_l) V.value = p.volume_l;
        if (p.price_per_l) P.value = p.price_per_l;
        if (p.total_cost) T.value = p.total_cost;
        if (p.station && !form.station.value) form.station.value = p.station;
        if (p.currency && p.currency !== "PLN") {
          form.currency.value = p.currency;
          syncCurrency(false);
        }
        scanStatus.textContent = "✓ Odczytano — sprawdź pola przed zapisem";
        if (p.non_fuel_total > 0) {
          receiptExtras = p;
          extrasBox.hidden = false;
          document.getElementById("extras-label").textContent =
            `Dodaj też wydatek „Płyny": ` +
            p.non_fuel_items.map((i) => i.description).join(", ") +
            ` — ${fmt(p.non_fuel_total)} ${p.currency}`;
        } else {
          receiptExtras = null;
          extrasBox.hidden = true;
        }
      } catch (ex) {
        scanStatus.textContent = "Błąd: " + ex.message;
      } finally {
        scanInput.value = "";
      }
    });

    if (editId) {
      document.getElementById("form-title").textContent = "Edycja tankowania";
      const f = await getJSON(withVehicle(`api/fillups/${editId}`));
      form.date.value = f.date.replace(" ", "T");
      form.odometer.value = f.odometer;
      // Wpis zagraniczny edytujemy w walucie oryginalnej.
      const foreign = f.currency && f.currency !== "PLN";
      V.value = f.volume_l;
      P.value = foreign ? (f.price_per_l_orig ?? f.price_per_l) : f.price_per_l;
      T.value = foreign ? (f.total_cost_orig ?? f.total_cost) : f.total_cost;
      form.currency.value = f.currency || "PLN";
      if (foreign) form.exchange_rate.value = f.exchange_rate ?? "";
      syncCurrency(true);
      form.full_tank.checked = !!f.full_tank;
      form.missed_previous.checked = !!f.missed_previous;
      form.paid_by.checked = f.paid_by === "own";
      form.station.value = f.station || "";
      form.notes.value = f.notes || "";
      form.latitude.value = f.latitude ?? "";
      form.longitude.value = f.longitude ?? "";
    } else {
      const pre = await getJSON(withVehicle("api/prefill"));
      form.date.value = pre.date;
      if (pre.odometer) {
        form.odometer.value = pre.odometer;
        document.getElementById("odo-hint").textContent = "(z encji odometru)";
      }
      if (pre.station) form.station.value = pre.station;
      if (pre.price_per_l) P.value = pre.price_per_l;
      const gps = document.getElementById("gps-hint");
      if (pre.latitude != null) {
        form.latitude.value = pre.latitude;
        form.longitude.value = pre.longitude;
        if (pre.station_matched) {
          gps.textContent = "(dopasowana po GPS)";
        } else {
          // Brak zapisanej stacji w pobliżu — spytaj OSM o sugestie.
          gps.textContent = "(szukam stacji w pobliżu…)";
          getJSON(`api/stations/nearby?lat=${pre.latitude}&lon=${pre.longitude}`)
            .then((near) => {
              if (near.length && !form.station.value) {
                form.station.value = near[0].name;
                gps.textContent = `(z OSM, ${near[0].distance_m} m)`;
              } else if (near.length) {
                gps.textContent = `(w pobliżu: ${near.map((n) => n.name).slice(0, 3).join(", ")})`;
              } else {
                gps.textContent = "";
              }
            })
            .catch(() => { gps.textContent = ""; });
        }
      }
    }

    const stations = await getJSON("api/stations");
    const dl = document.getElementById("stations");
    for (const s of stations) {
      const o = document.createElement("option");
      o.value = s.name;
      dl.appendChild(o);
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
        paid_by: form.paid_by.checked ? "own" : "fleet_card",
        station: form.station.value, notes: form.notes.value,
        latitude: form.latitude.value, longitude: form.longitude.value,
        currency: form.currency.value,
        exchange_rate: form.exchange_rate.value,
        attachment_id: attachmentId,
      };
      try {
        if (editId) await sendJSON(withVehicle(`api/fillups/${editId}`), "PUT", body);
        else await sendJSON(withVehicle("api/fillups"), "POST", body);
        // Paragon mieszany: pozycje niepaliwowe → wydatek "Płyny"
        // (jedno zdjęcie tworzy dwa wpisy, oba zaakceptowane świadomie).
        if (receiptExtras && document.getElementById("extras-check").checked) {
          const cats = await getJSON("api/categories");
          const fluids = cats.find((c) => c.name === "Płyny");
          await sendJSON(withVehicle("api/expenses"), "POST", {
            date: form.date.value,
            odometer: form.odometer.value,
            category_id: fluids ? fluids.id : null,
            description: receiptExtras.non_fuel_items
              .map((i) => i.description).join(", "),
            cost: receiptExtras.non_fuel_total,
            attachment_id: attachmentId,
          });
        }
        window.location.href = withVehicle(`${base}/fillups`);
      } catch (ex) {
        err.textContent = ex.message;
        err.hidden = false;
      }
    });
  }

  // ── Wydatki ─────────────────────────────────────────────────────────────
  async function initExpenses() {
    const [cats, rows] = await Promise.all([
      getJSON("api/categories"), getJSON(withVehicle("api/expenses")),
    ]);
    const sel = document.getElementById("category-select");
    sel.innerHTML = cats.filter((c) => !c.hidden)
      .map((c) => `<option value="${c.id}">${c.name}</option>`).join("");
    const catByName = Object.fromEntries(cats.map((c) => [c.name, c.id]));

    const form = document.getElementById("expense-form");
    const submitBtn = document.getElementById("expense-submit");
    const cancelBtn = document.getElementById("expense-cancel");
    const tbody = document.querySelector("#expenses-table tbody");
    let editId = null;
    let byId = {};

    const resetForm = () => {
      editId = null;
      form.reset();
      form.date.value = new Date().toISOString().slice(0, 16);
      submitBtn.textContent = "Dodaj wydatek";
      cancelBtn.hidden = true;
    };

    const render = (list) => {
      byId = Object.fromEntries(list.map((e) => [String(e.id), e]));
      const totals = {};
      for (const e of list)
        totals[e.category || "Inne"] = (totals[e.category || "Inne"] || 0) + e.cost;
      document.getElementById("category-totals").innerHTML =
        Object.entries(totals).sort((a, b) => b[1] - a[1])
          .map(([n, v]) => `<span class="chip">${n}: <b>${fmt(v, 0)} PLN</b></span>`)
          .join("") || '<span class="muted">Brak wydatków</span>';
      tbody.innerHTML = list.map((e) => `
        <tr>
          <td>${e.date}</td><td>${e.category || ""}</td>
          <td>${e.description || ""}</td>
          <td class="num">${fmt(e.cost)}</td>
          <td>
            <button class="btn" data-edit="${e.id}">Edytuj</button>
            <button class="btn danger" data-del="${e.id}">Usuń</button>
          </td>
        </tr>`).join("");
    };

    const reload = async () => render(await getJSON(withVehicle("api/expenses")));
    render(rows);
    form.date.value = new Date().toISOString().slice(0, 16);

    tbody.addEventListener("click", async (ev) => {
      const ds = ev.target.dataset || {};
      if (ds.del) {
        if (!confirm("Usunąć wydatek?")) return;
        await fetch(withVehicle(`api/expenses/${ds.del}`), { method: "DELETE" });
        if (editId === ds.del) resetForm();
        reload();
      } else if (ds.edit) {
        const e = byId[ds.edit];
        if (!e) return;
        editId = ds.edit;
        form.date.value = e.date.replace(" ", "T");
        form.cost.value = e.cost;
        form.odometer.value = e.odometer ?? "";
        // Kategoria ukryta nie jest w select — dołóż ją tymczasowo.
        if (e.category && !catByName[e.category]) catByName[e.category] = e.category_id;
        if (e.category_id && !sel.querySelector(`option[value="${e.category_id}"]`))
          sel.insertAdjacentHTML("beforeend",
            `<option value="${e.category_id}">${e.category}</option>`);
        form.category_id.value = e.category_id ?? "";
        form.description.value = e.description || "";
        submitBtn.textContent = "Zapisz zmiany";
        cancelBtn.hidden = false;
        form.scrollIntoView({ behavior: "smooth" });
      }
    });

    cancelBtn.addEventListener("click", resetForm);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const errEl = document.getElementById("expense-error");
      errEl.hidden = true;
      const body = {
        date: form.date.value, cost: form.cost.value,
        odometer: form.odometer.value,
        category_id: form.category_id.value,
        description: form.description.value,
      };
      try {
        if (editId) await sendJSON(withVehicle(`api/expenses/${editId}`), "PUT", body);
        else await sendJSON(withVehicle("api/expenses"), "POST", body);
        resetForm();
        reload();
      } catch (ex) {
        errEl.textContent = ex.message;
        errEl.hidden = false;
      }
    });
  }

  // ── Mapa tankowań ───────────────────────────────────────────────────────
  async function initMap() {
    const data = (await getJSON(withVehicle("api/map-data")))
      .filter((s) => s.latitude != null && s.longitude != null);
    const el = document.getElementById("map");
    if (!data.length) {
      el.innerHTML = '<p class="muted" style="padding:16px">Brak stacji ze ' +
        "współrzędnymi — dodaj tankowanie z telefonu (GPS) albo uzupełnij " +
        "pozycję w formularzu.</p>";
      return;
    }
    const map = L.map(el);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    const maxVisits = Math.max(...data.map((s) => s.visits), 1);
    const bounds = [];
    for (const s of data) {
      // Zagranica > prywatne > flota (stacja może mieć różne wpisy).
      const color = s.foreign_cnt > 0 ? css("--series-3")
        : s.own_paid > 0 ? css("--series-2") : css("--series-1");
      const radius = 8 + 12 * Math.sqrt(s.visits / maxVisits);
      L.circleMarker([s.latitude, s.longitude], {
        radius, color, fillColor: color, fillOpacity: 0.55, weight: 2,
      }).addTo(map).bindPopup(`
        <b>${s.name}</b>${s.brand ? ` <span>(${s.brand})</span>` : ""}<br>
        Wizyty: <b>${s.visits}</b><br>
        Wydano: <b>${fmt(s.total_cost, 0)} PLN</b><br>
        Śr. cena: <b>${fmt(s.avg_price)} PLN/L</b><br>
        Ostatnio: ${s.last_date ? s.last_date.slice(0, 10) : "–"}
        ${s.own_paid ? `<br>Prywatne tankowania: ${s.own_paid}` : ""}
        ${s.country !== "PL" ? `<br>Kraj: ${s.country}` : ""}`);
      bounds.push([s.latitude, s.longitude]);
    }
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }

  // ── Statystyki ──────────────────────────────────────────────────────────
  async function initStatistics() {
    const s = await getJSON(withVehicle("api/statistics"));
    const set = (id, v, d) =>
      (document.getElementById(id).textContent = v == null ? "–" : fmt(v, d));
    set("st-range", s.estimated_range_km, 0);
    set("st-annual", s.leasing.projected_annual_km, 0);
    set("st-lease", s.leasing.odo_vs_budget, 0);
    set("st-region", s.region.latest ? s.region.latest.price : null);
    document.getElementById("region-name").textContent = s.region.name;

    if (s.leasing.km_limit) {
      const card = document.getElementById("lease-card");
      card.hidden = false;
      const l = s.leasing;
      document.getElementById("lease-text").textContent =
        `Przebieg ${fmt(l.current_odometer, 0)} km z limitu ` +
        `${fmt(l.km_limit, 0)} km.` +
        (l.odo_vs_budget != null
          ? ` Zapas względem krzywej leasingu: ${fmt(l.odo_vs_budget, 0)} km.`
          : "") +
        (l.limit_depletion_date
          ? ` Przy obecnym tempie limit wyczerpie się ~${l.limit_depletion_date}.`
          : "");
    }

    // Moja cena vs region — dwie serie na wspólnej osi czasu (dni).
    const days = [...new Set([
      ...s.price_series.map((p) => p.date.slice(0, 10)),
      ...s.region.series.map((p) => p.date),
    ])].sort();
    const byDay = (series) => {
      const m = Object.fromEntries(
        series.map((p) => [p.date.slice(0, 10), p.value]));
      return days.map((d) => m[d] ?? null);
    };
    const opts = baseChartOpts();
    opts.spanGaps = true;
    opts.plugins.legend = { display: true, position: "bottom" };
    new Chart(document.getElementById("chart-vs-region"), {
      type: "line",
      data: {
        labels: days,
        datasets: [
          { label: "Moja cena", data: byDay(s.price_series),
            borderColor: css("--series-1"), backgroundColor: css("--series-1"),
            borderWidth: 2, pointRadius: 2, tension: 0.25, spanGaps: true },
          { label: `Region (${s.region.fuel_type})`, data: byDay(s.region.series),
            borderColor: css("--series-2"), backgroundColor: css("--series-2"),
            borderWidth: 2, pointRadius: 2, tension: 0.25, spanGaps: true,
            borderDash: [6, 4] },
        ],
      },
      options: opts,
    });

    const kmOpts = baseChartOpts();
    kmOpts.scales.y.beginAtZero = true;
    new Chart(document.getElementById("chart-km"), {
      type: "bar",
      data: {
        labels: s.monthly_km.map((m) => m.month),
        datasets: [{ label: "km", data: s.monthly_km.map((m) => m.km),
          backgroundColor: css("--series-1"),
          borderColor: css("--surface"), borderWidth: 1, borderRadius: 3 }],
      },
      options: kmOpts,
    });

    // ── Koszt posiadania (TCO, 0.13.0) ─────────────────────────────────────
    const tco = s.tco;
    set("tco-per-km", tco.cost_per_km.total, 2);
    set("tco-per-month", tco.cost_per_month, 0);
    set("tco-per-100km", tco.cost_per_100km, 0);
    document.getElementById("tco-period").textContent = tco.period_months
      ? `Na podstawie ${fmt(tco.period_months, 1)} mies. historii ` +
        `i ${fmt(tco.distance_km, 0)} km.`
      : "Za mało historii, żeby wyliczyć okres.";

    const tcoLabels = [], tcoValues = [], tcoColors = [];
    const tcoSlice = (label, value, colorVar) => {
      if (value > 0) {
        tcoLabels.push(label); tcoValues.push(value); tcoColors.push(css(colorVar));
      }
    };
    tcoSlice("Paliwo", tco.fuel_total, "--series-1");
    tcoSlice("Płyny", tco.by_group.fluids, "--series-2");
    tcoSlice("Serwis", tco.by_group.service, "--series-4");
    tcoSlice("Ubezpieczenie", tco.by_group.insurance, "--series-5");
    tcoSlice("Opłaty", tco.by_group.fees, "--series-6");
    tcoSlice("Inne", tco.by_group.other, "--series-7");
    if (tco.lease_total) tcoSlice("Rata leasingu", tco.lease_total, "--series-3");
    new Chart(document.getElementById("chart-tco"), {
      type: "doughnut",
      data: { labels: tcoLabels, datasets: [{
        data: tcoValues, backgroundColor: tcoColors,
        borderColor: css("--surface"), borderWidth: 2,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: "bottom",
            labels: { color: css("--muted") } },
          tooltip: { callbacks: {
            label: (ctx) => `${ctx.label}: ${fmt(ctx.parsed, 0)} PLN`,
          } },
        },
      },
    });

    // ── Skumulowany koszt i koszt/km w czasie (na bazie monthly_report) ────
    const months = [...s.monthly_report].reverse();  // rosnąco chronologicznie
    let running = 0;
    const cumulative = months.map((m) => {
      running += m.fuel_card + m.fuel_own + m.fluids + m.other_expenses;
      return Math.round(running * 100) / 100;
    });
    lineChart("chart-cumulative", months.map((m) => m.month), cumulative,
      "--series-1", "Skumulowany koszt (PLN)");

    const costPerKmSeries = months.map((m) => {
      const total = m.fuel_card + m.fuel_own + m.fluids + m.other_expenses;
      return m.km > 0 ? Math.round((total / m.km) * 100) / 100 : null;
    });
    const cpkOpts = baseChartOpts();
    cpkOpts.spanGaps = true;
    new Chart(document.getElementById("chart-cost-per-km"), {
      type: "line",
      data: { labels: months.map((m) => m.month), datasets: [{
        label: "PLN/km", data: costPerKmSeries,
        borderColor: css("--series-4"), backgroundColor: css("--series-4"),
        borderWidth: 2, pointRadius: 2, tension: 0.25, spanGaps: true,
      }] },
      options: cpkOpts,
    });

    const r = s.records;
    const seg = (x) => x
      ? `${fmt(x.l_per_100km)} L/100km (${x.distance_km} km, ${x.date.slice(0, 10)})`
      : "–";
    const fill = (x) => x
      ? `${fmt(x.price_per_l)} PLN/L (${x.station || "?"}, ${x.date.slice(0, 10)})`
      : "–";
    document.querySelector("#records-table tbody").innerHTML = [
      ["Najlepsze spalanie", seg(r.best_consumption)],
      ["Najgorsze spalanie", seg(r.worst_consumption)],
      ["Najdłuższy dystans na baku", r.longest_segment
        ? `${fmt(r.longest_segment.distance_km, 0)} km (${r.longest_segment.date.slice(0, 10)})` : "–"],
      ["Najtańsze tankowanie", fill(r.cheapest_fillup)],
      ["Najdroższe tankowanie", fill(r.most_expensive_fillup)],
    ].map(([n, v]) => `<tr><td>${n}</td><td>${v}</td></tr>`).join("");

    document.querySelector("#stations-table tbody").innerHTML =
      s.stations.map((x) => `
        <tr><td>${x.station}</td><td class="num">${x.visits}</td>
        <td class="num">${fmt(x.volume_l, 1)}</td>
        <td class="num">${fmt(x.total_cost, 0)}</td>
        <td class="num">${x.avg_price != null ? fmt(x.avg_price) : "–"}</td></tr>`).join("");

    document.querySelector("#report-table tbody").innerHTML =
      s.monthly_report.map((m) => `
        <tr><td>${m.month}</td><td class="num">${fmt(m.fuel_card)}</td>
        <td class="num">${fmt(m.fuel_own)}</td>
        <td class="num">${fmt(m.fluids)}</td>
        <td class="num">${fmt(m.other_expenses)}</td>
        <td class="num">${fmt(m.volume_l, 1)}</td>
        <td class="num">${fmt(m.km, 0)}</td></tr>`).join("");
  }

  // ── Ustawienia / import ─────────────────────────────────────────────────
  function initSettings() {
    const vehicleForm = document.getElementById("vehicle-form");

    function openVehicleForm(v) {
      document.getElementById("vehicle-form-title").textContent =
        v ? `Edycja: ${v.name}` : "Nowy pojazd";
      document.getElementById("veh-id").value = v ? v.id : "";
      document.getElementById("veh-name").value = v ? v.name : "";
      document.getElementById("veh-tank").value = v ? v.tank_capacity_l : "";
      document.getElementById("veh-fuel").value = v ? v.fuel_type : "";
      document.getElementById("veh-lease-start").value =
        (v && v.lease_start) || "";
      document.getElementById("veh-lease-end").value = (v && v.lease_end) || "";
      document.getElementById("veh-lease-limit").value =
        v && v.lease_km_limit != null ? v.lease_km_limit : "";
      document.getElementById("veh-lease-rate").value =
        v && v.monthly_rate != null ? v.monthly_rate : "";
      document.getElementById("veh-budget").value =
        v && v.monthly_fuel_budget != null ? v.monthly_fuel_budget : "";
      document.getElementById("veh-odometer").value =
        (v && v.odometer_entity) || "";
      document.getElementById("veh-fuel-level").value =
        (v && v.fuel_level_entity) || "";
      document.getElementById("veh-location").value =
        (v && v.location_entity) || "";
      vehicleForm.hidden = false;
      vehicleForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
      document.getElementById("veh-name").focus();
    }

    function closeVehicleForm() {
      vehicleForm.hidden = true;
    }

    document.getElementById("vehicle-add-btn").addEventListener(
      "click", () => openVehicleForm(null));
    document.getElementById("veh-cancel").addEventListener(
      "click", closeVehicleForm);

    vehicleForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = document.getElementById("veh-id").value;
      const limit = document.getElementById("veh-lease-limit").value;
      const rate = document.getElementById("veh-lease-rate").value;
      const payload = {
        name: document.getElementById("veh-name").value.trim(),
        tank_capacity_l: parseFloat(document.getElementById("veh-tank").value),
        fuel_type: document.getElementById("veh-fuel").value.trim(),
        lease_start: document.getElementById("veh-lease-start").value || null,
        lease_end: document.getElementById("veh-lease-end").value || null,
        lease_km_limit: limit ? parseInt(limit, 10) : null,
        monthly_rate: rate ? parseFloat(rate) : null,
        monthly_fuel_budget: parseFloat(
          document.getElementById("veh-budget").value) || 0,
        odometer_entity: document.getElementById("veh-odometer").value.trim(),
        fuel_level_entity:
          document.getElementById("veh-fuel-level").value.trim(),
        location_entity: document.getElementById("veh-location").value.trim(),
      };
      const r = await fetch(id ? `api/vehicles/${id}` : "api/vehicles", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || `HTTP ${r.status}`); return; }
      closeVehicleForm();
      loadVehicleList();
    });

    const vehicleList = document.getElementById("vehicle-list");
    async function loadVehicleList() {
      const rows = await getJSON("api/vehicles");
      vehicleList.innerHTML = `
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Nazwa</th><th>Paliwo</th>
          <th class="num">Bak (L)</th><th>Stan</th><th>Leasing</th><th></th></tr></thead>
          <tbody>${rows.map((v) => {
            const badge = v.active ? '<span class="badge-active">aktywny</span>'
              : v.archived ? '<span class="muted">zarchiwizowany</span>' : "";
            const lease = v.lease_km_limit
              ? `${fmt(v.lease_km_limit, 0)} km${v.lease_end ? " do " + v.lease_end : ""}`
              : "–";
            const actions = [
              `<button type="button" class="btn small" data-edit="${v.id}">Edytuj</button>`];
            if (!v.active && !v.archived) actions.push(
              `<button type="button" class="btn small" data-activate="${v.id}">Aktywuj</button>`);
            if (!v.archived) actions.push(
              `<button type="button" class="btn small" data-archive="${v.id}">Archiwizuj</button>`);
            else actions.push(
              `<button type="button" class="btn small" data-unarchive="${v.id}">Przywróć</button>`);
            if (!v.active) actions.push(
              `<button type="button" class="btn small" data-delete="${v.id}">Usuń</button>`);
            return `<tr${v.active ? ' class="row-active"' : ""}>
              <td>${v.name}</td><td>${v.fuel_type}</td>
              <td class="num">${fmt(v.tank_capacity_l, 1)}</td>
              <td>${badge}</td><td>${lease}</td>
              <td>${actions.join(" ")}</td></tr>`;
          }).join("")}</tbody>
        </table></div>`;

      vehicleList.querySelectorAll("[data-edit]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          openVehicleForm(await getJSON(`api/vehicles/${btn.dataset.edit}`));
        }));
      vehicleList.querySelectorAll("[data-activate]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          await fetch(`api/vehicles/${btn.dataset.activate}/activate`,
                     { method: "POST" });
          location.reload();
        }));
      vehicleList.querySelectorAll("[data-archive]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          await fetch(`api/vehicles/${btn.dataset.archive}/archive`,
                     { method: "POST" });
          loadVehicleList();
        }));
      vehicleList.querySelectorAll("[data-unarchive]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          await fetch(`api/vehicles/${btn.dataset.unarchive}/unarchive`,
                     { method: "POST" });
          loadVehicleList();
        }));
      vehicleList.querySelectorAll("[data-delete]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!confirm("Usunąć pojazd? Tej operacji nie można cofnąć."))
            return;
          const r = await fetch(`api/vehicles/${btn.dataset.delete}`,
                                { method: "DELETE" });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) { alert(data.error || `HTTP ${r.status}`); return; }
          loadVehicleList();
        }));
    }
    loadVehicleList();

    const backupList = document.getElementById("backup-list");
    async function loadBackupList() {
      const rows = await getJSON("api/backup/list");
      if (!rows.length) {
        backupList.innerHTML = '<p class="muted">Brak nocnych kopii jeszcze.</p>';
        return;
      }
      backupList.innerHTML = `
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Plik</th><th>Data</th>
          <th class="num">Rozmiar</th><th></th></tr></thead>
          <tbody>${rows.map((b) => `
            <tr><td>${b.filename}</td><td>${b.created_at || "–"}</td>
              <td class="num">${fmt(b.size_bytes / 1024 / 1024, 1)} MB</td>
              <td><button type="button" class="btn small"
                data-restore="${b.filename}">Przywróć</button></td></tr>`
          ).join("")}</tbody>
        </table></div>`;
      backupList.querySelectorAll("[data-restore]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!confirm(`Przywrócić bazę z "${btn.dataset.restore}"? ` +
              "Bieżące dane zostaną najpierw automatycznie zabezpieczone."))
            return;
          const r = await sendJSON("api/backup/restore", "POST",
            { filename: btn.dataset.restore }).catch((e) => ({ error: e.message }));
          if (r && r.error) { alert(r.error); return; }
          location.reload();
        }));
    }
    loadBackupList();

    document.getElementById("backup-upload-form").addEventListener(
      "submit", async (e) => {
        e.preventDefault();
        const form = e.target;
        if (!confirm("Przywrócić bazę z wgranego pliku? Bieżące dane " +
            "zostaną najpierw automatycznie zabezpieczone."))
          return;
        const r = await fetch("api/backup/restore/upload",
          { method: "POST", body: new FormData(form) });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { alert(data.error || `HTTP ${r.status}`); return; }
        location.reload();
      });

    document.getElementById("backup-json-form").addEventListener(
      "submit", async (e) => {
        e.preventDefault();
        const form = e.target;
        const rep = document.getElementById("backup-json-report");
        if (!confirm("Przywrócić WSZYSTKIE dane z pliku JSON? To zastąpi " +
            "całą bieżącą bazę (poza pełną migracją .db, ta operacja wymaga " +
            "identycznej wersji add-onu)."))
          return;
        rep.hidden = false;
        rep.textContent = "Importuję…";
        const r = await fetch("api/backup/import.json",
          { method: "POST", body: new FormData(form) });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { rep.textContent = data.error || `HTTP ${r.status}`; return; }
        rep.textContent = "Import JSON zakończony.";
        setTimeout(() => location.reload(), 1000);
      });

    async function loadNotifyServices(current) {
      const select = document.getElementById("set-notify-service");
      let services = [];
      try {
        services = (await getJSON("api/ha-services")).services || [];
      } catch (e) { /* HA API niedostępne — zostaje sama bieżąca wartość */ }
      if (current && !services.includes(current)) services.unshift(current);
      select.innerHTML = services
        .map((s) => `<option value="${s}">${s}</option>`).join("");
      select.value = current || (services[0] || "");
    }

    async function loadSettingsForms() {
      const s = await getJSON("api/settings");
      document.getElementById("set-currency").value = s.default_currency;
      document.getElementById("set-region").value = s.price_region;
      document.getElementById("alert-budget-on").checked =
        !!s.alert_budget_enabled;
      document.getElementById("alert-budget-threshold").value =
        s.alert_budget_threshold;
      document.getElementById("alert-cheap-on").checked =
        !!s.alert_cheap_fuel_enabled;
      document.getElementById("alert-cheap-delta").value =
        s.alert_cheap_fuel_delta;
      document.getElementById("alert-lease-on").checked =
        !!s.alert_lease_enabled;
      document.getElementById("alert-lease-km").value =
        s.alert_lease_km_threshold;
      await loadNotifyServices(s.notify_service);
    }
    loadSettingsForms();

    document.getElementById("notify-form").addEventListener(
      "submit", async (e) => {
        e.preventDefault();
        await sendJSON("api/settings", "PUT", {
          notify_service:
            document.getElementById("set-notify-service").value.trim(),
          alert_budget_enabled:
            document.getElementById("alert-budget-on").checked ? 1 : 0,
          alert_budget_threshold: parseFloat(
            document.getElementById("alert-budget-threshold").value) || 0,
          alert_cheap_fuel_enabled:
            document.getElementById("alert-cheap-on").checked ? 1 : 0,
          alert_cheap_fuel_delta: parseFloat(
            document.getElementById("alert-cheap-delta").value) || 0,
          alert_lease_enabled:
            document.getElementById("alert-lease-on").checked ? 1 : 0,
          alert_lease_km_threshold: parseInt(
            document.getElementById("alert-lease-km").value, 10) || 0,
        });
      });

    document.getElementById("budget-form").addEventListener(
      "submit", async (e) => {
        e.preventDefault();
        await sendJSON("api/settings", "PUT", {
          default_currency:
            document.getElementById("set-currency").value.trim().toUpperCase(),
        });
      });

    document.getElementById("region-form").addEventListener(
      "submit", async (e) => {
        e.preventDefault();
        await sendJSON("api/settings", "PUT", {
          price_region: document.getElementById("set-region").value.trim(),
        });
      });

    const csvForm = document.getElementById("csv-form");
    csvForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const rep = document.getElementById("csv-report");
      rep.hidden = false;
      rep.textContent = "Importuję…";
      const fd = new FormData(csvForm);
      const r = await fetch(withVehicle("api/import/csv"),
        { method: "POST", body: fd });
      rep.textContent = JSON.stringify(await r.json(), null, 2);
    });

    document.getElementById("drivvo-btn").addEventListener("click", async () => {
      const rep = document.getElementById("drivvo-report");
      rep.hidden = false;
      rep.textContent = "Importuję z Drivvo…";
      const r = await fetch(withVehicle("api/import/drivvo"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          include_refuellings: document.getElementById("drivvo-refuel").checked,
        }),
      });
      rep.textContent = JSON.stringify(await r.json(), null, 2);
    });

    // ── Kategorie wydatków: CRUD + grupa TCO (0.13.0) ──────────────────────
    const TCO_LABELS = { fluids: "Płyny", service: "Serwis",
      insurance: "Ubezpieczenie", fees: "Opłaty", other: "Inne" };
    const catList = document.getElementById("category-list");
    const catForm = document.getElementById("category-form");

    function openCategoryForm(c) {
      document.getElementById("category-form-title").textContent =
        c ? `Edycja: ${c.name}` : "Nowa kategoria";
      document.getElementById("cat-id").value = c ? c.id : "";
      document.getElementById("cat-name").value = c ? c.name : "";
      document.getElementById("cat-group").value = c ? c.tco_group : "other";
      catForm.hidden = false;
      catForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
      document.getElementById("cat-name").focus();
    }
    function closeCategoryForm() { catForm.hidden = true; }

    document.getElementById("category-add-btn").addEventListener(
      "click", () => openCategoryForm(null));
    document.getElementById("cat-cancel").addEventListener(
      "click", closeCategoryForm);

    const renderCats = async () => {
      const cats = await getJSON("api/categories");
      catList.innerHTML = `
        <div class="table-wrap"><table class="table">
          <thead><tr><th>Nazwa</th><th>Grupa TCO</th><th>Widoczna</th>
          <th></th></tr></thead>
          <tbody>${cats.map((c) => `
            <tr>
              <td>${c.name}</td>
              <td>${TCO_LABELS[c.tco_group] || c.tco_group}</td>
              <td><label class="check">
                <input type="checkbox" data-vis="${c.id}" ${c.hidden ? "" : "checked"}>
              </label></td>
              <td>
                <button type="button" class="btn small" data-edit="${c.id}">Edytuj</button>
                <button type="button" class="btn small danger" data-del="${c.id}">Usuń</button>
              </td>
            </tr>`).join("")}</tbody>
        </table></div>`;

      catList.querySelectorAll("[data-vis]").forEach((el) =>
        el.addEventListener("change", async () => {
          await sendJSON(`api/categories/${el.dataset.vis}`, "PUT",
            { hidden: !el.checked });
        }));
      catList.querySelectorAll("[data-edit]").forEach((btn) =>
        btn.addEventListener("click", () => {
          const c = cats.find((x) => String(x.id) === btn.dataset.edit);
          if (c) openCategoryForm(c);
        }));
      catList.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!confirm("Usunąć kategorię?")) return;
          const r = await fetch(`api/categories/${btn.dataset.del}`,
                                { method: "DELETE" });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) { alert(data.error || `HTTP ${r.status}`); return; }
          renderCats();
        }));
    };
    renderCats();

    catForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = document.getElementById("cat-id").value;
      const payload = {
        name: document.getElementById("cat-name").value.trim(),
        tco_group: document.getElementById("cat-group").value,
      };
      const r = await fetch(id ? `api/categories/${id}` : "api/categories", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { alert(data.error || `HTTP ${r.status}`); return; }
      closeCategoryForm();
      renderCats();
    });
  }

  // ── Porównanie pojazdów (0.13.0) ────────────────────────────────────────
  async function initCompare() {
    const rows = await getJSON("api/compare");
    const tbody = document.querySelector("#compare-table tbody");
    tbody.innerHTML = rows.map((v) => `
      <tr${v.active ? ' class="row-active"' : ""}>
        <td>${v.name}${v.active ? ' <span class="badge-active">aktywny</span>' : ""}</td>
        <td>${v.fuel_type}</td>
        <td class="num">${v.fillup_count}</td>
        <td class="num">${v.avg_consumption != null ? fmt(v.avg_consumption) : "–"}</td>
        <td class="num">${v.tco.cost_per_km.total != null ? fmt(v.tco.cost_per_km.total, 2) : "–"}</td>
        <td class="num">${v.avg_price_per_l != null ? fmt(v.avg_price_per_l) : "–"}</td>
        <td class="num">${v.monthly_km != null ? fmt(v.monthly_km, 0) : "–"}</td>
        <td class="num">${fmt(v.expenses_total, 0)}</td>
      </tr>`).join("") ||
      '<tr><td colspan="8" class="muted">Brak pojazdów</td></tr>';

    const names = rows.map((v) => v.name);

    const consOpts = baseChartOpts();
    consOpts.scales.y.beginAtZero = true;
    new Chart(document.getElementById("chart-compare-consumption"), {
      type: "bar",
      data: { labels: names, datasets: [{
        label: "L/100km", data: rows.map((v) => v.avg_consumption),
        backgroundColor: css("--series-1"),
        borderColor: css("--surface"), borderWidth: 1, borderRadius: 3,
      }] },
      options: consOpts,
    });

    const tcoOpts = baseChartOpts();
    tcoOpts.scales.y.beginAtZero = true;
    new Chart(document.getElementById("chart-compare-tco"), {
      type: "bar",
      data: { labels: names, datasets: [{
        label: "PLN/km", data: rows.map((v) => v.tco.cost_per_km.total),
        backgroundColor: css("--series-4"),
        borderColor: css("--surface"), borderWidth: 1, borderRadius: 3,
      }] },
      options: tcoOpts,
    });
  }

  return { initDashboard, initFillups, initFillupForm, initExpenses,
           initSettings, initMap, initStatistics, initVehicleSwitcher,
           initCompare };
})();
