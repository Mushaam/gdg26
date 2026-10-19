/* Barebones organizer UI — plain fetch + DOM updates */

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || res.statusText);
  }
  return data;
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  return node;
}

async function loadStats() {
  const stats = await api("/api/stats");
  document.getElementById("stats").textContent = JSON.stringify(stats, null, 2);
}

async function loadReadings() {
  const rows = await api("/api/readings?limit=30");
  const box = document.getElementById("readings");
  box.innerHTML = "";
  if (!rows.length) {
    box.appendChild(el("p", "No readings yet."));
    return;
  }
  const table = el("table");
  table.innerHTML =
    "<tr><th>ID</th><th>Sensor</th><th>Type</th><th>Value</th><th>Timestamp</th></tr>";
  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      "<td>" + r.id + "</td>" +
      "<td>" + r.sensor_id + "</td>" +
      "<td>" + r.type + "</td>" +
      "<td>" + r.value + "</td>" +
      "<td>" + r.timestamp + "</td>";
    table.appendChild(tr);
  }
  box.appendChild(table);
}

async function loadRules() {
  const rows = await api("/api/rules");
  const box = document.getElementById("rules");
  box.innerHTML = "";
  if (!rows.length) {
    box.appendChild(el("p", "No rules yet. Create one above."));
    return;
  }
  const table = el("table");
  table.innerHTML =
    "<tr><th>ID</th><th>Name</th><th>Target</th><th>Kind</th><th>Condition</th><th>Enabled</th><th>Actions</th></tr>";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const target = (r.sensor_id || "all sensors") + " / " + r.metric_type;
    const condition =
      r.operator + " " + r.threshold +
      (r.rule_kind === "window" ? " (avg " + r.window_minutes + "m)" : "");
    const actions = document.createElement("td");

    const toggle = el("button", r.enabled ? "Disable" : "Enable");
    toggle.className = "secondary";
    toggle.onclick = async function () {
      const path = r.enabled
        ? "/api/rules/" + r.id + "/disable"
        : "/api/rules/" + r.id + "/enable";
      await api(path, { method: "POST", body: "{}" });
      await refreshAll();
    };
    actions.appendChild(toggle);

    tr.innerHTML =
      "<td>" + r.id + "</td>" +
      "<td>" + r.name + "</td>" +
      "<td>" + target + "</td>" +
      "<td>" + r.rule_kind + "</td>" +
      "<td>" + condition + "</td>" +
      "<td>" + (r.enabled ? "yes" : "no") + "</td>";
    tr.appendChild(actions);
    table.appendChild(tr);
  }
  box.appendChild(table);
}

async function loadAlerts() {
  const rows = await api("/api/alerts");
  const box = document.getElementById("alerts");
  box.innerHTML = "";
  if (!rows.length) {
    box.appendChild(el("p", "No alerts yet."));
    return;
  }
  const table = el("table");
  table.innerHTML =
    "<tr><th>ID</th><th>Rule</th><th>Sensor</th><th>Value</th><th>Status</th><th>Message</th><th>Actions</th></tr>";
  for (const a of rows) {
    const tr = document.createElement("tr");
    const actions = document.createElement("td");

    if (a.status === "open") {
      const ack = el("button", "Acknowledge");
      ack.className = "secondary";
      ack.onclick = async function () {
        await api("/api/alerts/" + a.id + "/acknowledge", { method: "POST", body: "{}" });
        await refreshAll();
      };
      actions.appendChild(ack);
    }
    if (a.status === "open" || a.status === "acknowledged") {
      const resolve = el("button", "Resolve");
      resolve.className = "danger";
      resolve.onclick = async function () {
        await api("/api/alerts/" + a.id + "/resolve", { method: "POST", body: "{}" });
        await refreshAll();
      };
      actions.appendChild(resolve);
    }

    tr.innerHTML =
      "<td>" + a.id + "</td>" +
      "<td>" + a.rule_id + "</td>" +
      "<td>" + a.sensor_id + "</td>" +
      "<td>" + a.trigger_value + "</td>" +
      "<td>" + a.status + "</td>" +
      "<td>" + a.message + "</td>";
    tr.appendChild(actions);
    table.appendChild(tr);
  }
  box.appendChild(table);
}

async function refreshAll() {
  await Promise.all([loadStats(), loadRules(), loadAlerts(), loadReadings()]);
}

function isoNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function simulateReadings() {
  const sensors = ["library-floor-2", "cafeteria", "gym", "lab-a"];
  const types = ["temperature", "air_quality", "occupancy"];
  const batch = [];
  for (let i = 0; i < 20; i++) {
    const type = types[i % types.length];
    let value;
    if (type === "temperature") value = 20 + Math.random() * 20;
    else if (type === "air_quality") value = 10 + Math.random() * 90;
    else value = Math.floor(Math.random() * 80);

    batch.push({
      sensorId: sensors[i % sensors.length],
      type: type,
      value: Math.round(value * 10) / 10,
      timestamp: isoNow(),
    });
  }
  await api("/api/readings/batch", { method: "POST", body: JSON.stringify(batch) });
  // Give the worker a moment to flush
  setTimeout(refreshAll, 300);
}

document.getElementById("rule-form").addEventListener("submit", async function (e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {
    name: fd.get("name"),
    sensor_id: fd.get("sensor_id") || null,
    metric_type: fd.get("metric_type"),
    rule_kind: fd.get("rule_kind"),
    operator: fd.get("operator"),
    threshold: Number(fd.get("threshold")),
    window_minutes: Number(fd.get("window_minutes")) || null,
    enabled: true,
  };
  await api("/api/rules", { method: "POST", body: JSON.stringify(body) });
  e.target.reset();
  await refreshAll();
});

document.getElementById("btn-refresh").onclick = refreshAll;
document.getElementById("btn-simulate").onclick = simulateReadings;

refreshAll();
setInterval(refreshAll, 5000);
