"""Prueba de punta a punta: los 14 pasos del flujo, de la direccion inicial a la final, por HTTP."""
import base64
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Guia, Pedido, SyncERP

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
FIRMA = "data:image/png;base64," + base64.b64encode(PNG).decode()


def foto():
    return SimpleUploadedFile("evidencia.png", PNG, content_type="image/png")


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class FlujoCompleto(TestCase):
    def setUp(self):
        call_command("seed_demo", verbosity=0)

    def entrar(self, u):
        self.client.logout()
        self.assertTrue(self.client.login(username=u, password="ceiba2026"))

    # Ate (Lima) → Cercado de Arequipa
    PUNTOS = {"origen_lat": "-12.026512", "origen_lng": "-76.917890", "destino_lat": "-16.398822", "destino_lng": "-71.536963"}

    def crear(self, usuario, origen="LIMA", destino="AREQUIPA", moneda="PEN", puntos=None):
        self.entrar(usuario)
        r = self.client.post("/app/nuevo/", {
            "origen_direccion": "Av. Industrial 100", "origen_distrito": "Ate", "origen_departamento": origen,
            "origen_contacto": "Almacén", "origen_telefono": "999111222",
            "destino_direccion": "Calle Mercaderes 300", "destino_distrito": "Cercado", "destino_departamento": destino,
            "destinatario_nombre": "Juan Pérez", "destinatario_documento": "40123456", "destinatario_telefono": "988777666",
            "moneda": moneda, **(puntos if puntos is not None else self.PUNTOS),
            "descripcion": ["Mayólicas", "Tubos PVC"], "cantidad": ["4", "2"], "peso_kg": ["20", "3"],
            "largo_cm": ["60", "300"], "ancho_cm": ["40", "20"], "alto_cm": ["10", "20"],
            "valor_declarado": ["400", "100"], "fragil": ["1", "0"],
        })
        self.assertEqual(r.status_code, 302, r.content[:3000])
        return Pedido.objects.latest("creado")

    def _post_sin_paquetes_validos(self, puntos):
        self.entrar("cliente")
        datos = {"origen_direccion": "Av. Industrial 100", "origen_distrito": "Ate", "origen_departamento": "LIMA",
                 "origen_contacto": "Almacén", "origen_telefono": "999111222", "destino_direccion": "Calle 1",
                 "destino_distrito": "Cercado", "destino_departamento": "AREQUIPA", "destinatario_nombre": "Juan Pérez",
                 "destinatario_documento": "40123456", "destinatario_telefono": "988777666", "moneda": "PEN",
                 "descripcion": ["Caja"], "cantidad": ["1"], "peso_kg": ["2"], "largo_cm": [""], "ancho_cm": [""],
                 "alto_cm": [""], "valor_declarado": [""], "fragil": ["0"], **puntos}
        return self.client.post("/app/nuevo/", datos)

    def test_puntos_del_mapa_obligatorios_y_validos(self):
        antes = Pedido.objects.count()
        r = self._post_sin_paquetes_validos({})
        self.assertContains(r, "Marque en el mapa el punto exacto de recojo.")
        self.assertContains(r, "Marque en el mapa el punto exacto de entrega.")
        r = self._post_sin_paquetes_validos({**self.PUNTOS, "destino_lat": "40.4168", "destino_lng": "-3.7038"})  # Madrid
        self.assertContains(r, "debe estar en Perú")
        r = self._post_sin_paquetes_validos({**self.PUNTOS, "destino_lat": self.PUNTOS["origen_lat"],
                                             "destino_lng": self.PUNTOS["origen_lng"]})
        self.assertContains(r, "es el mismo que el de recojo")
        self.assertEqual(Pedido.objects.count(), antes)

    def test_pedido_guarda_puntos_y_los_muestra(self):
        p = self.crear("cliente")
        self.assertEqual(str(p.origen_lat), "-12.026512")
        self.assertEqual(str(p.destino_lng), "-71.536963")
        r = self.client.get(f"/app/pedido/{p.codigo}/")
        self.assertContains(r, "mapa-pedido")
        self.assertContains(r, "destination=-16.398822,-71.536963")
        r = self.client.get("/app/nuevo/")
        self.assertContains(r, 'id="mapa-origen"')
        self.assertContains(r, 'id="mapa-destino"')

    def test_cotizacion_api(self):
        r = self.client.post("/api/cotizar/", json.dumps({"origen": "LIMA", "destino": "CUSCO", "paquetes": [
            {"cantidad": 1, "peso_kg": 2, "largo_cm": 100, "ancho_cm": 100, "alto_cm": 50}]}), content_type="application/json")
        c = r.json()
        self.assertTrue(c["ok"])
        self.assertEqual(c["cobra_por"], "volumen")  # 0.5 m3 x 250 = 125 kg > 2 kg
        self.assertEqual(c["peso_facturable_kg"], "125.00")

    def test_flujo_interprovincial_completo(self):
        p = self.crear("empresa")                                        # pasos 1-3
        self.assertEqual(p.estado, "creado")
        self.assertEqual(p.peso_facturable_kg, max(p.peso_real_kg, p.peso_facturable_kg))
        r = self.client.post(f"/app/pedido/{p.codigo}/pagar/", {"metodo": "credito", "accion": "pagar"})  # paso 4
        p.refresh_from_db()
        self.assertEqual(p.comprobante.tipo, "factura")                   # paso 5
        gre_r = p.guia("GRE-R")
        self.assertEqual(gre_r.sunat_estado, "aceptada")
        self.assertEqual(p.estado, "recojo_asignado")                    # paso 7 (auto, agente Lima)
        self.assertTrue(SyncERP.objects.filter(pedido=p, tipo="cxc").exists())  # paso 6
        self.assertEqual(self.client.get(f"/doc/{p.codigo}/guia/GRE-R/").status_code, 200)
        self.assertEqual(self.client.get(f"/doc/{p.codigo}/comprobante/").status_code, 200)

        # paso 8: el agente de origen escanea un QR equivocado -> no avanza
        self.entrar("agente_lima")
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "recojo", "qr": "T001-99999", "foto": foto()})
        p.refresh_from_db()
        self.assertEqual(p.estado, "recojo_asignado")
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "recojo", "qr": gre_r.qr_texto, "peso_real": "86.5", "foto": foto()})
        p.refresh_from_db()
        self.assertEqual(p.estado, "recogido")

        # paso 9: operaciones entrega a transporte -> GRE-T
        self.entrar("operador")
        self.assertEqual(self.client.get("/ops/").status_code, 200)
        t = p.tramos.get(tipo="linea")
        from .models import Transportista
        tr = Transportista.objects.get(ruc="20498765432")
        self.client.post(f"/ops/pedido/{p.codigo}/", {"accion": "transporte", "transportista": tr.id, "registro": "api"})
        p.refresh_from_db()
        self.assertEqual(p.estado, "en_transito")
        gre_t = p.guia("GRE-T")
        self.assertIsNotNone(gre_t)

        # paso 10: QR en ruta
        self.entrar("transporte")
        self.client.post("/transporte/", {"codigo": p.codigo, "qr": gre_t.qr_texto, "ubicacion": "Terminal Nazca"})
        self.assertTrue(p.eventos.filter(descripcion__contains="Nazca").exists())

        # paso 11: recepcion en destino (agente Arequipa)
        self.entrar("agente_arequipa")
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "recepcion", "qr": gre_t.numero_completo, "almacenar": "on", "ubicacion": "Rack B-2"})
        p.refresh_from_db()
        self.assertEqual(p.estado, "en_destino")

        # excepcion: fallido -> reprogramado -> entrega
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "reparto"})
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "fallido", "motivo": "Destinatario ausente"})
        p.refresh_from_db()
        self.assertEqual(p.estado, "fallido")
        self.entrar("operador")
        self.client.post(f"/ops/pedido/{p.codigo}/", {"accion": "reprogramar", "fecha": "2030-01-10"})
        p.refresh_from_db()
        self.assertEqual(p.estado, "reprogramado")

        # paso 12-14: entrega con POD, cierre y conciliacion
        self.entrar("agente_arequipa")
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "reparto"})
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "entrega", "qr": gre_r.qr_texto, "receptor_nombre": "Juan Pérez",
                                                         "receptor_documento": "40123456", "firma": FIRMA})
        p.refresh_from_db()
        self.assertEqual(p.estado, "entregado")
        self.assertIsNotNone(p.cerrado_en)
        tipos = set(SyncERP.objects.filter(pedido=p).values_list("tipo", flat=True))
        self.assertTrue({"venta", "gre", "cxc", "costo", "almacen", "cierre"} <= tipos)

        # portal de seguimiento sin cuenta
        self.client.logout()
        r = self.client.get(f"/s/{p.codigo}/")
        self.assertContains(r, "Entregado")

        # cobranza del credito
        self.entrar("operador")
        pago = p.pagos.get()
        self.client.post(f"/ops/pedido/{p.codigo}/", {"accion": "cobranza", "pago": pago.id, "referencia": "DEP-123"})
        pago.refresh_from_db()
        self.assertEqual(pago.estado, "cobrado")
        self.assertEqual(self.client.get("/ops/erp/").status_code, 200)

    def test_mismo_departamento_y_anulacion(self):
        p = self.crear("cliente", "LIMA", "LIMA", "USD")
        self.client.post(f"/app/pedido/{p.codigo}/pagar/", {"metodo": "yape", "accion": "pagar"})
        p.refresh_from_db()
        self.assertEqual(p.comprobante.tipo, "boleta")
        self.assertEqual(p.comprobante.moneda, "USD")
        self.entrar("agente_lima")
        self.client.post(f"/agente/pedido/{p.codigo}/", {"accion": "recojo", "qr": p.guia("GRE-R").qr_texto, "foto": foto()})
        p.refresh_from_db()
        self.assertEqual(p.estado, "en_destino")  # sin tramo interprovincial

        p2 = self.crear("cliente")
        self.client.post(f"/app/pedido/{p2.codigo}/pagar/", {"metodo": "tarjeta", "accion": "pagar"})
        self.entrar("operador")
        self.client.post(f"/ops/pedido/{p2.codigo}/", {"accion": "anular", "motivo": "Error en dirección"})
        p2.refresh_from_db()
        self.assertEqual(p2.estado, "anulado")
        self.assertEqual(p2.comprobante.sunat_estado, "baja")
        self.assertFalse(Guia.objects.filter(pedido=p2).exclude(sunat_estado="baja").exists())

    def test_consulta_dni_ruc(self):
        from unittest import mock

        from django.core.cache import cache

        from .models import Configuracion
        cache.clear()
        r = self.client.get("/api/documento/?numero=40123456").json()
        self.assertFalse(r["ok"])  # sin token
        Configuracion.objects.filter(pk=1).update(token_decolecta="x")
        dni = {"first_name": "JUAN CARLOS", "first_last_name": "PEREZ", "second_last_name": "QUISPE", "document_number": "40123456"}
        ruc = {"razon_social": "CONSTRUCTORA ANDINA S.A.C.", "estado": "ACTIVO", "condicion": "NO HABIDO",
               "direccion": "AV. JAVIER PRADO 1450", "distrito": "SAN ISIDRO", "departamento": "LIMA"}
        with mock.patch("envios.padron._pedir", side_effect=[dni, ruc]):
            r = self.client.get("/api/documento/?numero=40123456").json()
            self.assertEqual(r["nombre"], "Juan Carlos Perez Quispe")
            r = self.client.get("/api/documento/?numero=20100070970").json()
            self.assertEqual((r["tipo"], r["departamento"], r["alerta"]), ("ruc", "LIMA", "RUC NO HABIDO en SUNAT"))
        self.assertFalse(self.client.get("/api/documento/?numero=20100070971").json()["ok"])  # digito verificador

    def test_credito_solo_empresas(self):
        p = self.crear("cliente")
        self.client.post(f"/app/pedido/{p.codigo}/pagar/", {"metodo": "credito", "accion": "pagar"})
        p.refresh_from_db()
        self.assertEqual(p.estado, "creado")
