"""Operador logístico (Go Just In Time) en Ceiba GO: solicitudes por correo y WhatsApp del remitente autorizado, guías
del cliente desde su XML, recojo por QR, tablero por destino, guía consolidada con sus relacionadas (SUNAT simulado),
llegada con foto y guía del último tramo, entrega con foto y DNI, bitácora, app del celular, permisos y costo por
guía."""
import hashlib
import hmac
import io
import json
import shutil
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal as D
from email.message import EmailMessage

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from envios.models import Agente, Configuracion, Cuenta, Transportista

from . import servicios as srv
from .canales import correo_a_solicitud, whatsapp_a_solicitud
from .lector_qr import interpretar
from .models import (BitacoraEstado, ClienteLogistico, ConfigOperador, Consolidado, CostoTransporte, EstadoLogistico,
                     GuiaCliente, GuiaGo, MensajeEntrante, RemitenteAutorizado, SolicitudRecojo)
from .xml_guias import ErrorXML, leer

RUC_CLIENTE = '20512345678'
MEDIA = tempfile.mkdtemp(prefix='ceibago_operador_')


def xml_guia(serie='EG01', numero=1, ruc=RUC_CLIENTE, dest_doc='20100070970', dest_nombre='Destino Arequipa SAC',
             ubigeo='040101', direccion='Av. Ejército 100, Arequipa', peso='12.50', bultos=2):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<cbc:ID>{serie}-{numero}</cbc:ID><cbc:IssueDate>2026-11-18</cbc:IssueDate>
<cbc:DespatchAdviceTypeCode>09</cbc:DespatchAdviceTypeCode>
<cac:DespatchSupplierParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{ruc}</cbc:ID>
</cac:PartyIdentification><cac:PartyLegalEntity><cbc:RegistrationName>Cliente Logístico SAC</cbc:RegistrationName>
</cac:PartyLegalEntity></cac:Party></cac:DespatchSupplierParty>
<cac:DeliveryCustomerParty><cac:Party><cac:PartyIdentification><cbc:ID schemeID="6">{dest_doc}</cbc:ID>
</cac:PartyIdentification><cac:PartyLegalEntity><cbc:RegistrationName>{dest_nombre}</cbc:RegistrationName>
</cac:PartyLegalEntity></cac:Party></cac:DeliveryCustomerParty>
<cac:Shipment><cbc:ID>SUNAT_Envio</cbc:ID><cbc:HandlingCode>01</cbc:HandlingCode>
<cbc:GrossWeightMeasure unitCode="KGM">{peso}</cbc:GrossWeightMeasure>
<cbc:TotalTransportHandlingUnitQuantity>{bultos}</cbc:TotalTransportHandlingUnitQuantity>
<cac:ShipmentStage><cac:TransitPeriod><cbc:StartDate>2026-11-18</cbc:StartDate></cac:TransitPeriod></cac:ShipmentStage>
<cac:Delivery><cac:DeliveryAddress><cbc:ID>{ubigeo}</cbc:ID><cac:AddressLine><cbc:Line>{direccion}</cbc:Line>
</cac:AddressLine></cac:DeliveryAddress><cac:Despatch><cac:DespatchAddress><cbc:ID>150101</cbc:ID><cac:AddressLine>
<cbc:Line>Av. Argentina 123, Lima</cbc:Line></cac:AddressLine></cac:DespatchAddress></cac:Despatch></cac:Delivery>
</cac:Shipment>
<cac:DespatchLine><cbc:ID>1</cbc:ID><cbc:DeliveredQuantity unitCode="NIU">2</cbc:DeliveredQuantity>
<cac:Item><cbc:Description>Cajas de repuestos</cbc:Description></cac:Item></cac:DespatchLine>
</DespatchAdvice>'''.encode('utf-8')


def foto(nombre='foto.jpg'):
    """Cabecera JPEG real: el sistema valida el contenido, no solo el nombre."""
    return SimpleUploadedFile(nombre, b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 64 + b'\xff\xd9',
                              content_type='image/jpeg')


def correo(desde, asunto='Recojo', cuerpo='Hola, tenemos carga.', adjuntos=()):
    m = EmailMessage()
    m['From'], m['To'], m['Subject'] = desde, 'pedidos@gojustintime.pe', asunto
    m.set_content(cuerpo)
    for nombre, datos, tipo in adjuntos:
        principal, sub = tipo.split('/')
        m.add_attachment(datos, maintype=principal, subtype=sub, filename=nombre)
    return m


@override_settings(MEDIA_ROOT=MEDIA)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user('jefe', password='x', is_staff=True)
        u = User.objects.create_user('cliente1', password='x')
        cls.cuenta = Cuenta.objects.create(user=u, tipo='empresa', documento=RUC_CLIENTE, nombre='Cliente Logístico SAC',
                                           direccion='Av. Argentina 123, Lima', linea_credito=D('5000'))
        cls.perfil = ClienteLogistico.objects.create(cuenta=cls.cuenta, almacen_direccion='Almacén Callao, Av. Gambetta 5',
                                                     almacen_ubigeo='070101', contacto='Rosa')
        RemitenteAutorizado.objects.create(cliente=cls.perfil, tipo='CORREO', valor='Despachos@Cliente.pe')
        RemitenteAutorizado.objects.create(cliente=cls.perfil, tipo='CELULAR', valor='987 654 321')
        ua = User.objects.create_user('agente1', password='x')
        cls.agente = Agente.objects.create(user=ua, nombre='Operador Uno', zona='LIMA')
        cls.transportista = Transportista.objects.create(razon_social='Transportes Sur SAC', ruc='20444444441')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cfg = ConfigOperador.actual()
        cfg.activo = True
        cfg.save()
        EstadoLogistico.cargar_base()

    def solicitud_con_guias(self, *guias):
        sol = srv.crear_solicitud(self.cuenta, 'MANUAL', mensaje='Recojo de prueba')
        for kw in guias:
            srv.registrar_guia_xml(xml_guia(**kw), self.cuenta, sol)
        return sol


class ActivacionTest(Base):
    def test_apagado_no_lee_canales_ni_muestra_pantallas(self):
        ConfigOperador.objects.update(activo=False)
        self.assertIsNone(correo_a_solicitud(correo('despachos@cliente.pe')))
        self.assertIsNone(whatsapp_a_solicitud({'from': '51987654321', 'type': 'text', 'text': {'body': 'hola'}}))
        self.assertFalse(SolicitudRecojo.objects.exists())
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(reverse('operador:tablero')), reverse('operador:configuracion'))
        self.assertEqual(self.client.get(reverse('operador:configuracion')).status_code, 200)
        self.client.force_login(self.agente.user)
        self.assertEqual(self.client.get(reverse('operador:movil')).status_code, 403)

    def test_menu_segun_rol(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse('ops_panel')), reverse('operador:tablero'))
        self.client.force_login(self.agente.user)
        self.assertContains(self.client.get(reverse('agente_panel')), reverse('operador:movil'))
        self.client.force_login(self.cuenta.user)
        self.assertNotContains(self.client.get(reverse('cliente_panel')), 'Operador logístico')


class CanalesTest(Base):
    def test_correo_autorizado_crea_solicitud_con_guia_y_adjunto(self):
        cuerpo = 'Buenos días\nBultos: 3\nContacto: Jorge 999111222\nFecha: mañana'
        m = correo('Despachos <DESPACHOS@cliente.pe>', 'Recojo urgente', cuerpo,
                   [('EG01-7.xml', xml_guia(numero=7), 'application/xml'),
                    ('guia.pdf', b'%PDF-1.4 prueba', 'application/pdf')])
        sol = correo_a_solicitud(m)
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertEqual((sol.canal, sol.estado, sol.bultos), ('CORREO', 'ASIGNADO', 3))
        self.assertEqual(sol.contacto, 'Jorge 999111222')
        self.assertEqual(sol.fecha_recojo, timezone.localdate() + timedelta(days=1))
        self.assertEqual(sol.almacen, 'Almacén Callao, Av. Gambetta 5')  # de la ficha del cliente
        g = sol.guias.get()
        self.assertEqual((g.serie, g.numero, g.destino, g.peso), ('EG01', '7', 'AREQUIPA', D('12.50')))
        self.assertTrue(g.datos_completos)
        self.assertIn('guia.pdf guardado como adjunto', sol.avisos)
        self.assertEqual(sol.adjuntos.count(), 1)
        self.assertEqual(MensajeEntrante.objects.get().resultado, 'SOLICITUD')

    def test_zip_con_xml(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('EG01-8.xml', xml_guia(numero=8))
            f.writestr('carpeta/EG01-9.xml', xml_guia(numero=9))
        sol = correo_a_solicitud(correo('despachos@cliente.pe', adjuntos=[('guias.zip', z.getvalue(), 'application/zip')]))
        self.assertEqual(sorted(sol.guias.values_list('numero', flat=True)), ['8', '9'])

    def test_sin_texto_basta_y_segundo_mensaje_se_une(self):
        sol = correo_a_solicitud(correo('despachos@cliente.pe', asunto='', cuerpo=''))
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertIsNone(sol.bultos)
        otra = correo_a_solicitud(correo('despachos@cliente.pe', cuerpo='Bultos: 4'))
        self.assertEqual(otra.pk, sol.pk)
        self.assertEqual(SolicitudRecojo.objects.get(pk=sol.pk).bultos, 4)

    def test_correo_no_autorizado_queda_en_mensajes(self):
        self.assertIsNone(correo_a_solicitud(correo('otro@cliente.pe', 'Cotización')))
        self.assertFalse(SolicitudRecojo.objects.exists())
        m = MensajeEntrante.objects.get()
        self.assertEqual((m.remitente, m.resultado, m.asunto), ('otro@cliente.pe', 'NO_AUTORIZADO', 'Cotización'))

    def test_cliente_sin_credito_ni_casilla(self):
        Cuenta.objects.filter(pk=self.cuenta.pk).update(linea_credito=0)
        self.assertIsNone(correo_a_solicitud(correo('despachos@cliente.pe')))
        self.assertEqual(MensajeEntrante.objects.get().resultado, 'SIN_CREDITO')
        ClienteLogistico.objects.filter(pk=self.perfil.pk).update(servicio_logistico=True)
        self.assertIsInstance(correo_a_solicitud(correo('despachos@cliente.pe')), SolicitudRecojo)

    def test_usuario_inactivo_no_pide(self):
        User.objects.filter(pk=self.cuenta.user_id).update(is_active=False)
        self.assertIsNone(correo_a_solicitud(correo('despachos@cliente.pe')))

    def test_whatsapp_por_los_ultimos_9_digitos(self):
        sol = whatsapp_a_solicitud({'from': '51987654321', 'type': 'image', 'image': {'caption': '*Bultos:* 2'}})
        self.assertIsInstance(sol, SolicitudRecojo)
        self.assertEqual((sol.canal, sol.remitente, sol.bultos), ('WHATSAPP', '+51987654321', 2))
        self.assertIn('Envió una imagen', sol.avisos)
        self.assertIsNone(whatsapp_a_solicitud({'from': '51900000000', 'type': 'text', 'text': {'body': 'Precios?'}}))
        self.assertEqual(SolicitudRecojo.objects.count(), 1)
        self.assertTrue(MensajeEntrante.objects.filter(remitente='+51900000000', resultado='NO_AUTORIZADO').exists())

    def test_lineas_opcionales(self):
        d = srv.leer_campos('*Almacén:* Planta Ate\nbultos = 12 cajas\nFecha: 05/12/2026\nOtra cosa: x')
        self.assertEqual(d['almacen'], 'Planta Ate')
        self.assertEqual(d['bultos'], 12)
        self.assertEqual(str(d['fecha']), '2026-12-05')
        self.assertNotIn('contacto', d)

    def test_autorizados_unicos_entre_clientes(self):
        from .forms import ClienteLogisticoForm
        u = User.objects.create_user('otro', password='x')
        otra = Cuenta.objects.create(user=u, tipo='empresa', documento='20999999991', nombre='Otro')
        f = ClienteLogisticoForm({'correos': 'despachos@cliente.pe', 'celulares': '+51 987654321'},
                                 instance=ClienteLogistico(cuenta=otra))
        self.assertFalse(f.is_valid())
        self.assertIn('correos', f.errors)
        self.assertIn('celulares', f.errors)

    @override_settings(ROOT_URLCONF='ceibago.urls')
    def test_webhook_whatsapp_firmado(self):
        import os
        from unittest.mock import patch
        cuerpo = json.dumps({'entry': [{'changes': [{'value': {'messages': [
            {'from': '51987654321', 'id': 'w1', 'type': 'text', 'text': {'body': 'Recojo hoy'}}]}}]}]}).encode()
        url = reverse('operador:whatsapp')
        with patch.dict(os.environ, {'WHATSAPP_APP_SECRET': 'secreto-de-prueba', 'WHATSAPP_VERIFY_TOKEN': 'tok'}):
            self.assertEqual(self.client.post(url, cuerpo, content_type='application/json').status_code, 403)
            firma = 'sha256=' + hmac.new(b'secreto-de-prueba', cuerpo, hashlib.sha256).hexdigest()
            r = self.client.post(url, cuerpo, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=firma)
            self.assertEqual(r.status_code, 200)
            r = self.client.get(url, {'hub.mode': 'subscribe', 'hub.verify_token': 'tok', 'hub.challenge': '42'})
            self.assertEqual(r.content, b'42')
            r = self.client.get(url, {'hub.mode': 'subscribe', 'hub.verify_token': 'mal', 'hub.challenge': '42'})
            self.assertEqual(r.status_code, 403)
        self.assertEqual(SolicitudRecojo.objects.get().canal, 'WHATSAPP')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WHATSAPP_APP_SECRET', None)
            self.assertEqual(self.client.post(url, cuerpo, content_type='application/json').status_code, 403)


class XmlTest(TestCase):
    def test_lee_la_guia_sin_lxml(self):
        d = leer(xml_guia(numero=15))
        self.assertEqual((d['serie'], d['numero'], d['emisor_ruc'], d['bultos']), ('EG01', '15', RUC_CLIENTE, 2))
        self.assertEqual((d['llegada_ubigeo'], d['partida_ubigeo'], d['peso']), ('040101', '150101', D('12.50')))
        self.assertEqual(d['items'][0]['descripcion'], 'Cajas de repuestos')

    def test_rechaza_entidades(self):
        malo = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><DespatchAdvice>&a;</DespatchAdvice>'
        with self.assertRaises(ErrorXML):
            leer(malo)
        with self.assertRaises(ErrorXML):
            leer(b'<Invoice/>')


class QrTest(TestCase):
    def test_formatos(self):
        a = interpretar(f'{RUC_CLIENTE}|09|EG01|00000123|2026-11-18')
        self.assertEqual((a.clase, a.ruc, a.serie, a.numero), ('GUIA', RUC_CLIENTE, 'EG01', '123'))
        self.assertEqual(interpretar('CEIBA-OL|CONSOLIDADO|CL-000001|abc').clase, 'CONSOLIDADO')
        e = interpretar('https://e-factura.sunat.gob.pe/x?serie=T001&numero=45&ruc=20100070970')
        self.assertEqual((e.clase, e.serie, e.numero), ('ENLACE', 'T001', '45'))
        self.assertFalse(interpretar('https://e-factura.sunat.gob.pe/descarga/abc').identifica_guia)
        self.assertEqual(interpretar('hola').clase, 'DESCONOCIDO')
        self.assertEqual(interpretar('').clase, 'VACIO')


class FlujoTest(Base):
    def test_flujo_completo(self):
        sol = self.solicitud_con_guias({'numero': 1}, {'numero': 2, 'dest_doc': '20100070971', 'dest_nombre': 'Otro AQP'},
                                       {'numero': 3, 'ubigeo': '130101', 'direccion': 'Jr. Pizarro 1, Trujillo'})
        self.assertEqual(sol.guias.filter(estado__codigo='ATENDIDO').count(), 3)
        # recojo: escanea una guía y se confirma toda la solicitud (no hay recojo parcial)
        operador = self.agente.user
        g1 = srv.registrar_recojo(operador, f'{RUC_CLIENTE}|09|EG01|1', latitud='-12.05', longitud='-77.04')
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'ATENDIDO')
        self.assertEqual(GuiaCliente.objects.filter(estado__codigo='CONFIRMADO').count(), 3)
        self.assertEqual(g1.recojo_latitud, D('-12.050000'))
        with self.assertRaises(srv.ErrorOperador) as e:
            srv.registrar_recojo(operador, f'{RUC_CLIENTE}|09|EG01|1')
        self.assertTrue(e.exception.repetida)
        # tablero: dos grupos por destino
        grupos, _ = srv.tablero()
        self.assertEqual({g.destino: len(gs) for g, gs in grupos}, {'AREQUIPA': 2, 'TRUJILLO (LA LIBERTAD)': 1})
        aqp = Consolidado.objects.get(departamento='AREQUIPA')
        tru = Consolidado.objects.get(departamento='LA LIBERTAD')
        srv.mover_guia(GuiaCliente.objects.get(numero='3'), aqp, self.staff)  # logística decide moverla
        self.assertEqual(GuiaCliente.objects.get(numero='3').consolidado, aqp)
        srv.mover_guia(GuiaCliente.objects.get(numero='3'), tru, self.staff)
        srv.registrar_ingreso(operador, f'{RUC_CLIENTE}|09|EG01|2')
        # consolidada con sus guías como relacionadas (SUNAT simulado: queda aceptada)
        with self.assertRaises(srv.ErrorOperador):
            srv.emitir_consolidada(aqp, self.staff, llegada_ubigeo='040101', llegada_direccion='Agencia AQP')
        srv.confirmar_grupo(aqp, self.staff)
        with self.assertRaises(srv.ErrorOperador):
            srv.emitir_consolidada(aqp, self.staff)  # falta la llegada
        gr = srv.emitir_consolidada(aqp, self.staff, 'Agencia Sur', '20555555551', '040101',
                                    'Agencia Arequipa, Calle Mercaderes 10', self.transportista)
        self.assertEqual((gr.clase, gr.sunat_estado, gr.destinatario_doc), ('CONSOLIDADA', 'aceptada', '20555555551'))
        self.assertEqual(sorted(gr.relacionados.values_list('numero', flat=True)), ['EG01-1', 'EG01-2'])
        self.assertTrue(all(r.emisor_ruc == RUC_CLIENTE and r.tipo == '09' for r in gr.relacionados.all()))
        self.assertEqual((gr.peso_kg, gr.bultos), (D('25.00'), 4))
        self.assertEqual(gr.partida_direccion, Configuracion.get().empresa_direccion)  # sin partida propia
        self.assertEqual(GuiaCliente.objects.filter(consolidado=aqp, estado__codigo='EN_TRAYECTO').count(), 2)
        # documento del transportista
        srv.registrar_transportista(aqp, self.staff, self.transportista, '31', 'V001-555')
        # llegada: sin foto no; con foto, Por entregar y guía del último tramo con 3 relacionadas
        aqp.refresh_from_db()
        with self.assertRaises(srv.ErrorOperador):
            srv.registrar_llegada(operador, aqp.qr_texto)
        srv.registrar_llegada(operador, aqp.qr_texto, foto=foto())
        aqp.refresh_from_db()
        self.assertEqual(aqp.estado, 'EN_DESTINO')
        self.assertEqual(GuiaGo.objects.filter(clase='ULTIMO_TRAMO').count(), 2)  # uno por destinatario
        u = GuiaCliente.objects.get(numero='1').ultimo_tramo
        rel = sorted(u.relacionados.values_list('tipo', 'numero', 'emisor_ruc'))
        empresa = Configuracion.get()
        self.assertEqual(rel, [('09', 'EG01-1', RUC_CLIENTE), ('09', gr.numero_corto, empresa.empresa_ruc),
                               ('31', 'V001-555', '20444444441')])
        self.assertTrue(u.adjuntos.exists())  # la foto del recibo
        self.assertEqual(GuiaCliente.objects.get(numero='1').estado.codigo, 'EN_REPARTO')
        # entrega: foto y DNI obligatorios
        with self.assertRaises(srv.ErrorOperador):
            srv.registrar_entrega(operador, f'{RUC_CLIENTE}|09|EG01|1', foto=foto(), dni='')
        srv.registrar_entrega(operador, f'{RUC_CLIENTE}|09|EG01|1', foto=foto(), dni='45678912', nombre='Ana')
        g = GuiaCliente.objects.get(numero='1')
        self.assertEqual((g.estado.codigo, g.receptor_dni), ('ENTREGADO', '45678912'))
        self.assertEqual(Consolidado.objects.get(pk=aqp.pk).estado, 'EN_DESTINO')
        srv.registrar_entrega(operador, '', GuiaCliente.objects.get(numero='2').pk, foto=foto(), dni='45678913')
        self.assertEqual(Consolidado.objects.get(pk=aqp.pk).estado, 'CERRADO')
        # bitácora: quién, cuándo, cómo y cuánto estuvo en el estado anterior
        b = BitacoraEstado.objects.filter(guia=g).order_by('fecha', 'id')
        self.assertEqual([x.estado.codigo for x in b],
                         ['ATENDIDO', 'CONFIRMADO', 'EN_CUSTODIA', 'EN_TRAYECTO', 'POR_ENTREGAR', 'EN_REPARTO',
                          'ENTREGADO'])
        self.assertEqual(b.filter(estado__codigo='CONFIRMADO').get().origen, 'ESCANEO')
        self.assertIsNotNone(b.last().segundos_anterior)

    def test_sunat_no_simulado_queda_pendiente(self):
        Configuracion.get()
        Configuracion.objects.update(proveedor_sunat='pse')  # con proveedor real la guía espera su envío
        self.solicitud_con_guias({'numero': 1})
        srv.registrar_recojo(self.staff, f'{RUC_CLIENTE}|09|EG01|1')
        grupo = Consolidado.objects.get()
        srv.confirmar_grupo(grupo)
        gr = srv.emitir_consolidada(grupo, llegada_ubigeo='040101', llegada_direccion='Agencia')
        self.assertEqual(gr.sunat_estado, 'pendiente')

    def test_guia_escaneada_sin_xml_queda_pendiente(self):
        sol = srv.crear_solicitud(self.cuenta, 'MANUAL')
        g = srv.registrar_recojo(self.staff, f'{RUC_CLIENTE}|09|EG02|50')
        self.assertFalse(g.datos_completos)
        self.assertEqual((g.solicitud, g.estado.codigo, g.consolidado), (sol, 'CONFIRMADO', None))
        g2, creada = srv.registrar_guia_xml(xml_guia(serie='EG02', numero=50), self.cuenta)
        self.assertFalse(creada)
        self.assertEqual(g2.pk, g.pk)
        self.assertTrue(g2.datos_completos)
        self.assertEqual(GuiaCliente.objects.get(pk=g.pk).consolidado.departamento, 'AREQUIPA')

    def test_estado_inicial_unico(self):
        self.assertEqual(EstadoLogistico.objects.filter(inicial=True).count(), 1)
        self.assertEqual(EstadoLogistico.el_inicial().codigo, 'ATENDIDO')

    def test_costo_por_guia_por_peso(self):
        self.solicitud_con_guias({'numero': 1, 'peso': '30'}, {'numero': 2, 'peso': '10', 'dest_doc': '20100070971'})
        srv.registrar_recojo(self.staff, f'{RUC_CLIENTE}|09|EG01|1')
        grupo = Consolidado.objects.get()
        srv.confirmar_grupo(grupo)
        srv.emitir_consolidada(grupo, llegada_ubigeo='040101', llegada_direccion='Agencia')
        CostoTransporte.objects.create(consolidado=grupo, descripcion='Flete', monto=D('80'))
        CostoTransporte.objects.create(consolidado=grupo, descripcion='Estiba', monto=D('20'))
        total, filas = srv.costos(grupo)
        self.assertEqual(total, D('100'))
        self.assertEqual(sorted(c for _, c in filas), [D('25.00'), D('75.00')])


class PantallasTest(Base):
    def test_pantallas_de_escritorio(self):
        sol = self.solicitud_con_guias({'numero': 1})
        srv.registrar_recojo(self.staff, f'{RUC_CLIENTE}|09|EG01|1')
        grupo = Consolidado.objects.get()
        srv.crear_solicitud(self.cuenta, 'WHATSAPP', '+51987654321', unir=False)  # sin operador asignado
        correo_a_solicitud(correo('desconocido@x.pe'))
        self.client.force_login(self.staff)
        for nombre, args in (('tablero', []), ('solicitudes', []), ('solicitud_nueva', []), ('solicitud', [sol.pk]),
                             ('guias', []), ('guia', [GuiaCliente.objects.get().pk]), ('planificacion', []),
                             ('consolidados', []), ('consolidado', [grupo.pk]), ('bitacora', []), ('costos', []),
                             ('mensajes', []), ('clientes', []), ('cliente', [self.cuenta.pk]), ('estados', []),
                             ('configuracion', []), ('movil', [])):
            r = self.client.get(reverse(f'operador:{nombre}', args=args))
            self.assertEqual(r.status_code, 200, nombre)
        srv.confirmar_grupo(grupo)
        # el campo de fecha (type=date) viene precargado en ISO: en formato local el navegador lo dejaba vacío
        r = self.client.get(reverse('operador:consolidado', args=[grupo.pk]))
        self.assertContains(r, f'name="fecha" value="{timezone.localdate().isoformat()}"')
        r = self.client.post(reverse('operador:consolidado', args=[grupo.pk]),
                             {'accion': 'emitir', 'llegada_ubigeo': '040101', 'llegada_direccion': 'Agencia AQP',
                              'fecha': timezone.localdate().isoformat(), 'transportista': self.transportista.pk})
        self.assertEqual(r.status_code, 302)
        grupo.refresh_from_db()
        self.assertEqual(grupo.estado, 'EN_TRAYECTO')
        r = self.client.post(reverse('operador:consolidado', args=[grupo.pk]),
                             {'accion': 'costo', 'descripcion': 'Flete', 'monto': '150'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(grupo.costos.get().monto, D('150'))
        r = self.client.post(reverse('operador:consolidado', args=[grupo.pk]),
                             {'accion': 'transportista', 'transportista': self.transportista.pk, 'tipo': '31',
                              'numero': 'V001-9', 'archivo': SimpleUploadedFile('x.pdf', b'%PDF-1.4 x')})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(grupo.adjuntos.count(), 1)
        self.assertContains(self.client.get(reverse('operador:hoja_despacho', args=[grupo.pk])), grupo.qr_texto)
        self.assertContains(self.client.get(reverse('operador:guia_go', args=[grupo.guia.pk])), 'EG01-1')
        self.assertContains(self.client.get(reverse('operador:mensajes')), 'desconocido@x.pe')

    def test_cliente_guarda_autorizados(self):
        self.client.force_login(self.staff)
        r = self.client.post(reverse('operador:cliente', args=[self.cuenta.pk]), {
            'servicio_logistico': 'on', 'correos': 'despachos@cliente.pe\nlogistica@cliente.pe',
            'celulares': '+51 912 345 678', 'almacen_direccion': 'Callao', 'almacen_ubigeo': '070101',
            'almacen_referencia': '', 'contacto': 'Rosa'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(self.perfil.autorizados.values_list('clave', flat=True)),
                         ['912345678', 'despachos@cliente.pe', 'logistica@cliente.pe'])

    def test_permisos(self):
        self.client.force_login(self.agente.user)  # el agente opera el celular, no planifica
        self.assertEqual(self.client.get(reverse('operador:movil')).status_code, 200)
        self.assertEqual(self.client.get(reverse('operador:tablero')).status_code, 403)
        self.assertEqual(self.client.post(reverse('operador:configuracion'), {}).status_code, 403)
        self.client.force_login(self.cuenta.user)  # el cliente nada
        self.assertEqual(self.client.get(reverse('operador:movil')).status_code, 403)
        self.assertEqual(self.client.post(reverse('operador:movil_registrar'), {'paso': 'RECOJO'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('operador:tablero')).status_code, 302)

    def test_configuracion_enciende_el_modulo(self):
        ConfigOperador.objects.update(activo=False)
        self.client.force_login(self.staff)
        r = self.client.post(reverse('operador:configuracion'), {
            'activo': 'on', 'partida_direccion': 'Almacén Go, Av. Colonial 1', 'partida_ubigeo': '150101',
            'serie_consolidada': 'T001', 'serie_ultimo_tramo': 'T002', 'motivo_traslado': '13',
            'descripcion_motivo': 'Traslado de mercadería de terceros', 'ultimo_tramo_por': 'DESTINATARIO'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ConfigOperador.actual().activo)


class CelularTest(Base):
    def test_verificar_y_registrar(self):
        self.solicitud_con_guias({'numero': 1})
        self.client.force_login(self.agente.user)
        r = self.client.get(reverse('operador:movil_pendientes'), {'paso': 'RECOJO'})
        self.assertEqual(len(r.json()['items']), 1)
        r = self.client.post(reverse('operador:movil_verificar'), {'paso': 'RECOJO', 'qr': f'{RUC_CLIENTE}|09|EG01|1'})
        self.assertTrue(r.json()['ok'])
        datos = {'paso': 'RECOJO', 'qr': f'{RUC_CLIENTE}|09|EG01|1', 'latitud': '-12.1', 'longitud': '-77.0',
                 'id_local': 'a1', 'cuando': (timezone.now() - timedelta(minutes=5)).isoformat()}
        r = self.client.post(reverse('operador:movil_registrar'), datos)
        self.assertEqual(r.json(), {'ok': True, 'mensaje': 'Recogida: guía EG01-1', 'id_local': 'a1'})
        g = GuiaCliente.objects.get()
        self.assertLess(g.recogida_en, timezone.now() - timedelta(minutes=4))  # la hora real del escaneo sin señal
        self.assertEqual(g.recogida_por, self.agente.user)
        r = self.client.post(reverse('operador:movil_registrar'), datos)  # reintento de la cola
        self.assertTrue(r.json()['ok'] and r.json()['repetida'])
        r = self.client.post(reverse('operador:movil_verificar'), {'paso': 'RECOJO', 'qr': 'basura'})
        self.assertFalse(r.json()['ok'])
