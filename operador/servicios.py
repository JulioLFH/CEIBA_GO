"""Flujo de Go Just In Time: cada escaneo o foto cambia el estado de la guía del cliente y queda en la bitácora.

Canales → Solicitud (Asignado) → Recojo con QR (Confirmado; la solicitud pasa a Atendido, sin recojo parcial) →
Ingreso al almacén (En custodia) → Planificación por destino → Guía consolidada (En trayecto) → Transporte tercero →
Llegada con QR y foto del recibo (Por entregar) → Guía del último tramo (En reparto) → Entrega con foto, DNI y firma
(Entregado)."""
import hashlib
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import lector_qr, xml_guias
from .models import (Adjunto, BitacoraEstado, ClienteLogistico, ConfigOperador, Consolidado, CostoTransporte,
                     DocumentoRelacionado, EstadoLogistico, GuiaCliente, GuiaGo, RemitenteAutorizado, SolicitudRecojo,
                     solo_digitos)

D0 = Decimal('0')
# orden del flujo normal (para no retroceder por un escaneo repetido)
FLUJO = ['ATENDIDO', 'CONFIRMADO', 'EN_CUSTODIA', 'EN_TRAYECTO', 'POR_ENTREGAR', 'EN_REPARTO', 'ENTREGADO']
PASOS = {'RECOJO': 'Recojo en el almacén del cliente', 'INGRESO': 'Ingreso al almacén de Go',
         'LLEGADA': 'Llegada a destino', 'ENTREGA': 'Entrega al destinatario'}
MAX_LISTA = 15
MAX_FOTO = 8 * 1024 * 1024


class ErrorOperador(Exception):
    """Error que se le muestra al operador en palabras simples."""

    def __init__(self, mensaje, repetida=False):
        super().__init__(mensaje)
        self.repetida = repetida


def _posicion(codigo):
    return FLUJO.index(codigo) if codigo in FLUJO else -1


def activo():
    return ConfigOperador.actual().activo


# ---------------------------------------------------------------- estados y bitácora
def cambiar_estado(guia, codigo, usuario=None, origen='SISTEMA', detalle='', latitud=None, longitud=None,
                   ahora=None):
    """Cambia el estado de la guía del cliente y lo deja en la bitácora con el tiempo en el estado anterior."""
    nuevo = EstadoLogistico.de(codigo)
    if guia.pk and guia.estado_id == nuevo.pk:
        return False
    ahora = ahora or timezone.now()
    anterior = guia.estado if guia.estado_id else None
    segundos = max(int((ahora - guia.estado_desde).total_seconds()), 0) if anterior and guia.estado_desde else None
    guia.estado, guia.estado_desde = nuevo, ahora
    if guia.pk:
        guia.save(update_fields=['estado', 'estado_desde'])
    else:
        guia.save()
    BitacoraEstado.objects.create(guia=guia, de=anterior.nombre if anterior else '', a=nuevo.nombre, estado=nuevo,
                                  usuario=_usuario(usuario), fecha=ahora, segundos_anterior=segundos, origen=origen,
                                  detalle=(detalle or '')[:250], latitud=_coord(latitud), longitud=_coord(longitud))
    return True


def _estado_solicitud(sol, nuevo, usuario=None, origen='SISTEMA', detalle='', ahora=None):
    ahora = ahora or timezone.now()
    previo = sol.bitacora.order_by('-fecha', '-id').first()
    desde = previo.fecha if previo else sol.creado
    anterior = dict(SolicitudRecojo.ESTADOS).get(sol.estado, '') if sol.pk and previo else ''
    sol.estado = nuevo
    BitacoraEstado.objects.create(solicitud=sol, de=anterior, a=dict(SolicitudRecojo.ESTADOS)[nuevo],
                                  usuario=_usuario(usuario), fecha=ahora, origen=origen, detalle=(detalle or '')[:250],
                                  segundos_anterior=max(int((ahora - desde).total_seconds()), 0) if previo else None)


def _usuario(usuario):
    return usuario if usuario is not None and getattr(usuario, 'is_authenticated', False) else None


def _coord(valor):
    try:
        return round(Decimal(str(valor)), 6) if valor not in (None, '') else None
    except ArithmeticError:
        return None


# ---------------------------------------------------------------- archivos
def validar_imagen(archivo):
    """La foto debe ser una imagen de verdad (JPG, PNG o WEBP por su contenido, no solo por el nombre)."""
    if archivo is None:
        raise ErrorOperador('Falta la foto.')
    if archivo.size > MAX_FOTO:
        raise ErrorOperador('La foto supera los 8 MB.')
    archivo.seek(0)
    cabecera = archivo.read(12)
    archivo.seek(0)
    if not (cabecera.startswith(b'\xff\xd8\xff') or cabecera.startswith(b'\x89PNG\r\n\x1a\n')
            or (cabecera[:4] == b'RIFF' and cabecera[8:12] == b'WEBP')):
        raise ErrorOperador('El archivo no es una foto (JPG, PNG o WEBP).')


def validar_documento(archivo):
    """Documento del transportista: una foto o un PDF (por su contenido), hasta 8 MB."""
    if archivo.size > MAX_FOTO:
        raise ErrorOperador('El archivo supera los 8 MB.')
    archivo.seek(0)
    es_pdf = archivo.read(5) == b'%PDF-'
    archivo.seek(0)
    if not es_pdf:
        validar_imagen(archivo)


def adjuntar(archivo, descripcion='', usuario=None, **destino):
    return Adjunto.objects.create(archivo=archivo, nombre=getattr(archivo, 'name', '')[:200],
                                  descripcion=descripcion[:200], usuario=_usuario(usuario), **destino)


def vincular(adjunto, descripcion='', usuario=None, **destino):
    """El mismo archivo ligado a otro documento (sin copiarlo)."""
    return Adjunto.objects.create(archivo=adjunto.archivo.name, nombre=adjunto.nombre, descripcion=descripcion[:200],
                                  usuario=_usuario(usuario), **destino)


# ---------------------------------------------------------------- clientes y solicitudes
def perfil(cuenta):
    return ClienteLogistico.objects.filter(cuenta=cuenta).first()


def buscar_remitente(tipo, valor):
    """(perfil, resultado): el ClienteLogistico cuyo correo o celular autorizado coincide con el remitente si puede
    pedir recojos; si no, None y el motivo (NO_AUTORIZADO o SIN_CREDITO)."""
    clave = RemitenteAutorizado.normalizar(tipo, valor)
    if not clave or (tipo == 'CELULAR' and len(clave) < 9):
        return None, 'NO_AUTORIZADO'
    r = RemitenteAutorizado.objects.filter(tipo=tipo, clave=clave).select_related('cliente__cuenta__user').first()
    if r is None:
        return None, 'NO_AUTORIZADO'
    return (r.cliente, 'SOLICITUD') if r.cliente.habilitado else (None, 'SIN_CREDITO')


def cliente_autorizado(tipo, valor):
    return buscar_remitente(tipo, valor)[0]


CAMPOS_MENSAJE = {'almacen': r'almac[eé]n', 'bultos': r'bultos?', 'contacto': r'contacto', 'fecha': r'fecha'}


def leer_campos(texto):
    """Líneas opcionales del mensaje: «Almacén: …», «Bultos: …», «Contacto: …», «Fecha: …». Lo demás se ignora."""
    datos = {}
    for linea in (texto or '').splitlines():
        for campo, patron in CAMPOS_MENSAJE.items():
            m = re.match(rf'^\s*\*?\s*{patron}\s*\*?\s*[:=]\s*\*?\s*(.+?)\s*\*?\s*$', linea, re.IGNORECASE)
            if m and campo not in datos:
                datos[campo] = m.group(1).strip()
    if 'bultos' in datos:
        n = re.search(r'\d+', datos['bultos'])
        datos['bultos'] = int(n.group()) if n else None
    if 'fecha' in datos:
        datos['fecha'] = _fecha_texto(datos['fecha'])
    return datos


def _fecha_texto(texto):
    texto = (texto or '').strip().lower()
    hoy = timezone.localdate()
    if texto.startswith('hoy'):
        return hoy
    if texto.startswith(('mañana', 'manana')):
        return hoy + timedelta(days=1)
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', texto)
    if m:
        y, mes, d = (int(x) for x in m.groups())
    else:
        m = re.match(r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?', texto)
        if not m:
            return None
        d, mes = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else hoy.year
        y += 2000 if y < 100 else 0
    try:
        return date(y, mes, d)
    except ValueError:
        return None


def solicitud_abierta(cuenta, hoy=None):
    """Solicitud del cliente aún sin atender creada hoy: un segundo mensaje del mismo día se suma a ella."""
    hoy = hoy or timezone.localdate()
    inicio = timezone.make_aware(datetime.combine(hoy, time.min))
    return SolicitudRecojo.objects.filter(cliente=cuenta, estado='ASIGNADO', creado__gte=inicio).order_by('-pk').first()


@transaction.atomic
def crear_solicitud(cuenta, canal='MANUAL', remitente='', asunto='', mensaje='', usuario=None, unir=True,
                    operador=None, **campos):
    """Solicitud de recojo (estado Asignado). El almacén de origen sale de la ficha del cliente; las líneas opcionales
    del mensaje lo completan o cambian."""
    leidos = {**leer_campos(mensaje), **{k: v for k, v in campos.items() if v not in (None, '')}}
    sol = solicitud_abierta(cuenta) if unir else None
    if sol is not None:
        sol.mensaje = f'{sol.mensaje}\n---\n{mensaje}'.strip()[:10000]
        for campo in ('bultos', 'contacto'):
            if leidos.get(campo) and not getattr(sol, campo):
                setattr(sol, campo, leidos[campo])
        if leidos.get('fecha') and not sol.fecha_recojo:
            sol.fecha_recojo = leidos['fecha']
        sol.save()
        BitacoraEstado.objects.create(solicitud=sol, de=sol.get_estado_display(), a=sol.get_estado_display(),
                                      usuario=_usuario(usuario), origen='CANAL' if canal != 'MANUAL' else 'PANTALLA',
                                      detalle=f'Otro mensaje del cliente por {dict(SolicitudRecojo.CANALES)[canal]}')
        return sol
    p = perfil(cuenta)
    sol = SolicitudRecojo(cliente=cuenta, canal=canal, remitente=(remitente or '')[:150],
                          asunto=(asunto or '')[:200], mensaje=(mensaje or '')[:10000],
                          almacen=(leidos.get('almacen') or (p.direccion_recojo if p else cuenta.direccion))[:250],
                          almacen_ubigeo=(p.almacen_ubigeo if p else ''),
                          bultos=leidos.get('bultos'), contacto=(leidos.get('contacto') or (p.contacto if p else ''))[:120],
                          fecha_recojo=leidos.get('fecha'), operador=operador)
    sol.save()
    _estado_solicitud(sol, 'ASIGNADO', usuario, 'CANAL' if canal != 'MANUAL' else 'PANTALLA',
                      f'Llegó por {sol.get_canal_display()}{f" de {remitente}" if remitente else ""}')
    sol.save(update_fields=['estado'])
    return sol


# ---------------------------------------------------------------- guías del cliente
def buscar_guia(lectura):
    """GuiaCliente que corresponde al QR leído (por RUC, serie y número; o por el enlace)."""
    if lectura.clase == 'ENLACE' and not lectura.identifica_guia:
        return GuiaCliente.objects.filter(qr_texto=lectura.texto).first()
    if not lectura.identifica_guia:
        return None
    qs = GuiaCliente.objects.filter(serie=lectura.serie, numero=lectura.numero)
    if lectura.ruc:
        qs = qs.filter(emisor_ruc=lectura.ruc)
    if lectura.tipo:
        qs = qs.filter(tipo=lectura.tipo)
    return qs.first() if qs.count() == 1 else None


def _datos_xml(guia, datos):
    for campo in ('emisor_nombre', 'fecha_emision', 'fecha_traslado', 'motivo_traslado', 'destinatario_tipo_doc',
                  'destinatario_doc', 'destinatario_nombre', 'partida_ubigeo', 'partida_direccion', 'llegada_ubigeo',
                  'llegada_direccion', 'bultos', 'peso', 'unidad_peso', 'items'):
        setattr(guia, campo, datos[campo])
    guia.poner_destino()
    guia.datos_completos = True


@transaction.atomic
def registrar_guia_xml(contenido, cliente, solicitud=None, usuario=None):
    """Carga la guía del cliente desde su XML sin digitar. Si ya estaba (escaneada antes sin datos), la completa.
    Devuelve (guía, creada)."""
    datos = xml_guias.leer(contenido)
    guia = GuiaCliente.objects.filter(emisor_ruc=datos['emisor_ruc'], tipo='09', serie=datos['serie'],
                                      numero=datos['numero']).first()
    if guia is not None:
        if not guia.datos_completos:
            _datos_xml(guia, datos)
            guia.origen_datos = guia.origen_datos if guia.origen_datos != 'MANUAL' else 'XML'
            if solicitud is not None and guia.solicitud_id is None:
                guia.solicitud = solicitud
            guia.save()
            if _posicion(guia.estado.codigo) >= _posicion('CONFIRMADO'):
                asignar_grupo(guia)
        return guia, False
    guia = GuiaCliente(cliente=cliente, solicitud=solicitud, emisor_ruc=datos['emisor_ruc'], tipo='09',
                       serie=datos['serie'], numero=datos['numero'], origen_datos='XML')
    _datos_xml(guia, datos)
    cambiar_estado(guia, EstadoLogistico.el_inicial().codigo, usuario, 'SISTEMA',
                   f'Guía cargada de su XML{f" (solicitud {solicitud.numero})" if solicitud else ""}')
    return guia, True


def _guia_desde_qr(lectura, cliente, solicitud):
    """Guía nueva a partir del QR (sin XML todavía): los datos que el QR no trae quedan pendientes, nunca inventados."""
    if lectura.identifica_guia:
        serie, numero = lectura.serie, lectura.numero
    else:  # un enlace sin serie ni número: la misma guía vuelve a reconocerse por su enlace
        serie, numero = 'QR', hashlib.sha1(lectura.texto.encode()).hexdigest()[:10].upper()
    datos = lector_qr.obtener_datos_de_enlace(lectura.url) if lectura.url else None
    ruc = lectura.ruc or cliente.documento
    guia = GuiaCliente(cliente=cliente, solicitud=solicitud, emisor_ruc=ruc,
                       emisor_nombre=cliente.nombre if ruc == cliente.documento else '', tipo=lectura.tipo or '09',
                       serie=serie, numero=numero, origen_datos='QR', qr_texto=lectura.texto, datos_completos=False)
    if datos:
        _datos_xml(guia, datos)
    return guia


def _solicitud_para(guia_o_cliente, usuario):
    """Solicitud abierta a la que pertenece la guía: la suya, la del cliente emisor o la única asignada al operador."""
    if isinstance(guia_o_cliente, GuiaCliente) and guia_o_cliente.solicitud_id:
        return guia_o_cliente.solicitud
    cuenta = guia_o_cliente.cliente if isinstance(guia_o_cliente, GuiaCliente) else guia_o_cliente
    if cuenta is not None:
        sol = SolicitudRecojo.objects.filter(cliente=cuenta, estado='ASIGNADO').order_by('creado').first()
        if sol:
            return sol
    if usuario is not None and getattr(usuario, 'is_authenticated', False):
        propias = list(SolicitudRecojo.objects.filter(estado='ASIGNADO', operador=usuario)[:2])
        if len(propias) == 1:
            return propias[0]
    return None


def _cuenta_por_ruc(ruc):
    from envios.models import Cuenta
    return Cuenta.objects.filter(documento=ruc).order_by('pk').first() if ruc else None


# ---------------------------------------------------------------- pasos del operador (celular)
def _guia_de_qr(qr, guia_id, permitir_nueva=False):
    """(guía, lectura, origen) a partir del QR o de la guía elegida de la lista."""
    if guia_id:
        guia = GuiaCliente.objects.filter(pk=guia_id).select_related('estado').first()
        if guia is None:
            raise ErrorOperador('Esa guía ya no está en la lista.')
        return guia, None, 'LISTA'
    lectura = lector_qr.interpretar(qr)
    if lectura.clase == 'VACIO':
        raise ErrorOperador('No se leyó ningún código. Vuelva a escanear o elija la guía de la lista.')
    if lectura.clase == 'CONSOLIDADO':
        raise ErrorOperador('Ese código es de una hoja de despacho, no de una guía del cliente.')
    if lectura.clase == 'DESCONOCIDO':
        raise ErrorOperador('Este código no es de una guía. Elija la guía de la lista.')
    guia = buscar_guia(lectura)
    if guia is None and not permitir_nueva:
        guia = _guia_por_ultimo_tramo(lectura)
    if guia is None and not permitir_nueva:
        raise ErrorOperador('No encontramos esta guía. Elija la guía de la lista.')
    return guia, lectura, 'ESCANEO'


def _guia_por_ultimo_tramo(lectura):
    """La guía del último tramo (de Go) también identifica la guía del cliente que lleva."""
    if not lectura.identifica_guia or not lectura.numero.isdigit():
        return None
    return GuiaCliente.objects.filter(ultimo_tramo__serie=lectura.serie,
                                      ultimo_tramo__numero=int(lectura.numero)).first()


@transaction.atomic
def registrar_recojo(usuario, qr='', guia_id=None, latitud=None, longitud=None, ahora=None):
    """El operador escanea el QR de la guía en el almacén del cliente: la guía queda Confirmada con hora y GPS, y la
    solicitud completa pasa a Atendido (no hay recojo parcial: todas sus guías se confirman juntas)."""
    ahora = ahora or timezone.now()
    guia, lectura, origen = _guia_de_qr(qr, guia_id, permitir_nueva=True)
    if guia is None:
        cliente = _cuenta_por_ruc(lectura.ruc)
        solicitud = _solicitud_para(cliente, usuario) if cliente else _solicitud_para(None, usuario)
        if solicitud is None:
            raise ErrorOperador('No hay una solicitud de recojo abierta para esta guía. Avise a logística.')
        guia = _guia_desde_qr(lectura, cliente or solicitud.cliente, solicitud)
        cambiar_estado(guia, EstadoLogistico.el_inicial().codigo, usuario, 'ESCANEO',
                       'Guía creada al escanear su QR' + ('' if guia.datos_completos else ' (datos pendientes)'),
                       latitud, longitud, ahora)
    elif lectura is not None and not guia.qr_texto:
        guia.qr_texto = lectura.texto
        guia.save(update_fields=['qr_texto'])
    if _posicion(guia.estado.codigo) >= _posicion('CONFIRMADO'):
        raise ErrorOperador(f'Esta guía ya fue recogida ({guia.estado.nombre}).', repetida=True)
    solicitud = _solicitud_para(guia, usuario)
    guia.solicitud = solicitud
    guia.recogida_en, guia.recogida_por = ahora, _usuario(usuario)
    guia.recojo_latitud, guia.recojo_longitud = _coord(latitud), _coord(longitud)
    guia.save(update_fields=['solicitud', 'recogida_en', 'recogida_por', 'recojo_latitud', 'recojo_longitud'])
    cambiar_estado(guia, 'CONFIRMADO', usuario, origen, 'Recojo en el almacén del cliente', latitud, longitud, ahora)
    asignar_grupo(guia)
    if solicitud is not None and solicitud.estado == 'ASIGNADO':
        solicitud.atendida_en, solicitud.atendida_por = ahora, _usuario(usuario)
        solicitud.latitud, solicitud.longitud = _coord(latitud), _coord(longitud)
        _estado_solicitud(solicitud, 'ATENDIDO', usuario, origen, f'Recojo de la guía {guia.numero_completo}', ahora)
        solicitud.save()
        for otra in solicitud.guias.select_related('estado').exclude(pk=guia.pk):
            if _posicion(otra.estado.codigo) < _posicion('CONFIRMADO'):
                otra.recogida_en, otra.recogida_por = ahora, _usuario(usuario)
                otra.save(update_fields=['recogida_en', 'recogida_por'])
                cambiar_estado(otra, 'CONFIRMADO', usuario, 'SISTEMA',
                               f'Recojo completo de la solicitud {solicitud.numero}', latitud, longitud, ahora)
                asignar_grupo(otra)
    return guia


@transaction.atomic
def registrar_ingreso(usuario, qr='', guia_id=None, latitud=None, longitud=None, ahora=None):
    """El bulto entra al almacén de Go: En custodia."""
    guia, _, origen = _guia_de_qr(qr, guia_id)
    if _posicion(guia.estado.codigo) >= _posicion('EN_CUSTODIA'):
        raise ErrorOperador(f'Esta guía ya está en el almacén ({guia.estado.nombre}).', repetida=True)
    if guia.estado.codigo != 'CONFIRMADO':
        raise ErrorOperador('Esta guía todavía no se recogió en el almacén del cliente.')
    cambiar_estado(guia, 'EN_CUSTODIA', usuario, origen, 'Ingresó al almacén de Go', latitud, longitud, ahora)
    return guia


# ---------------------------------------------------------------- planificación por destino
def asignar_grupo(guia):
    """La guía recogida entra sola al grupo abierto de su destino (departamento y provincia), de cualquier cliente."""
    if guia.consolidado_id or not guia.departamento:
        return None
    provincia = guia.provincia or guia.departamento
    grupo = Consolidado.objects.filter(departamento=guia.departamento, provincia=provincia,
                                       estado='ARMANDO').order_by('pk').first()
    if grupo is None:
        grupo = Consolidado.objects.create(departamento=guia.departamento, provincia=provincia)
    guia.consolidado = grupo
    guia.save(update_fields=['consolidado'])
    return grupo


def tablero():
    """([(grupo, [guías])] de los grupos abiertos, guías recogidas que aún no tienen destino)."""
    grupos = []
    for g in Consolidado.objects.filter(estado__in=('ARMANDO', 'CONFIRMADO')).order_by('departamento', 'provincia',
                                                                                      'pk'):
        grupos.append((g, list(g.guias.select_related('estado', 'cliente').order_by('pk'))))
    sin_destino = GuiaCliente.objects.filter(consolidado__isnull=True, estado__codigo__in=('CONFIRMADO', 'EN_CUSTODIA')) \
        .select_related('estado', 'cliente')
    return grupos, list(sin_destino)


@transaction.atomic
def mover_guia(guia, grupo, usuario=None):
    """Logística decide: la guía pasa a otro grupo abierto (o a uno nuevo con su destino)."""
    if guia.consolidado_id and not guia.consolidado.abierto:
        raise ErrorOperador('La guía ya salió en una guía consolidada.')
    if grupo is not None and not grupo.abierto:
        raise ErrorOperador('Ese grupo ya salió.')
    if grupo is None:
        if not guia.departamento:
            raise ErrorOperador('La guía no tiene destino: espere su XML.')
        grupo = Consolidado.objects.create(departamento=guia.departamento,
                                           provincia=guia.provincia or guia.departamento)
    anterior = guia.consolidado
    guia.consolidado = grupo
    guia.save(update_fields=['consolidado'])
    BitacoraEstado.objects.create(guia=guia, de=guia.estado.nombre, a=guia.estado.nombre, estado=guia.estado,
                                  usuario=_usuario(usuario), origen='PANTALLA',
                                  detalle=f'Movida de {anterior.numero if anterior else "sin grupo"} a {grupo.numero}')
    if anterior is not None and anterior.estado == 'ARMANDO' and not anterior.guias.exists():
        anterior.estado = 'ANULADO'
        anterior.save(update_fields=['estado'])
    return grupo


def confirmar_grupo(grupo, usuario=None):
    if grupo.estado != 'ARMANDO':
        raise ErrorOperador('El grupo ya fue confirmado.')
    if not grupo.guias.exists():
        raise ErrorOperador('El grupo no tiene guías.')
    grupo.estado, grupo.confirmado_en, grupo.confirmado_por = 'CONFIRMADO', timezone.now(), _usuario(usuario)
    grupo.save(update_fields=['estado', 'confirmado_en', 'confirmado_por'])
    return grupo


# ---------------------------------------------------------------- guías de remisión de Go
def _empresa():
    from envios.models import Configuracion
    return Configuracion.get()


def _peso_kg(guia):
    return (guia.peso or D0) * (1000 if guia.unidad_peso == 'TNE' else 1)


def _nueva_guia(clase, partida_ubigeo, partida_direccion, llegada_ubigeo, llegada_direccion, destinatario_doc,
                destinatario_nombre, serie, guias, relacionados, transportista=None, fecha=None, observaciones=''):
    """Guía remitente de Go. La emisión sigue a Ceiba GO: con el proveedor «simulado» queda aceptada al instante; con
    otro proveedor queda pendiente de envío (la conexión real con SUNAT es la etapa siguiente)."""
    from envios.models import Serie
    cfg, empresa = ConfigOperador.actual(), _empresa()
    gr = GuiaGo.objects.create(
        clase=clase, serie=serie, numero=Serie.siguiente(serie), fecha=fecha or timezone.localdate(),
        motivo_traslado=cfg.motivo_traslado, descripcion_motivo=cfg.descripcion_motivo[:100],
        modalidad='01' if transportista else '02', destinatario_doc=(destinatario_doc or '')[:15],
        destinatario_nombre=(destinatario_nombre or 'Destinatario')[:200], partida_ubigeo=partida_ubigeo or '',
        partida_direccion=(partida_direccion or '')[:250], llegada_ubigeo=llegada_ubigeo or '',
        llegada_direccion=(llegada_direccion or '')[:250], transportista=transportista,
        peso_kg=sum((_peso_kg(g) for g in guias), D0) or Decimal('1'),
        bultos=sum(g.bultos for g in guias) or len(guias),
        items=[{'descripcion': f'Mercadería de {g.emisor_nombre or g.emisor_ruc} según guía {g.numero_completo}'[:250],
                'cantidad': g.bultos or 1, 'unidad': 'NIU'} for g in guias],
        observaciones=observaciones[:500],
        sunat_estado='aceptada' if empresa.proveedor_sunat == 'simulado' else 'pendiente')
    vistos = set()
    for tipo, num, ruc in relacionados:
        if (tipo, num, ruc) not in vistos:
            vistos.add((tipo, num, ruc))
            DocumentoRelacionado.objects.create(guia=gr, tipo=tipo, numero=num, emisor_ruc=ruc or '')
    return gr


@transaction.atomic
def emitir_consolidada(grupo, usuario=None, agencia_nombre='', agencia_ruc='', llegada_ubigeo='', llegada_direccion='',
                       transportista=None, fecha=None):
    """Una guía remitente de Go por grupo, con todas las guías del grupo como documentos relacionados (llenados
    solos). Las guías pasan a En trayecto."""
    if grupo.estado != 'CONFIRMADO':
        raise ErrorOperador('Primero confirme el grupo en el tablero de planificación.')
    guias = list(grupo.guias.select_related('estado'))
    if not guias:
        raise ErrorOperador('El grupo no tiene guías.')
    sin_datos = [g.numero_completo for g in guias if not g.datos_completos or g.serie == 'QR']
    if sin_datos:
        raise ErrorOperador(f'Faltan los datos de {len(sin_datos)} guía(s) ({", ".join(sin_datos[:5])}): espere su '
                            f'XML o muévalas a otro grupo.')
    if not llegada_ubigeo or not llegada_direccion:
        raise ErrorOperador('Indique la agencia de destino (ubigeo y dirección de llegada).')
    cfg, empresa = ConfigOperador.actual(), _empresa()
    gr = _nueva_guia('CONSOLIDADA', cfg.partida_ubigeo, cfg.partida_direccion or empresa.empresa_direccion,
                     llegada_ubigeo, llegada_direccion, agencia_ruc or empresa.empresa_ruc,
                     agencia_nombre or empresa.empresa_razon, cfg.serie_consolidada, guias,
                     [('09', g.numero_completo, g.emisor_ruc) for g in guias], transportista, fecha,
                     f'Consolidado {grupo.numero} · {len(guias)} guía(s) de clientes hacia {grupo.destino}')
    grupo.guia, grupo.transportista = gr, transportista or grupo.transportista
    grupo.agencia_nombre, grupo.agencia_ruc = (agencia_nombre or '')[:200], (agencia_ruc or '')[:11]
    grupo.llegada_ubigeo, grupo.llegada_direccion, grupo.estado = llegada_ubigeo, llegada_direccion[:250], 'EN_TRAYECTO'
    grupo.save()
    ahora = timezone.now()
    for g in guias:
        if g.estado.codigo == 'CONFIRMADO':  # no pasó por el escaneo de ingreso: se registra el paso
            cambiar_estado(g, 'EN_CUSTODIA', usuario, 'SISTEMA', 'Ingreso al almacén registrado al consolidar',
                           ahora=ahora)
        cambiar_estado(g, 'EN_TRAYECTO', usuario, 'PANTALLA', f'Guía consolidada {gr.numero_completo}', ahora=ahora)
    return gr


def _transportista_por_ruc(ruc):
    from envios.models import Transportista
    return Transportista.objects.filter(ruc=ruc).order_by('pk').first() if ruc else None


@transaction.atomic
def registrar_transportista(grupo, usuario=None, transportista=None, tipo='', numero='', fecha=None, peso=None,
                            bultos=None, qr='', archivo=None):
    """Documento del transporte tercero (su guía transportista o su recibo, por QR o foto), ligado a la consolidada."""
    if grupo.estado in ('ARMANDO', 'ANULADO', 'CERRADO'):
        raise ErrorOperador('Primero confirme el grupo.')
    lectura = lector_qr.interpretar(qr) if qr else None
    if lectura is not None and lectura.identifica_guia:
        numero = numero or f'{lectura.serie}-{lectura.numero}'
        tipo = tipo or ('31' if lectura.tipo in ('31', '') else 'RECIBO')
        transportista = transportista or _transportista_por_ruc(lectura.ruc)
    if not (numero or archivo or qr):
        raise ErrorOperador('Escanee el QR o tome la foto del documento del transportista.')
    grupo.transportista = transportista or grupo.transportista
    grupo.doc_transportista_tipo = tipo or grupo.doc_transportista_tipo or ('31' if numero else 'RECIBO')
    grupo.doc_transportista_numero = (numero or grupo.doc_transportista_numero)[:30]
    grupo.doc_transportista_fecha = fecha or grupo.doc_transportista_fecha or timezone.localdate()
    grupo.doc_transportista_peso = peso if peso not in (None, '') else grupo.doc_transportista_peso
    grupo.doc_transportista_bultos = bultos if bultos not in (None, '') else grupo.doc_transportista_bultos
    grupo.doc_transportista_qr = qr or grupo.doc_transportista_qr
    grupo.save()
    if archivo is not None:
        adjuntar(archivo, 'Documento del transportista', usuario, consolidado=grupo)
    return grupo


def _consolidado_de_qr(qr, consolidado_id):
    if consolidado_id:
        g = Consolidado.objects.filter(pk=consolidado_id).first()
        if g is None:
            raise ErrorOperador('Ese despacho ya no está en la lista.')
        return g, 'LISTA'
    lectura = lector_qr.interpretar(qr)
    if lectura.clase == 'VACIO':
        raise ErrorOperador('No se leyó ningún código. Vuelva a escanear o elija el despacho de la lista.')
    if lectura.clase == 'CONSOLIDADO':
        g = Consolidado.objects.filter(numero=lectura.numero, token=lectura.token).first()
        if g is None:
            raise ErrorOperador('Esta hoja de despacho no es válida.')
        return g, 'ESCANEO'
    if lectura.identifica_guia:
        filtro = Q(doc_transportista_numero__iexact=f'{lectura.serie}-{lectura.numero}')
        if lectura.numero.isdigit():
            filtro |= Q(guia__serie=lectura.serie, guia__numero=int(lectura.numero))
        g = Consolidado.objects.filter(filtro).first()
        if g is not None:
            return g, 'ESCANEO'
    raise ErrorOperador('Este código no es de una guía consolidada ni de un transportista. Elija de la lista.')


@transaction.atomic
def registrar_llegada(usuario, qr='', consolidado_id=None, foto=None, latitud=None, longitud=None, ahora=None):
    """El operador de destino escanea el QR de la consolidada (o de la del transportista) y toma la foto del recibo:
    todas las guías del grupo pasan a Por entregar y se genera la guía del último tramo (En reparto)."""
    ahora = ahora or timezone.now()
    grupo, origen = _consolidado_de_qr(qr, consolidado_id)
    if grupo.estado in ('EN_DESTINO', 'CERRADO'):
        raise ErrorOperador(f'El despacho {grupo.numero} ya se recibió.', repetida=True)
    if grupo.estado != 'EN_TRAYECTO':
        raise ErrorOperador('Este despacho todavía no salió (falta su guía consolidada).')
    if foto is None:
        raise ErrorOperador('Falta la foto del recibo.')
    validar_imagen(foto)
    adjunto = adjuntar(foto, 'Foto del recibo en la llegada a destino', usuario, consolidado=grupo)
    grupo.estado, grupo.recibido_en, grupo.recibido_por = 'EN_DESTINO', ahora, _usuario(usuario)
    grupo.latitud, grupo.longitud = _coord(latitud), _coord(longitud)
    grupo.save()
    for g in grupo.guias.select_related('estado'):
        cambiar_estado(g, 'POR_ENTREGAR', usuario, 'FOTO' if origen == 'ESCANEO' else origen,
                       f'Recepción en destino del consolidado {grupo.numero}', latitud, longitud, ahora)
    generar_ultimo_tramo(grupo, usuario, adjunto, ahora)
    return grupo


def generar_ultimo_tramo(grupo, usuario=None, foto_recibo=None, ahora=None):
    """Guía de la agencia de destino a la ubicación del cliente, con la guía del cliente, la consolidada y la del
    transportista como relacionadas y la foto del recibo adjunta."""
    cfg, empresa = ConfigOperador.actual(), _empresa()
    grupos = {}
    for g in grupo.guias.select_related('estado').filter(ultimo_tramo__isnull=True):
        clave = (g.destinatario_doc, g.llegada_ubigeo, g.llegada_direccion) if cfg.ultimo_tramo_por == 'DESTINATARIO' \
            else (g.pk,)
        grupos.setdefault(clave, []).append(g)
    emitidas = []
    for guias in grupos.values():
        primera = guias[0]
        relacionados = [('09', g.numero_completo, g.emisor_ruc) for g in guias]
        if grupo.guia_id:
            relacionados.append(('09', grupo.guia.numero_corto, empresa.empresa_ruc))
        if grupo.doc_transportista_tipo == '31' and grupo.doc_transportista_numero and grupo.transportista_id:
            relacionados.append(('31', grupo.doc_transportista_numero.upper(), grupo.transportista.ruc))
        gr = _nueva_guia('ULTIMO_TRAMO', grupo.llegada_ubigeo, grupo.llegada_direccion, primera.llegada_ubigeo,
                         primera.llegada_direccion, primera.destinatario_doc, primera.destinatario_nombre,
                         cfg.serie_ultimo_tramo, guias, relacionados,
                         observaciones=f'Último tramo del consolidado {grupo.numero}')
        if foto_recibo is not None:
            vincular(foto_recibo, 'Foto del recibo del transportista', usuario, guia_go=gr)
        for g in guias:
            g.ultimo_tramo = gr
            g.save(update_fields=['ultimo_tramo'])
            cambiar_estado(g, 'EN_REPARTO', usuario, 'SISTEMA', f'Guía del último tramo {gr.numero_completo}',
                           ahora=ahora)
        emitidas.append(gr)
    return emitidas


@transaction.atomic
def registrar_entrega(usuario, qr='', guia_id=None, foto=None, dni='', nombre='', firma='', latitud=None,
                      longitud=None, ahora=None):
    """Prueba de entrega: foto, DNI de quien recibe y firma en pantalla. Entrega juntas las guías del cliente que van
    en la misma guía del último tramo."""
    ahora = ahora or timezone.now()
    guia, _, origen = _guia_de_qr(qr, guia_id)
    if guia.estado.codigo == 'ENTREGADO':
        raise ErrorOperador('Esta guía ya fue entregada.', repetida=True)
    if guia.estado.codigo not in ('POR_ENTREGAR', 'EN_REPARTO'):
        raise ErrorOperador('Esta guía todavía no llegó a destino.')
    dni = solo_digitos(dni)
    if foto is None:
        raise ErrorOperador('Falta la foto de la entrega.')
    if len(dni) not in (8, 9, 11, 12):
        raise ErrorOperador('Falta el DNI de quien recibe (8 dígitos).')
    if firma and not firma.startswith('data:image/png;base64,'):
        firma = ''
    validar_imagen(foto)
    adjunto = adjuntar(foto, 'Foto de la entrega', usuario, guia=guia)
    juntas = [guia]
    if guia.ultimo_tramo_id:
        juntas += list(GuiaCliente.objects.filter(ultimo_tramo=guia.ultimo_tramo).exclude(pk=guia.pk)
                       .select_related('estado').exclude(estado__codigo='ENTREGADO'))
    for g in juntas:
        g.entregado_en, g.entregado_por = ahora, _usuario(usuario)
        g.receptor_dni, g.receptor_nombre, g.firma = dni[:15], (nombre or '')[:120], firma[:200000]
        g.entrega_latitud, g.entrega_longitud = _coord(latitud), _coord(longitud)
        g.save()
        if g is not guia:
            vincular(adjunto, 'Foto de la entrega', usuario, guia=g)
        cambiar_estado(g, 'ENTREGADO', usuario, origen, f'Recibió DNI {dni}{f" {nombre}" if nombre else ""}',
                       latitud, longitud, ahora)
    _cerrar_si_entregado(guia.consolidado)
    return guia


def _cerrar_si_entregado(grupo):
    if grupo is not None and grupo.estado == 'EN_DESTINO' and \
            not grupo.guias.exclude(estado__codigo='ENTREGADO').exists():
        grupo.estado = 'CERRADO'
        grupo.save(update_fields=['estado'])


# ---------------------------------------------------------------- verificación al escanear (sin cambiar nada)
PIDE_FOTO = {'LLEGADA': 'Tome la foto del recibo', 'ENTREGA': 'Tome la foto de la entrega'}


def verificar(usuario, paso, qr='', ident=None):
    """Lo que el celular muestra apenas escanea (verde o rojo) antes de «Confirmar». No cambia nada."""
    if paso not in PASOS:
        raise ErrorOperador('Elija qué está haciendo.')
    if paso == 'LLEGADA':
        grupo, _ = _consolidado_de_qr(qr, ident)
        if grupo.estado in ('EN_DESTINO', 'CERRADO'):
            raise ErrorOperador(f'El despacho {grupo.numero} ya se recibió.', repetida=True)
        if grupo.estado != 'EN_TRAYECTO':
            raise ErrorOperador('Este despacho todavía no salió (falta su guía consolidada).')
        return {'titulo': f'Despacho a {grupo.destino}', 'detalle': f'{grupo.numero} · {grupo.guias.count()} guía(s)',
                'foto': PIDE_FOTO['LLEGADA'], 'dni': False}
    guia, lectura, _ = _guia_de_qr(qr, ident, permitir_nueva=paso == 'RECOJO')
    if guia is None:  # recojo de una guía que aún no está en el sistema: se crea al confirmar
        cliente = _cuenta_por_ruc(lectura.ruc)
        sol = _solicitud_para(cliente, usuario) if cliente else _solicitud_para(None, usuario)
        if sol is None:
            raise ErrorOperador('No hay una solicitud de recojo abierta para esta guía. Avise a logística.')
        numero = f'{lectura.serie}-{lectura.numero}' if lectura.identifica_guia else 'del código escaneado'
        return {'titulo': (cliente or sol.cliente).nombre, 'detalle': f'Guía {numero} · solicitud {sol.numero}',
                'foto': '', 'dni': False}
    codigo = guia.estado.codigo
    if paso == 'RECOJO' and _posicion(codigo) >= _posicion('CONFIRMADO'):
        raise ErrorOperador(f'Esta guía ya fue recogida ({guia.estado.nombre}).', repetida=True)
    if paso == 'INGRESO':
        if _posicion(codigo) >= _posicion('EN_CUSTODIA'):
            raise ErrorOperador(f'Esta guía ya está en el almacén ({guia.estado.nombre}).', repetida=True)
        if codigo != 'CONFIRMADO':
            raise ErrorOperador('Esta guía todavía no se recogió en el almacén del cliente.')
    if paso == 'ENTREGA':
        if codigo == 'ENTREGADO':
            raise ErrorOperador('Esta guía ya fue entregada.', repetida=True)
        if codigo not in ('POR_ENTREGAR', 'EN_REPARTO'):
            raise ErrorOperador('Esta guía todavía no llegó a destino.')
        return {'titulo': guia.destinatario_nombre or 'Destinatario', 'detalle': f'Guía {guia.numero_completo} · '
                f'{guia.llegada_direccion[:60]}', 'foto': PIDE_FOTO['ENTREGA'], 'dni': True}
    return {'titulo': guia.emisor_nombre or guia.cliente.nombre,
            'detalle': f'Guía {guia.numero_completo} · {guia.bultos} bulto(s)'
                       f'{f" · {guia.destino}" if guia.destino else ""}', 'foto': '', 'dni': False}


def registrar(usuario, paso, qr='', ident=None, foto=None, dni='', firma='', latitud=None, longitud=None, ahora=None):
    """Confirma el paso del celular. Devuelve el texto que se le muestra al operador."""
    if paso == 'RECOJO':
        g = registrar_recojo(usuario, qr, ident, latitud, longitud, ahora)
        return f'Recogida: guía {g.numero_completo}'
    if paso == 'INGRESO':
        g = registrar_ingreso(usuario, qr, ident, latitud, longitud, ahora)
        return f'En custodia: guía {g.numero_completo}'
    if paso == 'LLEGADA':
        c = registrar_llegada(usuario, qr, ident, foto, latitud, longitud, ahora)
        return f'Recibido: {c.guias.count()} guía(s) del despacho {c.numero}'
    if paso == 'ENTREGA':
        g = registrar_entrega(usuario, qr, ident, foto, dni, '', firma, latitud, longitud, ahora)
        return f'Entregada: guía {g.numero_completo}'
    raise ErrorOperador('Elija qué está haciendo.')


# ---------------------------------------------------------------- listas cortas del celular
def pendientes(usuario, paso):
    """Lista corta de lo pendiente para elegir sin teclear cuando el QR no se lee."""
    if paso == 'RECOJO':
        qs = GuiaCliente.objects.filter(estado__codigo='ATENDIDO', solicitud__estado='ASIGNADO') \
            .filter(Q(solicitud__operador=usuario) | Q(solicitud__operador__isnull=True))
        return [{'id': g.pk, 'titulo': g.emisor_nombre or g.cliente.nombre, 'detalle': f'Guía {g.numero_completo} · '
                 f'{g.bultos} bulto(s)', 'qr': g.qr_texto, 'serie': g.serie, 'numero': g.numero}
                for g in qs.select_related('cliente').order_by('solicitud__creado')[:MAX_LISTA]]
    if paso == 'INGRESO':
        qs = GuiaCliente.objects.filter(estado__codigo='CONFIRMADO').order_by('-recogida_en')
        return [{'id': g.pk, 'titulo': g.emisor_nombre or g.cliente.nombre, 'detalle': f'Guía {g.numero_completo} · '
                 f'{g.destino or "sin destino"}', 'qr': g.qr_texto, 'serie': g.serie, 'numero': g.numero}
                for g in qs.select_related('cliente')[:MAX_LISTA]]
    if paso == 'LLEGADA':
        qs = Consolidado.objects.filter(estado='EN_TRAYECTO').select_related('guia').order_by('creado')
        return [{'id': c.pk, 'titulo': f'Despacho a {c.destino}', 'detalle': f'{c.numero} · guía '
                 f'{c.guia.numero_completo if c.guia_id else ""} · {c.guias.count()} guía(s)', 'qr': c.qr_texto,
                 'serie': c.guia.serie if c.guia_id else '', 'numero': str(c.guia.numero) if c.guia_id else ''}
                for c in qs[:MAX_LISTA]]
    if paso == 'ENTREGA':
        qs = GuiaCliente.objects.filter(estado__codigo__in=('POR_ENTREGAR', 'EN_REPARTO')).order_by('estado_desde')
        return [{'id': g.pk, 'titulo': g.destinatario_nombre or 'Destinatario', 'detalle':
                 f'{g.llegada_direccion[:60]} · guía {g.numero_completo}', 'qr': g.qr_texto, 'serie': g.serie,
                 'numero': g.numero} for g in qs[:MAX_LISTA]]
    return []


# ---------------------------------------------------------------- costo por guía (pago al transportista)
def costos(grupo):
    """Costos del transporte del consolidado, repartidos entre sus guías por peso (o por bultos si no hay peso).
    Devuelve (total S/, [(guía, costo)])."""
    total = sum((c.monto for c in CostoTransporte.objects.filter(consolidado=grupo)), D0)
    guias = list(grupo.guias.select_related('cliente'))
    base = sum((_peso_kg(g) for g in guias), D0)
    usar_peso = base > 0
    if not usar_peso:
        base = Decimal(sum(g.bultos or 1 for g in guias))
    filas = []
    for g in guias:
        parte = (_peso_kg(g) if usar_peso else Decimal(g.bultos or 1)) / base if base else D0
        filas.append((g, (total * parte).quantize(Decimal('0.01'))))
    return total, filas
