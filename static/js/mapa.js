/* Ceiba GO · mapas para marcar el punto exacto de recojo y de entrega (Leaflet + OpenStreetMap).
   - clic en el mapa o arrastrar el marcador: guarda latitud/longitud en los campos ocultos
   - «Buscar»: busca la dirección escrita (Nominatim, solo en Perú) y centra el mapa
   - «Mi ubicación»: usa el GPS del celular o la ubicación del navegador
   - al marcar, completa dirección, distrito y departamento si están vacíos (o si los completó el mapa antes)
   CeibaMapa.ver(elemento, puntos): mapa de solo lectura con recojo, entrega y la línea entre ambos. */
(function () {
  "use strict";
  var LIMA = [-12.0464, -77.0428];
  var TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  var ATRIB = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
  var NOMINATIM = "https://nominatim.openstreetmap.org/";
  // la política de uso de OpenStreetMap exige saber desde qué sitio se piden sus mapas y búsquedas; la página no lo
  // envía por defecto (Referrer-Policy: same-origin), así que aquí se manda solo el dominio, sin la ruta ni datos
  var REFERER = "strict-origin-when-cross-origin";
  var capaOsm = function () { return L.tileLayer(TILES, { maxZoom: 19, attribution: ATRIB, referrerPolicy: REFERER }); };
  var DEPARTAMENTOS = ["AMAZONAS", "ANCASH", "APURIMAC", "AREQUIPA", "AYACUCHO", "CAJAMARCA", "CALLAO", "CUSCO",
    "HUANCAVELICA", "HUANUCO", "ICA", "JUNIN", "LA LIBERTAD", "LAMBAYEQUE", "LIMA", "LORETO", "MADRE DE DIOS",
    "MOQUEGUA", "PASCO", "PIURA", "PUNO", "SAN MARTIN", "TACNA", "TUMBES", "UCAYALI"];
  var COLOR = { origen: "#1F7A52", destino: "#C53B3B" };
  var ETIQUETA = { origen: "A", destino: "B" };

  function icono(lado) {
    return L.divIcon({
      className: "pin-ceiba",
      html: '<div class="pin" style="background:' + COLOR[lado] + '"><span>' + ETIQUETA[lado] + "</span></div>",
      iconSize: [30, 42], iconAnchor: [15, 40], popupAnchor: [0, -36]
    });
  }

  function sinTildes(t) {
    return (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().trim();
  }

  function departamento(dir) {
    var candidatos = [dir.state, dir.region, dir.state_district, dir.province, dir.city];
    for (var i = 0; i < candidatos.length; i++) {
      var t = sinTildes(candidatos[i]).replace(/^(DEPARTAMENTO|REGION|PROVINCIA CONSTITUCIONAL|PROVINCIA) (DE |DEL )?/, "");
      if (t.indexOf("LIMA") === 0) return "LIMA";
      if (t.indexOf("CALLAO") >= 0) return "CALLAO";
      if (DEPARTAMENTOS.indexOf(t) >= 0) return t;
    }
    return "";
  }

  function nominatim(ruta, params) {
    var q = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    return fetch(NOMINATIM + ruta + "?" + q + "&format=jsonv2&accept-language=es",
      { headers: { "Accept": "application/json" }, referrerPolicy: REFERER })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status === 429 ? "demasiadas búsquedas seguidas, espere unos segundos" :
          r.status === 403 ? "el buscador de OpenStreetMap rechazó la consulta" : "el buscador respondió " + r.status);
        return r.json();
      });
  }

  function Selector(lado, form) {
    var self = this;
    this.lado = lado;
    this.form = form;
    this.lat = form.querySelector("[name=" + lado + "_lat]");
    this.lng = form.querySelector("[name=" + lado + "_lng]");
    this.info = document.getElementById("coord-" + lado);
    this.lista = document.getElementById("res-" + lado);
    this.buscador = document.getElementById("buscar-" + lado);
    this.campos = {
      direccion: form.querySelector("[name=" + lado + "_direccion]"),
      distrito: form.querySelector("[name=" + lado + "_distrito]"),
      departamento: form.querySelector("[name=" + lado + "_departamento]"),
      referencia: form.querySelector("[name=" + lado + "_referencia]")
    };
    // lo que escribe la persona no se pisa; lo que puso el mapa sí se actualiza al mover el punto
    Object.keys(this.campos).forEach(function (k) {
      var c = self.campos[k];
      if (c) c.addEventListener("input", function () { c.dataset.mapa = ""; });
    });
    var inicial = this.lat.value && this.lng.value ? [parseFloat(this.lat.value), parseFloat(this.lng.value)] : null;
    this.mapa = L.map("mapa-" + lado, { scrollWheelZoom: false }).setView(inicial || LIMA, inicial ? 17 : 12);
    var capa = capaOsm().addTo(this.mapa), self2 = this, fallos = 0;
    capa.on("tileerror", function () {
      if (++fallos === 3) self2.aviso("⚠ El mapa no se pudo cargar (OpenStreetMap no respondió). Revise su conexión y recargue la página.");
    });
    this.mapa.on("click", function (e) { self.poner(e.latlng, true); });
    this.mapa.on("focus", function () { self.mapa.scrollWheelZoom.enable(); });
    this.marcador = null;
    if (inicial) this.poner(L.latLng(inicial[0], inicial[1]), false);
    document.querySelector("[data-buscar=" + lado + "]").addEventListener("click", function () { self.buscar(); });
    this.buscador.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); self.buscar(); } });
    document.querySelector("[data-ubicacion=" + lado + "]").addEventListener("click", function () { self.miUbicacion(); });
  }

  Selector.prototype.poner = function (latlng, completar) {
    var self = this;
    if (!this.marcador) {
      this.marcador = L.marker(latlng, { draggable: true, icon: icono(this.lado), autoPan: true }).addTo(this.mapa);
      this.marcador.on("dragend", function () { self.poner(self.marcador.getLatLng(), true); });
    } else {
      this.marcador.setLatLng(latlng);
    }
    this.lat.value = latlng.lat.toFixed(6);
    this.lng.value = latlng.lng.toFixed(6);
    this.info.innerHTML = '✔ Punto marcado: <b>' + latlng.lat.toFixed(6) + ", " + latlng.lng.toFixed(6) +
      "</b> · puede arrastrar el marcador para ajustarlo";
    this.info.className = "small mapa-ok";
    var caja = this.info.closest(".mapa-caja");
    if (caja) caja.classList.remove("con-error");
    if (completar) this.completar(latlng);
    document.dispatchEvent(new CustomEvent("ceiba:puntos"));
  };

  Selector.prototype.completar = function (latlng) {
    var self = this;
    nominatim("reverse", { lat: latlng.lat, lon: latlng.lng, zoom: 18, addressdetails: 1 }).then(function (r) {
      var d = r.address || {};
      var calle = [d.road || d.pedestrian || d.footway || d.residential || d.neighbourhood || "", d.house_number || ""]
        .join(" ").trim();
      // en Perú OpenStreetMap guarda el distrito en «city» (Santiago de Surco, Ate, Miraflores…) y la urbanización
      // o el barrio en «suburb» / «neighbourhood» (Monterrico, Vitarte…): esa va a la referencia
      var distrito = d.city || d.town || d.village || d.municipality || d.city_district || d.suburb || "";
      var zona = [d.suburb, d.neighbourhood, d.quarter].filter(function (z) { return z && z !== distrito; })[0] || "";
      self.llenar("direccion", calle);
      self.llenar("distrito", distrito);
      if (zona) self.llenar("referencia", "Urb. / zona: " + zona);
      var dep = departamento(d);
      if (dep) self.llenar("departamento", dep);
    }).catch(function () {  // sin el buscador igual quedan las coordenadas exactas, que es lo importante
      self.info.innerHTML += ' · <span style="color:#81560F">no se pudo leer la dirección de ese punto: complétela a mano</span>';
    });
  };

  Selector.prototype.llenar = function (k, valor) {
    var c = this.campos[k];
    if (!c || !valor) return;
    if (c.value && c.dataset.mapa !== "1") return;  // ya lo escribió la persona
    c.value = valor;
    c.dataset.mapa = "1";
    c.dispatchEvent(new Event("change", { bubbles: true }));
  };

  Selector.prototype.buscar = function () {
    var self = this;
    var texto = (this.buscador.value || "").trim();
    if (!texto) {
      var c = this.campos;
      texto = [c.direccion && c.direccion.value, c.distrito && c.distrito.value,
        c.departamento && c.departamento.selectedOptions[0] && c.departamento.value ? c.departamento.selectedOptions[0].text : ""]
        .filter(Boolean).join(", ");
      this.buscador.value = texto;
    }
    if (texto.length < 3) { this.aviso("Escriba una dirección para buscarla."); return; }
    this.lista.innerHTML = '<div class="res muted">Buscando…</div>';
    nominatim("search", { q: texto, countrycodes: "pe", limit: 5, addressdetails: 1 }).then(function (res) {
      if (!res.length) { self.lista.innerHTML = '<div class="res muted">No se encontró. Pruebe con calle y distrito, o marque el punto en el mapa.</div>'; return; }
      self.lista.innerHTML = "";
      res.forEach(function (r) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "res";
        b.textContent = r.display_name;
        b.addEventListener("click", function () {
          self.lista.innerHTML = "";
          var p = L.latLng(parseFloat(r.lat), parseFloat(r.lon));
          self.mapa.setView(p, 18);
          self.poner(p, true);
        });
        self.lista.appendChild(b);
      });
    }).catch(function (e) {
      self.lista.innerHTML = '<div class="res muted">⚠ No se pudo buscar (' + (e && e.message || "sin conexión") +
        '). Puede marcar el punto directamente en el mapa.</div>';
    });
  };

  Selector.prototype.miUbicacion = function () {
    var self = this;
    if (!navigator.geolocation) { this.aviso("Este navegador no permite obtener la ubicación."); return; }
    this.aviso("Obteniendo su ubicación…");
    navigator.geolocation.getCurrentPosition(function (pos) {
      var p = L.latLng(pos.coords.latitude, pos.coords.longitude);
      self.mapa.setView(p, 18);
      self.poner(p, true);
      if (pos.coords.accuracy > 50) self.info.innerHTML += ' · <span style="color:#81560F">precisión ±' +
        Math.round(pos.coords.accuracy) + " m: revise el punto</span>";
    }, function () { self.aviso("No se pudo obtener la ubicación (permiso denegado o sin GPS)."); },
    { enableHighAccuracy: true, timeout: 15000 });
  };

  Selector.prototype.aviso = function (t) {
    this.info.textContent = t;
    this.info.className = "small muted";
  };

  function distancia(a, b) {
    var r = Math.PI / 180, h = Math.pow(Math.sin((b.lat - a.lat) * r / 2), 2) +
      Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.pow(Math.sin((b.lng - a.lng) * r / 2), 2);
    return 2 * 6371 * Math.asin(Math.sqrt(h));
  }

  window.CeibaMapa = {
    formulario: function (form) {
      if (!window.L) return null;
      var o = new Selector("origen", form), d = new Selector("destino", form);
      var salida = document.getElementById("distancia");
      document.addEventListener("ceiba:puntos", function () {
        if (!salida || !o.marcador || !d.marcador) return;
        salida.innerHTML = "📏 Distancia en línea recta entre recojo y entrega: <b>" +
          distancia(o.marcador.getLatLng(), d.marcador.getLatLng()).toFixed(1) + " km</b>";
      });
      document.dispatchEvent(new CustomEvent("ceiba:puntos"));
      // un error del servidor sobre el punto se ve junto a su mapa
      form.querySelectorAll(".mapa-caja .errorlist").forEach(function (e) { e.closest(".mapa-caja").classList.add("con-error"); });
      return { origen: o, destino: d };
    },
    ver: function (el, puntos) {
      if (!window.L || !el) return;
      var m = L.map(el, { scrollWheelZoom: false });
      capaOsm().addTo(m);
      var capas = [];
      ["origen", "destino"].forEach(function (lado) {
        var p = puntos[lado];
        if (!p) return;
        capas.push(L.marker(p, { icon: icono(lado) }).addTo(m).bindPopup(lado === "origen" ? "Recojo" : "Entrega"));
      });
      if (puntos.origen && puntos.destino) {
        L.polyline([puntos.origen, puntos.destino], { color: "#0F3D2E", weight: 3, dashArray: "6 6" }).addTo(m);
      }
      if (capas.length > 1) m.fitBounds(L.featureGroup(capas).getBounds().pad(0.25));
      else if (capas.length) m.setView(capas[0].getLatLng(), 16);
    }
  };
})();
