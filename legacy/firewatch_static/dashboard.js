// FireWatch — monitoring logic: status, zones, detectors, zone editor, permits.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var toast = window.fwToast;

  // badge for a tri-state value: true / false / null
  function triBadge(v) {
    if (v === true) return '<span class="badge ok">yes</span>';
    if (v === false) return '<span class="badge bad">no</span>';
    return '<span class="badge muted">n/a</span>';
  }

  // ---------------- status ----------------
  async function refreshStatus() {
    var s;
    try { s = await (await fetch("/api/status")).json(); }
    catch (e) { return; }

    $("s-frames").textContent = s.frames_processed;
    $("s-events").textContent = s.events_total;

    var lb = $("live-badge");
    if (s.running) { lb.className = "badge live"; lb.innerHTML = '<span class="dot"></span> LIVE'; }
    else { lb.className = "badge bad"; lb.textContent = "stopped"; }

    var tg = $("tg-badge");
    if (s.telegram_ready) { tg.className = "badge ok"; tg.textContent = "Telegram ✓"; }
    else { tg.className = "badge muted"; tg.textContent = "Telegram —"; }

    var name = $("src-name");
    if (name) name.textContent = String(s.source).length > 22
      ? String(s.source).slice(0, 21) + "…" : s.source;

    setDet("d-fire", s.checks.fire);
    setDet("d-person", s.checks.person);
    setDet("d-ext", s.checks.extinguisher);

    renderZones(s.zones);
  }

  function setDet(id, on) {
    var el = $(id);
    el.className = "badge " + (on ? "ok" : "muted");
    el.textContent = on ? "active" : "not configured";
  }

  function renderZones(zones) {
    var box = $("zones");
    var ids = Object.keys(zones || {});
    if (!ids.length) {
      box.innerHTML = '<p class="hint">No zones defined. Click "Set zone" below the video.</p>';
      return;
    }
    box.innerHTML = ids.map(function (id) {
      var z = zones[id];
      var alert = z.persistent ? " alert" : "";
      var state = z.fire_active
        ? '<span class="badge bad"><span class="dot"></span> ' +
          String(z.event_type || "fire").toUpperCase() + " · " + z.streak + " fr.</span>"
        : '<span class="badge ok">clear</span>';
      return '<div class="zone-item' + alert + '">' +
        '<div class="zone-head"><span class="zone-title">' +
          '<svg class="icon icon-sm"><use href="#i-target"/></svg> Zone ' + id + "</span>" + state + "</div>" +
        '<div class="cond-row"><span>Observer</span>' + triBadge(z.observer_present) + "</div>" +
        '<div class="cond-row"><span>Extinguisher</span>' + triBadge(z.extinguisher_present) + "</div>" +
      "</div>";
    }).join("");
  }

  // ---------------- permit ----------------
  $("permitBtn").addEventListener("click", async function () {
    var num = $("permit").value.trim();
    if (!num) return toast("Enter a permit number", false);
    var fd = new FormData(); fd.append("permit_number", num);
    var r = await fetch("/api/permit/last", { method: "POST", body: fd });
    if (r.ok) { toast("Permit attached to the latest event"); $("permit").value = ""; }
    else { var d = await r.json().catch(function () { return {}; }); toast(d.detail || "No events yet", false); }
  });

  // ---------------- zone editor ----------------
  var editor = $("editor"), ctx = editor.getContext("2d");
  var editing = false, bgImg = null, pts = [];
  $("editHint").style.display = "none";

  function draw() {
    if (!bgImg) return;
    ctx.drawImage(bgImg, 0, 0, editor.width, editor.height);
    if (!pts.length) return;
    ctx.lineWidth = 3; ctx.strokeStyle = "#ff7a33"; ctx.fillStyle = "rgba(255,122,51,0.15)";
    ctx.beginPath();
    pts.forEach(function (p, i) {
      var px = p[0] * editor.width, py = p[1] * editor.height;
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    if (pts.length >= 3) ctx.closePath();
    ctx.stroke();
    if (pts.length >= 3) ctx.fill();
    pts.forEach(function (p) {
      ctx.fillStyle = "#ffd23f";
      ctx.beginPath(); ctx.arc(p[0] * editor.width, p[1] * editor.height, 5, 0, 6.29); ctx.fill();
    });
  }

  function startEdit() {
    var img = new Image();
    img.onload = function () {
      bgImg = img; editor.width = img.naturalWidth; editor.height = img.naturalHeight; pts = [];
      $("stream").style.display = "none"; editor.style.display = "block";
      $("editControls").style.display = "inline-flex"; $("editHint").style.display = "inline";
      $("editBtn").style.display = "none"; editing = true; draw();
    };
    img.onerror = function () { toast("Frame not ready yet, wait a couple of seconds", false); };
    img.src = "/frame.jpg?t=" + Date.now();
  }
  function stopEdit() {
    editing = false;
    $("stream").style.display = "block"; editor.style.display = "none";
    $("editControls").style.display = "none"; $("editHint").style.display = "none";
    $("editBtn").style.display = "inline-flex";
  }

  editor.addEventListener("click", function (e) {
    if (!editing) return;
    var r = editor.getBoundingClientRect();
    var x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    pts.push([Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))]); draw();
  });
  $("editBtn").addEventListener("click", startEdit);
  $("cancelBtn").addEventListener("click", stopEdit);
  $("undoBtn").addEventListener("click", function () { pts.pop(); draw(); });
  $("clearBtn").addEventListener("click", function () { pts = []; draw(); });
  $("saveZoneBtn").addEventListener("click", async function () {
    if (pts.length < 3) return toast("At least 3 points are required", false);
    var zone = { id: $("zoneId").value.trim() || "A", polygon: pts };
    var r = await fetch("/api/zones", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones: [zone] }),
    });
    if (r.ok) { toast("Zone saved"); stopEdit(); }
    else { var d = await r.json().catch(function () { return {}; }); toast(d.detail || "Could not save", false); }
  });

  // ---------------- start ----------------
  refreshStatus();
  setInterval(refreshStatus, 1500);
})();
