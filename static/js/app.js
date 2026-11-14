/* Ceiba GO: lector de QR (camara o foto) y firma en pantalla */
(function () {
  // ---------- Lector QR: <div class="lector" data-destino="id_input" data-enviar="1">
  document.querySelectorAll(".lector").forEach(function (box) {
    var destino = document.getElementById(box.dataset.destino);
    var btn = box.querySelector("[data-camara]");
    var foto = box.querySelector("[data-foto]");
    var area = box.querySelector(".camara");
    var estado = box.querySelector(".estado");
    var lector = null;
    function listo(texto) {
      destino.value = texto;
      destino.dispatchEvent(new Event("change"));
      if (estado) estado.textContent = "✔ QR leído: " + texto.split("|").slice(1, 3).join(" ");
      if (lector) { lector.stop().catch(function () {}); lector = null; area.innerHTML = ""; }
      if (box.dataset.enviar) destino.form.submit();
    }
    if (typeof Html5Qrcode === "undefined") {
      if (btn) btn.style.display = "none";
      if (foto) foto.closest("label").style.display = "none";
      if (estado) estado.textContent = "Sin lector de cámara: digite el número de la guía (ej. T001-00000001).";
      return;
    }
    if (btn) btn.addEventListener("click", function () {
      if (lector) { lector.stop().catch(function () {}); lector = null; area.innerHTML = ""; return; }
      area.id = area.id || "cam" + Math.random().toString(36).slice(2);
      lector = new Html5Qrcode(area.id);
      lector.start({ facingMode: "environment" }, { fps: 10, qrbox: 230 }, listo, function () {})
        .catch(function () {
          lector = null;
          if (estado) estado.textContent = "La cámara no está disponible (requiere https o localhost). Use 'Leer desde foto' o digite el número.";
        });
    });
    if (foto) foto.addEventListener("change", function () {
      if (!foto.files.length) return;
      area.id = area.id || "cam" + Math.random().toString(36).slice(2);
      new Html5Qrcode(area.id).scanFile(foto.files[0], false).then(listo)
        .catch(function () { if (estado) estado.textContent = "No se encontró un QR en la foto."; });
    });
  });

  // ---------- Firma: <canvas class="firma" data-campo="id_hidden">
  document.querySelectorAll("canvas.firma").forEach(function (cv) {
    var campo = document.getElementById(cv.dataset.campo);
    var ctx = cv.getContext("2d"), dib = false, usado = false;
    function ajustar() { var r = cv.getBoundingClientRect(); cv.width = r.width; cv.height = r.height; ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.strokeStyle = "#16211c"; }
    ajustar();
    function pos(e) { var r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    cv.addEventListener("pointerdown", function (e) { dib = true; cv.setPointerCapture(e.pointerId); var p = pos(e); ctx.beginPath(); ctx.moveTo(p[0], p[1]); });
    cv.addEventListener("pointermove", function (e) { if (!dib) return; var p = pos(e); ctx.lineTo(p[0], p[1]); ctx.stroke(); usado = true; });
    cv.addEventListener("pointerup", function () { dib = false; if (usado) campo.value = cv.toDataURL("image/png"); });
    var limpiar = document.querySelector('[data-limpiar="' + cv.id + '"]');
    if (limpiar) limpiar.addEventListener("click", function () { ctx.clearRect(0, 0, cv.width, cv.height); campo.value = ""; usado = false; });
  });

  // ---------- DNI / RUC: <input data-doc data-doc-nombre="id" data-doc-direccion="id" data-doc-distrito="id"
  //            data-doc-departamento="id" data-doc-tipo="name del radio persona/empresa">
  document.querySelectorAll("input[data-doc]").forEach(function (inp) {
    var aviso = document.createElement("div");
    aviso.className = "small doc-aviso";
    inp.insertAdjacentElement("afterend", aviso);
    var ultimo = "";
    function poner(id, valor, siempre) {
      var el = id && document.getElementById(id);
      if (!el || !valor) return;
      if (siempre || !el.value || el.dataset.auto === "1") {
        el.value = valor; el.dataset.auto = "1";
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    function consultar() {
      var n = inp.value.replace(/\D/g, "");
      if ((n.length !== 8 && n.length !== 11) || n === ultimo) return;
      ultimo = n;
      aviso.style.color = "var(--gris)"; aviso.textContent = "Consultando " + (n.length === 8 ? "RENIEC" : "SUNAT") + "…";
      fetch("/api/documento/?numero=" + n).then(function (r) { return r.json(); }).then(function (d) {
        if (inp.value.replace(/\D/g, "") !== n) return;
        if (!d.ok) { aviso.style.color = "var(--ambar)"; aviso.textContent = d.error; ultimo = ""; return; }
        poner(inp.dataset.docNombre, d.nombre, true);
        poner(inp.dataset.docDireccion, d.direccion);
        poner(inp.dataset.docDistrito, d.distrito);
        poner(inp.dataset.docDepartamento, d.departamento);
        if (inp.dataset.docTipo) {
          var radio = document.querySelector('input[name="' + inp.dataset.docTipo + '"][value="' + (d.tipo === "ruc" ? "empresa" : "persona") + '"]');
          if (radio) radio.checked = true;
        }
        aviso.style.color = d.alerta ? "var(--rojo)" : "var(--verde)";
        aviso.textContent = (d.alerta ? "⚠ " + d.alerta + " · " : "✔ ") + d.nombre;
      }).catch(function () { aviso.textContent = ""; ultimo = ""; });
    }
    inp.addEventListener("input", consultar);
    inp.addEventListener("change", consultar);
  });

  // ---------- Menu en celular (☰)
  var menuBtn = document.querySelector(".menu-btn");
  if (menuBtn) menuBtn.addEventListener("click", function () {
    var top = menuBtn.closest(".top"), abierto = top.classList.toggle("abierto");
    menuBtn.setAttribute("aria-expanded", abierto ? "true" : "false");
    menuBtn.textContent = abierto ? "✕" : "☰";
  });

  // ---------- Copiar link de seguimiento
  document.querySelectorAll("[data-copiar]").forEach(function (b) {
    b.addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(b.dataset.copiar);
      b.textContent = "Copiado ✔";
    });
  });
})();
