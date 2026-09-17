// FireWatch — логика «Мониторинга»: статус, зоны, детекторы, редактор зоны, наряд.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var toast = window.fwToast;

  // бейдж для трёх-состояния: true / false / null
  function triBadge(v) {
    if (v === true) return '<span class="badge ok">да</span>';
    if (v === false) return '<span class="badge bad">нет</span>';
    return '<span class="badge muted">н/д</span>';
  }

  // ---------------- статус ----------------
  async function refreshStatus() {
    var s;
    try { s = await (await fetch("/api/status")).json(); }
    catch (e) { return; }

    $("s-frames").textContent = s.frames_processed;
    $("s-events").textContent = s.events_total;

    var lb = $("live-badge");
    if (s.running) { lb.className = "badge live"; lb.innerHTML = '<span class="dot"></span> LIVE'; }
    else { lb.className = "badge bad"; lb.textContent = "остановлен"; }

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
    el.textContent = on ? "активен" : "не настроен";
  }

  function renderZones(zones) {
    var box = $("zones");
    var ids = Object.keys(zones || {});
    if (!ids.length) {
      box.innerHTML = '<p class="hint">Зоны не заданы. Нажмите «Задать зону» под видео.</p>';
      return;
    }
    box.innerHTML = ids.map(function (id) {
      var z = zones[id];
      var alert = z.persistent ? " alert" : "";
      var state = z.fire_active
        ? '<span class="badge bad"><span class="dot"></span> ' +
          String(z.event_type || "огонь").toUpperCase() + " · " + z.streak + " к.</span>"
        : '<span class="badge ok">спокойно</span>';
      return '<div class="zone-item' + alert + '">' +
        '<div class="zone-head"><span class="zone-title">' +
          '<svg class="icon icon-sm"><use href="#i-target"/></svg> Зона ' + id + "</span>" + state + "</div>" +
        '<div class="cond-row"><span>Наблюдающий</span>' + triBadge(z.observer_present) + "</div>" +
        '<div class="cond-row"><span>Огнетушитель</span>' + triBadge(z.extinguisher_present) + "</div>" +
      "</div>";
    }).join("");
  }

  // ---------------- наряд ----------------
  $("permitBtn").addEventListener("click", async function () {
    var num = $("permit").value.trim();
    if (!num) return toast("Введите номер наряда", false);
    var fd = new FormData(); fd.append("permit_number", num);
    var r = await fetch("/api/permit/last", { method: "POST", body: fd });
    if (r.ok) { toast("Наряд привязан к последнему событию"); $("permit").value = ""; }
    else { var d = await r.json().catch(function () { return {}; }); toast(d.detail || "Событий пока нет", false); }
  });

  // ---------------- редактор зоны ----------------
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
    img.onerror = function () { toast("Кадр ещё не готов, подождите пару секунд", false); };
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
    if (pts.length < 3) return toast("Нужно минимум 3 точки", false);
    var zone = { id: $("zoneId").value.trim() || "A", polygon: pts };
    var r = await fetch("/api/zones", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zones: [zone] }),
    });
    if (r.ok) { toast("Зона сохранена"); stopEdit(); }
    else { var d = await r.json().catch(function () { return {}; }); toast(d.detail || "Не удалось сохранить", false); }
  });

  // ---------------- старт ----------------
  refreshStatus();
  setInterval(refreshStatus, 1500);
})();
