const state = {
  status: null,
  config: null,
};

const els = {
  substatus: document.querySelector("#substatus"),
  runningMetric: document.querySelector("#runningMetric"),
  fpsMetric: document.querySelector("#fpsMetric"),
  trackMetric: document.querySelector("#trackMetric"),
  tempMetric: document.querySelector("#tempMetric"),
  throttleMetric: document.querySelector("#throttleMetric"),
  objects: document.querySelector("#objects"),
  zones: document.querySelector("#zones"),
  labels: document.querySelector("#labels"),
  events: document.querySelector("#events"),
  eventCount: document.querySelector("#eventCount"),
  clips: document.querySelector("#clips"),
  reports: document.querySelector("#reports"),
  startBtn: document.querySelector("#startBtn"),
  stopBtn: document.querySelector("#stopBtn"),
  reportBtn: document.querySelector("#reportBtn"),
  stream: document.querySelector("#stream"),
};

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(status) {
  state.status = status;
  const running = Boolean(status.running);
  els.runningMetric.textContent = running ? "Online" : "Offline";
  els.runningMetric.className = `metric ${running ? "online" : "warning"}`;
  els.fpsMetric.textContent = `${status.fps || 0} fps`;
  els.trackMetric.textContent = `${(status.tracks || []).length} tracks`;
  const system = status.system || {};
  const temp = system.temperature_c;
  els.tempMetric.textContent = temp == null ? "Temp --" : `${temp.toFixed(1)} C`;
  els.tempMetric.className = `metric ${["hot", "critical"].includes(system.temperature_status) ? "warning" : ""}`;
  const throttle = status.throttle || {};
  els.throttleMetric.textContent = throttle.active ? throttle.mode : "Normal";
  els.throttleMetric.className = `metric ${throttle.active ? "warning" : "online"}`;
  els.substatus.textContent = status.last_error
    ? status.last_error
    : `Frame ${status.frame_index || 0} | detector ${status.detector || "unknown"}${
        throttle.active ? ` | ${throttle.reason}` : ""
      }`;

  if (!els.objects.contains(document.activeElement)) {
    renderObjects(status.tracks || status.detections || []);
  }
}

function renderObjects(objects) {
  if (!objects.length) {
    els.objects.innerHTML = `<div class="empty">No active objects</div>`;
    return;
  }
  els.objects.innerHTML = objects
    .slice(0, 8)
    .map((item) => {
      const confidence = Number(item.confidence || 0).toFixed(2);
      const detectorLabel = item.detector_label && item.detector_label !== item.label ? item.detector_label : "";
      const zones = item.zones_seen && item.zones_seen.length ? ` | ${item.zones_seen.join(", ")}` : "";
      const trackId = item.track_id ?? "";
      return `
        <div class="object">
          <div>
            <strong>#${item.track_id ?? "-"} ${escapeHtml(item.label)}</strong>
            <small>${escapeHtml(item.source || "vision")}${
              detectorLabel ? ` | base ${escapeHtml(detectorLabel)}` : ""
            }${escapeHtml(zones)}</small>
            ${
              trackId
                ? `<form class="label-form" data-track-id="${escapeHtml(trackId)}">
                    <input name="label" value="${escapeHtml(item.custom_label || "")}" placeholder="Label object" aria-label="Label object #${escapeHtml(trackId)}" />
                    <button type="submit">Save</button>
                  </form>`
                : ""
            }
          </div>
          <small>${confidence}</small>
        </div>`;
    })
    .join("");
}

function renderZones(config) {
  const zones = config.zones || [];
  if (!zones.length) {
    els.zones.innerHTML = `<div class="empty">No zones configured</div>`;
    return;
  }
  els.zones.innerHTML = zones
    .map(
      (zone) => `
        <div class="zone">
          <strong>${escapeHtml(zone.name)}</strong>
          <small>${zone.x1}, ${zone.y1}, ${zone.x2}, ${zone.y2}</small>
        </div>`,
    )
    .join("");
}

function renderLabels(labels) {
  if (!labels.length) {
    els.labels.innerHTML = `<div class="empty">No learned labels</div>`;
    return;
  }
  els.labels.innerHTML = labels
    .slice(0, 8)
    .map(
      (profile) => `
        <div class="label-profile">
          <div>
            <strong>${escapeHtml(profile.name)}</strong>
            <small>${escapeHtml(profile.base_label)} | matches ${profile.match_count}</small>
          </div>
          <button type="button" data-delete-label="${escapeHtml(profile.id)}">Delete</button>
        </div>`,
    )
    .join("");
}

function renderEvents(events) {
  els.eventCount.textContent = `${events.length} events`;
  if (!events.length) {
    els.events.innerHTML = `<div class="empty">No events recorded</div>`;
    return;
  }
  els.events.innerHTML = events
    .map(
      (event) => `
        <article class="event">
          <time>${formatTime(event.timestamp)}</time>
          <div>
            <strong>${escapeHtml(event.summary)}</strong>
            <small>${escapeHtml(event.event_type)}${event.zone ? ` | ${escapeHtml(event.zone)}` : ""}</small>
          </div>
          <span class="severity ${escapeHtml(event.severity)}">${escapeHtml(event.severity)}</span>
        </article>`,
    )
    .join("");
}

function renderClips(clips) {
  if (!clips.length) {
    els.clips.innerHTML = `<div class="empty">No clips saved</div>`;
    return;
  }
  els.clips.innerHTML = clips
    .slice(0, 5)
    .map(
      (clip) => `
        <div class="media-item">
          <strong><a href="${escapeHtml(clip.url)}" target="_blank" rel="noreferrer">${escapeHtml(clip.name)}</a></strong>
          <small>${formatBytes(clip.size)}</small>
        </div>`,
    )
    .join("");
}

function renderReports(reports) {
  if (!reports.length) {
    els.reports.innerHTML = `<div class="empty">No reports generated</div>`;
    return;
  }
  els.reports.innerHTML = reports
    .slice(0, 5)
    .map(
      (report) => `
        <article class="report">
          <strong>${escapeHtml(report.title)}</strong>
          <small>${formatTime(report.created_at)}</small>
          <p>${escapeHtml(report.body)}</p>
        </article>`,
    )
    .join("");
}

async function refreshFast() {
  try {
    setStatus(await getJson("/status"));
  } catch (error) {
    els.substatus.textContent = error.message;
  }
}

async function refreshSlow() {
  const [events, clips, reports, labels] = await Promise.all([
    getJson("/events?limit=5"),
    getJson("/clips"),
    getJson("/reports?limit=5"),
    getJson("/object-labels"),
  ]);
  renderEvents(events);
  renderClips(clips);
  renderReports(reports);
  renderLabels(labels);
}

async function initialize() {
  await getJson("/object-labels/reset", { method: "POST" });
  state.config = await getJson("/config");
  renderZones(state.config);
  await refreshFast();
  await refreshSlow();
  setInterval(refreshFast, 1000);
  setInterval(refreshSlow, 5000);
}

els.startBtn.addEventListener("click", async () => {
  await getJson("/pipeline/start", { method: "POST" });
  els.stream.src = `/stream?t=${Date.now()}`;
  await refreshFast();
});

els.stopBtn.addEventListener("click", async () => {
  els.stream.removeAttribute("src");
  await getJson("/pipeline/stop", { method: "POST" });
  await refreshFast();
});

els.reportBtn.addEventListener("click", async () => {
  els.reportBtn.disabled = true;
  try {
    await getJson("/reports/generate", { method: "POST" });
    await refreshSlow();
  } finally {
    els.reportBtn.disabled = false;
  }
});

els.objects.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const trackId = form.dataset.trackId;
  const input = form.elements.namedItem("label");
  if (!trackId || !(input instanceof HTMLInputElement) || !input.value.trim()) return;
  const button = form.querySelector("button");
  if (button) button.disabled = true;
  try {
    await getJson(`/objects/${encodeURIComponent(trackId)}/label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: input.value }),
    });
    await refreshFast();
    await refreshSlow();
  } finally {
    if (button) button.disabled = false;
  }
});

els.labels.addEventListener("click", async (event) => {
  const button = event.target;
  if (!(button instanceof HTMLButtonElement)) return;
  const profileId = button.dataset.deleteLabel;
  if (!profileId) return;
  button.disabled = true;
  try {
    await getJson(`/object-labels/${encodeURIComponent(profileId)}`, { method: "DELETE" });
    await refreshSlow();
  } finally {
    button.disabled = false;
  }
});

initialize().catch((error) => {
  els.substatus.textContent = error.message;
});
