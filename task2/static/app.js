/* Barebones UI: fetch APIs and render plain HTML tables. */

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || res.statusText || "Request failed");
  }
  return data;
}

function setMessage(text, isError) {
  const el = document.getElementById("register-message");
  el.textContent = text || "";
  el.className = "message" + (isError ? " error" : "");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function loadWorkshops() {
  const workshops = await api("/api/workshops");
  const select = document.getElementById("register-workshop");
  const openOnes = workshops.filter((w) => w.status === "open");

  select.innerHTML = openOnes.length
    ? openOnes
        .map(
          (w) =>
            `<option value="${w.id}">#${w.id} — ${escapeHtml(w.title)}</option>`
        )
        .join("")
    : `<option value="">No open workshops</option>`;

  const container = document.getElementById("workshops");
  if (!workshops.length) {
    container.innerHTML = '<p class="empty">No workshops yet.</p>';
    return;
  }

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Title</th>
          <th>Status</th>
          <th>Registrations</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${workshops
          .map((w) => {
            const canCancel = w.status === "open";
            return `
              <tr>
                <td>${w.id}</td>
                <td>${escapeHtml(w.title)}</td>
                <td>${escapeHtml(w.status)}</td>
                <td>${w.registration_count}</td>
                <td>
                  <button
                    type="button"
                    class="secondary"
                    data-cancel="${w.id}"
                    ${canCancel ? "" : "disabled"}
                  >
                    Cancel
                  </button>
                </td>
              </tr>`;
          })
          .join("")}
      </tbody>
    </table>
  `;

  container.querySelectorAll("[data-cancel]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-cancel");
      if (!confirm("Cancel this workshop and notify all registrants?")) return;
      try {
        const result = await api(`/api/workshops/${id}/cancel`, {
          method: "POST",
          body: "{}",
        });
        setMessage(
          `Workshop cancelled. Enqueued ${result.notifications_enqueued} cancel notification(s).`
        );
        await refreshAll();
      } catch (err) {
        setMessage(err.message, true);
      }
    });
  });
}

async function loadRegistrations() {
  const rows = await api("/api/registrations");
  const container = document.getElementById("registrations");
  if (!rows.length) {
    container.innerHTML = '<p class="empty">No registrations yet.</p>';
    return;
  }
  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Workshop</th>
          <th>Name</th>
          <th>Email</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (r) => `
          <tr>
            <td>${r.id}</td>
            <td>#${r.workshop_id} ${escapeHtml(r.workshop_title)} (${escapeHtml(r.workshop_status)})</td>
            <td>${escapeHtml(r.member_name)}</td>
            <td>${escapeHtml(r.member_email)}</td>
            <td>${escapeHtml(r.created_at)}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function loadNotifications() {
  const rows = await api("/api/notifications");
  const container = document.getElementById("notifications");
  if (!rows.length) {
    container.innerHTML = '<p class="empty">No notification jobs yet.</p>';
    return;
  }
  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Event</th>
          <th>Workshop</th>
          <th>Member</th>
          <th>Status</th>
          <th>Attempts</th>
          <th>Last error</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (n) => `
          <tr>
            <td>${n.id}</td>
            <td>${escapeHtml(n.event_type)}</td>
            <td>#${n.workshop_id} ${escapeHtml(n.workshop_title)}</td>
            <td>${escapeHtml(n.member_name)} &lt;${escapeHtml(n.member_email)}&gt;</td>
            <td class="status-${escapeHtml(n.status)}">${escapeHtml(n.status)}</td>
            <td>${n.attempts}</td>
            <td>${escapeHtml(n.last_error || "")}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function refreshAll() {
  await loadWorkshops();
  await loadRegistrations();
  await loadNotifications();
}

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = document.getElementById("workshop-title").value.trim();
  try {
    await api("/api/workshops", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    document.getElementById("workshop-title").value = "";
    setMessage("Workshop created.");
    await refreshAll();
  } catch (err) {
    setMessage(err.message, true);
  }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const workshopId = document.getElementById("register-workshop").value;
  const member_name = document.getElementById("member-name").value.trim();
  const member_email = document.getElementById("member-email").value.trim();
  if (!workshopId) {
    setMessage("Create an open workshop first.", true);
    return;
  }
  try {
    await api(`/api/workshops/${workshopId}/register`, {
      method: "POST",
      body: JSON.stringify({ member_name, member_email }),
    });
    setMessage("Registered. Confirmation will be sent asynchronously.");
    document.getElementById("member-name").value = "";
    document.getElementById("member-email").value = "";
    await refreshAll();
  } catch (err) {
    setMessage(err.message, true);
  }
});

document.getElementById("refresh-btn").addEventListener("click", () => {
  refreshAll().catch((err) => setMessage(err.message, true));
});

refreshAll().catch((err) => setMessage(err.message, true));

// Auto-refresh notification table so retries are visible during demos.
setInterval(() => {
  loadNotifications().catch(() => {});
}, 2000);
