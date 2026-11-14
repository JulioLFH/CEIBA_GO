"""Orquestador de tramos: los 14 pasos del flujo como funciones con sus reglas.

Regla transversal: en todo tramo el paquete va con una guia electronica valida y aceptada.
Sin guia (o si el QR escaneado no es la guia del tramo) el tramo no avanza.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import erp, sunat
from .models import Agente, Configuracion, EventoTracking, Guia, Paquete, Pago, Pedido, Tramo
from .tarifas import cotizar


class FlujoError(Exception):
    pass


def evento(pedido, estado, descripcion, ubicacion="", actor="", publico=True):
    EventoTracking.objects.create(pedido=pedido, estado=estado, descripcion=descripcion,
                                  ubicacion=ubicacion, actor=actor, publico=publico)


def _estado(pedido, estado, descripcion, ubicacion="", actor=""):
    pedido.estado = estado
    pedido.save(update_fields=["estado", "actualizado", "motivo_excepcion", "cerrado_en", "fecha_reprogramada"])
    evento(pedido, estado, descripcion, ubicacion, actor)


def _exigir(pedido, *estados):
    if pedido.estado not in estados:
        raise FlujoError(f"El pedido {pedido.codigo} está «{pedido.get_estado_display()}»; "
                         f"este paso necesita: {', '.join(dict(Pedido.ESTADOS)[e] for e in estados)}.")


def leer_qr(texto):
    """Encuentra la guia a partir del QR escaneado (o del numero/ token digitado)."""
    texto = (texto or "").strip().upper()
    if not texto:
        raise FlujoError("Escanee el QR de la guía (o digite su número).")
    partes = texto.split("|")
    if partes[0] == "CEIBAGO" and len(partes) >= 5:
        g = Guia.objects.filter(qr_token=partes[-1]).first()
    else:
        g = Guia.objects.filter(qr_token=texto).first()
        m = re.match(r"^([TV]\d{3})-?0*(\d+)$", texto)
        if not g and m:
            g = Guia.objects.filter(serie=m.group(1), numero=int(m.group(2))).first()
    if not g:
        raise FlujoError("El QR no corresponde a ninguna guía de Ceiba GO.")
    return g


def _validar_guia(texto, pedido, tipos):
    g = leer_qr(texto)
    if g.pedido_id != pedido.id:
        raise FlujoError(f"La guía {g.numero_completo} es del pedido {g.pedido.codigo}, no de {pedido.codigo}.")
    if g.tipo not in tipos:
        raise FlujoError(f"Se esperaba la guía {' o '.join(tipos)}; se escaneó una {g.tipo}.")
    if g.sunat_estado != "aceptada":
        raise FlujoError(f"La guía {g.numero_completo} no está aceptada por SUNAT ({g.get_sunat_estado_display()}). Sin guía válida el tramo no avanza.")
    return g


def _lugar(pedido, lado):
    return f"{getattr(pedido, lado + '_distrito')}, {getattr(pedido, lado + '_departamento').title()}"


# ---------------------------------------------------------------- F1 · pasos 2 y 3
@transaction.atomic
def crear_pedido(cuenta, datos, filas_paquetes, moneda="PEN"):
    """Paso 2 (arma el envio) + paso 3 (cotiza): crea el pedido con la tarifa congelada."""
    cot = cotizar(datos["origen_departamento"], datos["destino_departamento"], filas_paquetes, moneda)
    if not cot["paquetes"]:
        raise FlujoError("Agregue al menos un paquete con cantidad y peso.")
    p = Pedido(cuenta=cuenta, moneda=moneda, **datos)
    p.tipo_cambio = Decimal(cot["tipo_cambio"])
    p.peso_real_kg = Decimal(cot["peso_real_kg"])
    p.volumen_m3 = Decimal(cot["volumen_m3"])
    p.peso_facturable_kg = Decimal(cot["peso_facturable_kg"])
    for campo in ("flete", "seguro", "recargos", "subtotal", "igv", "total"):
        setattr(p, campo, Decimal(cot[campo]))
    p.dias_estimados = cot["dias"]
    p.centro_costo = f"FLETE-{p.origen_departamento[:3]}-{p.destino_departamento[:3]}"
    p.save()
    for f in cot["paquetes"]:
        Paquete.objects.create(pedido=p, **f)
    evento(p, "creado", f"Envío creado y cotizado: {p.simbolo} {p.total} ({cot['regla']}).",
           _lugar(p, "origen"), cuenta.nombre)
    return p


# ---------------------------------------------------------------- F2 · pasos 4, 5 y 6
@transaction.atomic
def pagar(pedido, metodo, referencia=""):
    """Paso 4 (paga o usa credito) -> paso 5 (comprobante + GRE-R) -> paso 6 (venta/CxC al ERP) -> paso 7."""
    pedido = Pedido.objects.select_for_update().get(pk=pedido.pk)
    _exigir(pedido, "creado")
    cuenta = pedido.cuenta
    if metodo == "credito":
        if not cuenta.es_empresa:
            raise FlujoError("El pago a crédito es solo para empresas con RUC y línea aprobada.")
        if pedido.total_pen > cuenta.credito_disponible:
            raise FlujoError(f"Línea de crédito insuficiente: disponible S/ {cuenta.credito_disponible:.2f}.")
        pago = Pago.objects.create(pedido=pedido, metodo="credito", monto=pedido.total, moneda=pedido.moneda,
                                   tipo_cambio=pedido.tipo_cambio, estado="por_cobrar",
                                   vence=timezone.localdate() + timedelta(days=30), referencia=referencia)
    else:
        if metodo not in dict(Pago.METODOS):
            raise FlujoError("Método de pago no válido.")
        if not referencia:
            raise FlujoError("La pasarela no devolvió número de operación.")
        pago = Pago.objects.create(pedido=pedido, metodo=metodo, monto=pedido.total, moneda=pedido.moneda,
                                   tipo_cambio=pedido.tipo_cambio, estado="aprobado", referencia=referencia)

    # Paso 5: comprobante electronico + GRE Remitente
    comp = sunat.emitir_comprobante(pedido, "credito" if metodo == "credito" else "contado")
    gre_r = sunat.emitir_guia(pedido, "GRE-R")
    _crear_tramos(pedido, gre_r)
    _estado(pedido, "pagado",
            f"Pago {pago.get_metodo_display()} registrado. {comp.get_tipo_display()} {comp.numero_completo} y GRE-R {gre_r.numero_completo} emitidas.",
            _lugar(pedido, "origen"), "Ceiba GO")

    # Paso 6: el ERP registra la venta y, si es a credito, la cuenta por cobrar
    erp.encolar(pedido, "venta", {
        "comprobante": comp.numero_completo, "tipo": comp.tipo, "cliente_documento": comp.cliente_documento,
        "cliente": comp.cliente_nombre, "moneda": comp.moneda, "tipo_cambio": comp.tipo_cambio,
        "subtotal": comp.subtotal, "igv": comp.igv, "total": comp.total, "forma_pago": comp.forma_pago,
        "centro_costo": pedido.centro_costo, "fecha": comp.emitido,
    })
    erp.encolar(pedido, "gre", {"tipo": "GRE-R", "numero": gre_r.numero_completo, "peso_kg": gre_r.peso_bruto_kg,
                                "partida": gre_r.punto_partida, "llegada": gre_r.punto_llegada})
    if metodo == "credito":
        erp.encolar(pedido, "cxc", {"cliente_documento": cuenta.documento, "comprobante": comp.numero_completo,
                                    "moneda": pago.moneda, "monto": pago.monto, "vence": pago.vence})

    # Paso 7: asigna el recojo a un agente propio de la zona de origen
    agente = sugerir_agente(pedido.origen_departamento, "recojo")
    if agente:
        asignar_recojo(pedido, agente, actor="Ceiba GO (automático)")
    return pedido


def _crear_tramos(pedido, gre_r):
    Tramo.objects.create(pedido=pedido, tipo="recojo", orden=1, guia=gre_r)
    if pedido.origen_departamento != pedido.destino_departamento:
        Tramo.objects.create(pedido=pedido, tipo="linea", orden=2)
    Tramo.objects.create(pedido=pedido, tipo="entrega", orden=3, guia=gre_r)


def sugerir_agente(departamento, rol):
    roles = ["ambos", "recojo"] if rol == "recojo" else ["ambos", "destino"]
    activos = Q(tramos__estado__in=["asignado", "en_curso"])
    return (Agente.objects.filter(activo=True, zona=departamento, rol__in=roles)
            .annotate(carga=Count("tramos", filter=activos)).order_by("carga", "id").first())


# ---------------------------------------------------------------- F3 · pasos 7 y 8
@transaction.atomic
def asignar_recojo(pedido, agente, actor="Operaciones"):
    _exigir(pedido, "pagado", "recojo_asignado")
    t = pedido.tramo("recojo")
    t.agente, t.estado = agente, "asignado"
    t.save()
    _estado(pedido, "recojo_asignado", f"Recojo asignado a {agente.nombre}.", _lugar(pedido, "origen"), actor)


@transaction.atomic
def registrar_recojo(pedido, agente, qr, peso_real=None, foto=None, observaciones=""):
    """Paso 8: el agente escanea el QR de la GRE-R, confirma peso/volumen real y deja evidencia."""
    _exigir(pedido, "recojo_asignado")
    t = pedido.tramo("recojo")
    if t.agente_id != agente.id:
        raise FlujoError("Este recojo está asignado a otro agente.")
    _validar_guia(qr, pedido, ["GRE-R"])
    if not foto:
        raise FlujoError("Adjunte la foto de evidencia del recojo.")
    if peso_real:
        peso_real = Decimal(str(peso_real))
        if peso_real <= 0:
            raise FlujoError("El peso confirmado debe ser mayor que cero.")
        dif = peso_real - pedido.peso_real_kg
        pedido.peso_real_kg = peso_real
        pedido.save(update_fields=["peso_real_kg"])
        if abs(dif) > Decimal("0.5"):
            observaciones = (observaciones + f" · Peso real difiere de lo declarado en {dif:+.2f} kg.").strip(" ·")
    t.estado, t.registro, t.inicio, t.fin = "completado", "qr", t.inicio or timezone.now(), timezone.now()
    t.evidencia_foto, t.observaciones = foto, observaciones[:250]
    t.save()
    _estado(pedido, "recogido", f"Paquete recogido por {agente.nombre} (QR de GRE-R escaneado"
            f"{', ' + str(pedido.peso_real_kg) + ' kg confirmados' if peso_real else ''}).",
            _lugar(pedido, "origen"), agente.nombre)
    if pedido.origen_departamento == pedido.destino_departamento:
        # Envio dentro del mismo departamento: no hay linea, pasa directo a destino
        _recibir_en_destino(pedido, agente, almacenar=False, ubicacion="", registro="qr")


# ---------------------------------------------------------------- F4 · pasos 9 y 10
@transaction.atomic
def entregar_a_transporte(pedido, transportista, registro="manual", placa="", conductor="", actor="Operaciones"):
    """Paso 9: emite la GRE Transportista y entrega el paquete a la empresa de transporte."""
    _exigir(pedido, "recogido")
    t = pedido.tramo("linea")
    if not t:
        raise FlujoError("Este envío no tiene tramo interprovincial.")
    if registro == "api" and not transportista.tiene_api:
        raise FlujoError(f"{transportista} no tiene API; use registro manual o QR del agente.")
    gre_t = sunat.emitir_guia(pedido, "GRE-T", transportista)
    if placa or conductor:
        gre_t.placa, gre_t.conductor = placa or gre_t.placa, conductor or gre_t.conductor
        gre_t.save(update_fields=["placa", "conductor"])
    t.transportista, t.guia, t.estado, t.registro, t.inicio = transportista, gre_t, "en_curso", registro, timezone.now()
    t.save()
    _estado(pedido, "en_transito",
            f"Entregado a {transportista.razon_social} con GRE-T {gre_t.numero_completo} "
            f"({dict(Tramo.REGISTRO)[registro].lower()}).", _lugar(pedido, "origen"), actor)
    erp.encolar(pedido, "gre", {"tipo": "GRE-T", "numero": gre_t.numero_completo, "transportista_ruc": transportista.ruc,
                                "transportista": transportista.razon_social, "placa": gre_t.placa})
    erp.encolar(pedido, "costo", {"centro_costo": pedido.centro_costo, "concepto": "Flete interprovincial",
                                  "proveedor_ruc": transportista.ruc, "peso_kg": pedido.peso_real_kg})
    return gre_t


def registrar_en_ruta(pedido, qr, ubicacion, actor):
    """Paso 10: QR en ruta. El transportista registra puntos de control del traslado."""
    _exigir(pedido, "en_transito")
    _validar_guia(qr, pedido, ["GRE-T"])
    if not ubicacion:
        raise FlujoError("Indique la ubicación del punto de control.")
    evento(pedido, "en_transito", f"Punto de control en ruta: {ubicacion}.", ubicacion, actor)


# ---------------------------------------------------------------- F5 · pasos 11 y 12
@transaction.atomic
def recepcionar_destino(pedido, agente, qr, almacenar=True, ubicacion=""):
    """Paso 11: el agente de destino escanea el QR de la GRE-T y almacena si hace falta."""
    _exigir(pedido, "en_transito")
    if agente.zona != pedido.destino_departamento:
        raise FlujoError("Solo un agente de la zona de destino puede recepcionar.")
    _validar_guia(qr, pedido, ["GRE-T"])
    t = pedido.tramo("linea")
    t.estado, t.fin = "completado", timezone.now()
    t.save()
    _recibir_en_destino(pedido, agente, almacenar, ubicacion, "qr")


def _recibir_en_destino(pedido, agente, almacenar, ubicacion, registro):
    e = pedido.tramo("entrega")
    e.agente = e.agente or (agente if agente.zona == pedido.destino_departamento else sugerir_agente(pedido.destino_departamento, "destino"))
    e.estado = "asignado" if e.agente else "pendiente"
    e.almacenado, e.ubicacion_almacen = almacenar, ubicacion[:60]
    e.save()
    texto = f"Recepcionado en destino por {agente.nombre}"
    texto += f"; almacenado en {ubicacion}." if almacenar and ubicacion else ("; almacenado." if almacenar else ".")
    _estado(pedido, "en_destino", texto, _lugar(pedido, "destino"), agente.nombre)
    if almacenar:
        erp.encolar(pedido, "almacen", {"movimiento": "ingreso", "ubicacion": ubicacion, "bultos": pedido.total_bultos,
                                        "peso_kg": pedido.peso_real_kg, "almacen": pedido.destino_departamento})


@transaction.atomic
def asignar_reparto(pedido, agente, actor="Operaciones"):
    _exigir(pedido, "en_destino", "reprogramado", "fallido")
    e = pedido.tramo("entrega")
    e.agente, e.estado = agente, "asignado"
    e.save()
    evento(pedido, pedido.estado, f"Entrega final asignada a {agente.nombre}.", _lugar(pedido, "destino"), actor, publico=False)


@transaction.atomic
def iniciar_reparto(pedido, agente):
    _exigir(pedido, "en_destino", "reprogramado")
    e = pedido.tramo("entrega")
    if e.agente_id != agente.id:
        raise FlujoError("La entrega está asignada a otro agente.")
    e.estado, e.inicio = "en_curso", timezone.now()
    e.save()
    if e.almacenado:
        erp.encolar(pedido, "almacen", {"movimiento": "salida", "ubicacion": e.ubicacion_almacen,
                                        "bultos": pedido.total_bultos, "almacen": pedido.destino_departamento})
    _estado(pedido, "en_reparto", f"En reparto con {agente.nombre}.", _lugar(pedido, "destino"), agente.nombre)


@transaction.atomic
def entregar(pedido, agente, qr, receptor_nombre, receptor_documento, firma, foto=None):
    """Paso 12 (POD) -> paso 13 (cierra el pedido) -> paso 14 (concilia en el ERP)."""
    _exigir(pedido, "en_reparto")
    e = pedido.tramo("entrega")
    if e.agente_id != agente.id:
        raise FlujoError("La entrega está asignada a otro agente.")
    _validar_guia(qr, pedido, ["GRE-R", "GRE-T"])
    if not receptor_nombre or not receptor_documento:
        raise FlujoError("Registre nombre y documento de quien recibe.")
    if not firma or not firma.startswith("data:image"):
        raise FlujoError("Falta la firma de quien recibe (prueba de entrega).")
    e.estado, e.registro, e.fin = "completado", "qr", timezone.now()
    e.receptor_nombre, e.receptor_documento, e.firma = receptor_nombre[:120], receptor_documento[:11], firma
    if foto:
        e.evidencia_foto = foto
    e.save()
    _estado(pedido, "entregado", f"Entregado a {receptor_nombre} (doc. {receptor_documento}) con prueba de entrega.",
            _lugar(pedido, "destino"), agente.nombre)
    cerrar(pedido)


def cerrar(pedido):
    """Paso 13 y 14: cierra la trazabilidad y concilia venta, cobranza/credito y almacen en el ERP."""
    pedido.cerrado_en = timezone.now()
    pedido.save(update_fields=["cerrado_en"])
    evento(pedido, "entregado", "Pedido cerrado: trazabilidad completa.", actor="Ceiba GO", publico=False)
    pago = pedido.pagos.exclude(estado__in=["rechazado", "extornado"]).first()
    comp = pedido.comprobante_vigente
    erp.encolar(pedido, "cierre", {
        "comprobante": comp.numero_completo if comp else None, "total": pedido.total, "moneda": pedido.moneda,
        "pago": {"metodo": pago.metodo, "estado": pago.estado} if pago else None,
        "almacenado": bool(pedido.tramo("entrega") and pedido.tramo("entrega").almacenado),
        "centro_costo": pedido.centro_costo, "tramos": [
            {"tipo": t.tipo, "ejecutor": str(t.ejecutor or ""), "inicio": t.inicio, "fin": t.fin} for t in pedido.tramos.all()],
        "cerrado": pedido.cerrado_en,
    })


# ---------------------------------------------------------------- Excepciones
@transaction.atomic
def registrar_fallido(pedido, agente, motivo):
    _exigir(pedido, "en_reparto")
    if not motivo:
        raise FlujoError("Indique el motivo de la entrega fallida.")
    e = pedido.tramo("entrega")
    e.estado, e.intentos, e.observaciones = "fallido", e.intentos + 1, motivo[:250]
    e.save()
    pedido.motivo_excepcion = motivo[:200]
    _estado(pedido, "fallido", f"Entrega fallida: {motivo}.", _lugar(pedido, "destino"), agente.nombre)


@transaction.atomic
def reprogramar(pedido, fecha, actor="Operaciones"):
    _exigir(pedido, "fallido")
    e = pedido.tramo("entrega")
    e.estado = "asignado"
    e.save()
    pedido.fecha_reprogramada = fecha
    _estado(pedido, "reprogramado", f"Entrega reprogramada para el {fecha:%d/%m/%Y}.", _lugar(pedido, "destino"), actor)


@transaction.atomic
def devolver(pedido, motivo, actor="Operaciones"):
    _exigir(pedido, "fallido", "en_destino", "reprogramado")
    pedido.motivo_excepcion = motivo[:200]
    pedido.cerrado_en = timezone.now()
    _estado(pedido, "devuelto", f"Envío devuelto al remitente: {motivo}.", _lugar(pedido, "destino"), actor)


@transaction.atomic
def anular(pedido, motivo, actor="Operaciones"):
    """Anulacion antes del recojo: baja del comprobante y guias en SUNAT, extorno/liberacion y aviso al ERP."""
    _exigir(pedido, "creado", "pagado", "recojo_asignado")
    if not motivo:
        raise FlujoError("Indique el motivo de la anulación.")
    comp = pedido.comprobante_vigente
    if comp:
        sunat.comunicar_baja_comprobante(comp)
    for g in pedido.guias.exclude(sunat_estado="baja"):
        sunat.comunicar_baja_guia(g)
    for p in pedido.pagos.filter(estado__in=["aprobado", "por_cobrar"]):
        p.estado = "extornado"
        p.save(update_fields=["estado"])
    pedido.tramos.exclude(estado="completado").update(estado="fallido")
    pedido.motivo_excepcion = motivo[:200]
    pedido.cerrado_en = timezone.now()
    _estado(pedido, "anulado", f"Pedido anulado: {motivo}.", actor=actor)
    if comp:
        erp.encolar(pedido, "baja", {"comprobante": comp.numero_completo, "motivo": motivo,
                                     "guias": [g.numero_completo for g in pedido.guias.all()]})


@transaction.atomic
def registrar_cobranza(pago, referencia, actor="Operaciones"):
    if pago.estado != "por_cobrar":
        raise FlujoError("Ese pago no está pendiente de cobro.")
    pago.estado, pago.referencia = "cobrado", referencia[:60]
    pago.save(update_fields=["estado", "referencia"])
    evento(pago.pedido, pago.pedido.estado, f"Cobranza del crédito registrada ({referencia}).", actor=actor, publico=False)
    erp.encolar(pago.pedido, "cobranza", {"comprobante": pago.pedido.comprobante.numero_completo,
                                          "monto": pago.monto, "moneda": pago.moneda, "referencia": referencia})
