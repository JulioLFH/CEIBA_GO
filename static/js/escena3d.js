/* Ceiba GO · escena 3D de la portada: el camion recoge el paquete en la Casa A y lo entrega en la Casa B.
   Three.js r128 (cdnjs). Se pausa cuando no esta a la vista y respeta "reducir movimiento". */
(function () {
  var cont = document.getElementById("escena3d");
  if (!cont) return;
  if (typeof THREE === "undefined") { cont.closest(".escena").classList.add("sin3d"); return; }

  var C = { selva: 0x0f3d2e, hoja: 0x1f7a52, hojaClara: 0x2e9e66, sol: 0xe3a72f, bruma: 0xf4f6f2,
            carton: 0xc8955a, cinta: 0xe9d3a6, asfalto: 0x3b4743, pasto: 0xcfe3d2 };

  // ---------- render, escena y camara
  var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  cont.appendChild(renderer.domElement);

  var escena = new THREE.Scene();
  escena.fog = new THREE.Fog(0xeaf2ec, 34, 70);
  var camara = new THREE.PerspectiveCamera(38, 2, 0.1, 200);

  escena.add(new THREE.HemisphereLight(0xffffff, 0x8fb79c, 0.75));
  var sol = new THREE.DirectionalLight(0xfff3dc, 0.9);
  sol.position.set(-12, 22, 14);
  sol.castShadow = true;
  sol.shadow.mapSize.set(2048, 2048);
  var sc = sol.shadow.camera; sc.left = -26; sc.right = 26; sc.top = 16; sc.bottom = -16; sc.near = 1; sc.far = 70;
  escena.add(sol);

  function mat(color, extra) { return new THREE.MeshStandardMaterial(Object.assign({ color: color, roughness: 0.8, metalness: 0.05 }, extra || {})); }
  function caja(w, h, d, color, extra) {
    var m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), Array.isArray(color) ? color : mat(color, extra));
    m.castShadow = true; m.receiveShadow = true; return m;
  }
  function textura(dibujar, w, h) {
    var c = document.createElement("canvas"); c.width = w; c.height = h;
    dibujar(c.getContext("2d"), w, h);
    var t = new THREE.CanvasTexture(c); t.anisotropy = 4; return t;
  }
  function cartel(texto, fondo, colorTxt, ancho) {
    var t = textura(function (g, w, h) {
      g.fillStyle = fondo; g.beginPath();
      if (g.roundRect) g.roundRect(4, 4, w - 8, h - 8, 34); else g.rect(4, 4, w - 8, h - 8);
      g.fill();
      g.fillStyle = colorTxt; g.font = "700 64px Sora, Segoe UI, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(texto, w / 2, h / 2 + 4);
    }, 512, 128);
    var s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthTest: false }));
    s.scale.set(ancho || 4.4, (ancho || 4.4) / 4, 1); s.renderOrder = 10; return s;
  }

  // ---------- suelo, pista y lineas
  var suelo = new THREE.Mesh(new THREE.PlaneGeometry(140, 80), mat(C.pasto));
  suelo.rotation.x = -Math.PI / 2; suelo.receiveShadow = true; escena.add(suelo);
  var pista = new THREE.Mesh(new THREE.PlaneGeometry(140, 3.6), mat(C.asfalto, { roughness: 0.95 }));
  pista.rotation.x = -Math.PI / 2; pista.position.set(0, 0.01, 1.2); pista.receiveShadow = true; escena.add(pista);
  for (var i = -34; i <= 34; i += 3) {
    var l = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.14), mat(0xffffff, { roughness: 0.5 }));
    l.rotation.x = -Math.PI / 2; l.position.set(i, 0.02, 1.2); escena.add(l);
  }
  [-1, 1].forEach(function (lado) { // veredas hacia cada casa
    var v = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 2.6), mat(0xe6dcc8));
    v.rotation.x = -Math.PI / 2; v.position.set(lado * 11, 0.015, -1.3); v.receiveShadow = true; escena.add(v);
  });

  // ---------- casas
  function casa(x, colorMuro, colorTecho, nombre) {
    var g = new THREE.Group();
    var muro = caja(4.4, 3, 3.6, colorMuro); muro.position.y = 1.5; g.add(muro);
    var techo = new THREE.Mesh(new THREE.ConeGeometry(3.6, 2, 4), mat(colorTecho));
    techo.rotation.y = Math.PI / 4; techo.position.y = 4; techo.scale.set(1, 1, 0.85); techo.castShadow = true; g.add(techo);
    var puerta = caja(0.95, 1.7, 0.1, C.selva); puerta.position.set(0, 0.85, 1.82); g.add(puerta);
    var manija = caja(0.08, 0.08, 0.06, C.sol); manija.position.set(0.3, 0.85, 1.9); g.add(manija);
    [-1.35, 1.35].forEach(function (wx) {
      var v = caja(0.85, 0.75, 0.08, 0xbfe3f2, { roughness: 0.2, metalness: 0.2 }); v.position.set(wx, 1.9, 1.82); g.add(v);
      var marco = caja(0.95, 0.1, 0.12, 0xffffff); marco.position.set(wx, 1.48, 1.84); g.add(marco);
    });
    var chimenea = caja(0.5, 1.2, 0.5, 0x9a6b4f); chimenea.position.set(1.1, 4.3, -0.4); g.add(chimenea);
    var rotulo = cartel(nombre, "#0F3D2E", "#F4F6F2", 2.8); rotulo.position.set(0, 5.5, 1.2); g.add(rotulo);
    g.position.set(x, 0, -4.4);
    escena.add(g);
    return g;
  }
  casa(-11, 0xf6efe2, 0xb5533c, "Casa A");
  casa(11, 0xe8f1ea, C.hoja, "Casa B");

  // ---------- arboles tipo ceiba (copa en cupula)
  function ceiba(x, z, s) {
    var g = new THREE.Group();
    var tronco = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.3, 2.2, 8), mat(C.selva)); tronco.position.y = 1.1; tronco.castShadow = true; g.add(tronco);
    var copa = new THREE.Mesh(new THREE.SphereGeometry(1.5, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat(C.hojaClara));
    copa.position.y = 2.1; copa.scale.set(1.25, 0.8, 1.25); copa.castShadow = true; g.add(copa);
    var flor = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 8), mat(C.sol, { emissive: 0x6b4a08, emissiveIntensity: 0.25 }));
    flor.position.y = 3.35; g.add(flor);
    g.position.set(x, 0, z); g.scale.setScalar(s); escena.add(g);
  }
  [[-17, -5, 1.1], [-5.5, -6.5, 0.9], [0, -8.5, 1.2], [5.5, -6, 0.85], [17, -5.5, 1.1], [-19, 6.5, 0.8], [19, 7, 0.9]]
    .forEach(function (a) { ceiba(a[0], a[1], a[2]); });

  // ---------- camion Ceiba GO
  var camion = new THREE.Group();
  var ladoCarga = textura(function (g, w, h) {
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#1F7A52"; g.fillRect(0, h * 0.78, w, h * 0.22);
    g.fillStyle = "#E3A72F"; g.fillRect(0, h * 0.72, w, h * 0.06);
    g.fillStyle = "#0F3D2E"; g.font = "700 120px Sora, Segoe UI, sans-serif"; g.textBaseline = "middle"; g.textAlign = "center";
    g.fillText("ceiba", w * 0.42, h * 0.38);
    g.fillStyle = "#1F7A52"; g.fillText("GO", w * 0.78, h * 0.38);
  }, 1024, 400);
  var blanco = mat(0xffffff);
  var mCarga = [blanco, blanco, blanco, blanco, mat(0xffffff, { map: ladoCarga }), mat(0xffffff, { map: ladoCarga })];
  var carga = caja(3.6, 2.1, 2, mCarga); carga.position.set(-0.7, 1.55, 0); camion.add(carga);
  var cabina = caja(1.5, 1.6, 1.95, C.selva); cabina.position.set(1.95, 1.3, 0); camion.add(cabina);
  var vidrio = caja(0.08, 0.7, 1.7, 0x9fd0e6, { roughness: 0.1, metalness: 0.4 }); vidrio.position.set(2.72, 1.6, 0); camion.add(vidrio);
  [-1, 1].forEach(function (z) {
    var ventana = caja(0.75, 0.55, 0.06, 0x9fd0e6, { roughness: 0.1, metalness: 0.4 }); ventana.position.set(2.05, 1.62, z * 0.99); camion.add(ventana);
  });
  var chasis = caja(5.2, 0.35, 1.8, 0x23302b); chasis.position.set(0.2, 0.55, 0); camion.add(chasis);
  var faro = caja(0.06, 0.25, 0.35, 0xfff6c4, { emissive: 0xffe58a, emissiveIntensity: 0.8 }); faro.position.set(2.72, 0.95, 0.65); camion.add(faro);
  var faro2 = faro.clone(); faro2.position.z = -0.65; camion.add(faro2);
  var ruedas = [];
  [[1.9, 1], [1.9, -1], [-0.2, 1], [-0.2, -1], [-1.6, 1], [-1.6, -1]].forEach(function (p) {
    var r = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.35, 18), mat(0x1b1f1d));
    r.rotation.x = Math.PI / 2; r.position.set(p[0], 0.45, p[1] * 0.95); r.castShadow = true;
    var aro = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.37, 10), mat(0xc9d1cc, { metalness: 0.5 }));
    r.add(aro); camion.add(r); ruedas.push(r);
  });
  camion.scale.setScalar(1.25);
  camion.position.set(-30, 0, 1.2);
  escena.add(camion);

  // ---------- paquete con cinta y etiqueta QR
  var etiqueta = textura(function (g, w, h) {
    g.fillStyle = "#c8955a"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#ffffff"; g.fillRect(w * 0.18, h * 0.18, w * 0.64, h * 0.64);
    g.fillStyle = "#0F3D2E";
    for (var y = 0; y < 7; y++) for (var x = 0; x < 7; x++) if ((x * 7 + y * 3 + x * y) % 3 !== 0 || x % 6 === 0 && y % 6 === 0)
      g.fillRect(w * 0.24 + x * w * 0.075, h * 0.24 + y * h * 0.075, w * 0.07, h * 0.07);
  }, 128, 128);
  var carton = mat(C.carton);
  var paquete = caja(0.9, 0.75, 0.9, [carton, carton, carton, carton, mat(0xffffff, { map: etiqueta }), carton]);
  var cinta = caja(0.92, 0.77, 0.16, C.cinta); paquete.add(cinta);
  escena.add(paquete);

  // ---------- marcadores: pin de recojo y check de entregado
  var check = cartel("✓ Entregado", "#1F7A52", "#ffffff", 4.2); check.position.set(11, 8, -4.4); escena.add(check);
  var pin = new THREE.Group();
  var pinBola = new THREE.Mesh(new THREE.SphereGeometry(0.35, 16, 12), mat(C.sol, { emissive: 0x7a5200, emissiveIntensity: 0.35 }));
  pinBola.position.y = 0.55; pin.add(pinBola);
  var pinPunta = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.55, 12), mat(C.sol)); pinPunta.rotation.x = Math.PI; pinPunta.position.y = 0.15; pin.add(pinPunta);
  escena.add(pin);

  // ---------- linea de tiempo (segundos)
  var CICLO = 15;
  var PUERTA_A = new THREE.Vector3(-11, 0.38, -2.1), PUERTA_B = new THREE.Vector3(11, 0.38, -2.1);
  var EN_CAMION = new THREE.Vector3(-1.2, 0.98, 0); // relativo al camion, dentro de la carga (asoma por arriba)
  var mensajes = [
    [0.0, "Recogemos en la puerta de la Casa A"],
    [3.4, "Traslado seguro con guía electrónica SUNAT"],
    [6.0, "Con nosotros tu envío es rápido"],
    [9.0, "Entregado en la Casa B con prueba de entrega"],
    [12.5, "Ceiba GO · de tu dirección a la dirección final"],
  ];
  var cartelHtml = cont.parentNode.querySelector(".escena-msg");
  var msgActual = -1;

  function suave(t) { t = Math.min(Math.max(t, 0), 1); return t * t * (3 - 2 * t); }
  function tramo(t, a, b) { return suave((t - a) / (b - a)); }
  function arco(desde, hasta, k, alto) {
    var p = desde.clone().lerp(hasta, k); p.y += Math.sin(k * Math.PI) * alto; return p;
  }
  var tmp = new THREE.Vector3();

  function pose(t) {
    // camion: llega a A (0-2.2), espera, viaja A->B (4.2-8.4), espera, sale (10.8-13)
    var x;
    if (t < 2.2) x = -30 + 19 * tramo(t, 0, 2.2);
    else if (t < 4.2) x = -11;
    else if (t < 8.4) x = -11 + 22 * tramo(t, 4.2, 8.4);
    else if (t < 10.8) x = 11;
    else x = 11 + 22 * tramo(t, 10.8, 13);
    var prev = camion.position.x;
    camion.position.x = x;
    var mov = x - prev;
    ruedas.forEach(function (r) { r.rotation.y -= mov / 0.45; });
    camion.position.y = Math.abs(mov) > 0.001 ? Math.abs(Math.sin(t * 24)) * 0.04 : 0;
    camion.rotation.z = Math.abs(mov) > 0.01 ? -Math.sign(mov) * 0.01 : 0;

    // paquete: espera en A, sube al camion (2.4-3.8), viaja, baja en B (8.6-10.2)
    camion.updateMatrixWorld();
    var enCamion = camion.localToWorld(tmp.copy(EN_CAMION));
    if (t < 2.4) paquete.position.copy(PUERTA_A);
    else if (t < 3.8) paquete.position.copy(arco(PUERTA_A, enCamion, tramo(t, 2.4, 3.8), 1.6));
    else if (t < 8.6) paquete.position.copy(enCamion);
    else if (t < 10.2) paquete.position.copy(arco(enCamion, PUERTA_B, tramo(t, 8.6, 10.2), 1.6));
    else paquete.position.copy(PUERTA_B);
    paquete.rotation.y = (t > 2.4 && t < 3.8) || (t > 8.6 && t < 10.2) ? t * 3 : 0;
    var visible = t < 14.2;
    paquete.visible = visible;

    // pin de recojo sobre el paquete en A; check en B al entregar
    pin.visible = t < 2.6;
    pin.position.set(PUERTA_A.x, 1.2 + Math.sin(t * 5) * 0.15, PUERTA_A.z);
    var k = tramo(t, 10.2, 10.8) * (1 - tramo(t, 14, 14.6));
    check.material.opacity = k; check.position.set(11, 2.2 + k * 0.6, -1.4);

    // mensaje en pantalla
    var idx = 0;
    for (var i = 0; i < mensajes.length; i++) if (t >= mensajes[i][0]) idx = i;
    if (idx !== msgActual && cartelHtml) {
      msgActual = idx;
      cartelHtml.classList.remove("ver"); void cartelHtml.offsetWidth;
      cartelHtml.textContent = mensajes[idx][1]; cartelHtml.classList.add("ver");
    }

    // camara: sigue al camion; en pantallas angostas lo sigue de cerca para que se vea grande
    var e = encuadre;
    var cx = THREE.MathUtils.clamp(camion.position.x * e.sigue, -e.limite, e.limite);
    camara.position.set(cx + Math.sin(t * 0.4) * e.vaiven, e.alto, e.dist);
    camara.lookAt(cx * 0.9, 1.2, -1.5);
  }

  // ---------- encuadre segun el ancho (escritorio, tablet, celular), bucle y pausa fuera de vista
  var encuadre = { fov: 38, sigue: 0.35, limite: 5, alto: 9.5, dist: 17.5, vaiven: 1.2 };
  function ajustar() {
    var w = cont.clientWidth, h = cont.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    var a = w / h;
    if (a >= 2) encuadre = { fov: 38, sigue: 0.35, limite: 5, alto: 9.5, dist: 17.5, vaiven: 1.2 };       // escritorio
    else if (a >= 1.35) encuadre = { fov: 42, sigue: 0.6, limite: 8, alto: 9, dist: 16.5, vaiven: 0.8 };  // tablet
    else encuadre = { fov: 50, sigue: 0.95, limite: 11, alto: 8, dist: 14.5, vaiven: 0.4 };               // celular
    camara.aspect = a;
    camara.fov = encuadre.fov;
    camara.updateProjectionMatrix();
  }
  if ("ResizeObserver" in window) new ResizeObserver(ajustar).observe(cont);
  else window.addEventListener("resize", ajustar);
  ajustar();

  // La animacion es el contenido de la seccion: se reproduce siempre (Windows Server/RDP suele traer
  // "reducir movimiento" activado por defecto y la escena quedaba congelada).
  var quieto = false;
  var visibleEnPantalla = true, inicio = performance.now();
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (e) { visibleEnPantalla = e[0].isIntersecting; }).observe(cont);
  }
  function cuadro(ahora) {
    if (quieto) { pose(6.3); renderer.render(escena, camara); return; }
    if (visibleEnPantalla && !document.hidden) {
      pose(((ahora - inicio) / 1000) % CICLO);
      renderer.render(escena, camara);
    }
    requestAnimationFrame(cuadro);
  }
  requestAnimationFrame(cuadro);
})();
