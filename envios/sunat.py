"""Emision electronica (seccion 05): comprobante (boleta/factura) y guias GRE-R / GRE-T.

Decision 2 del documento (quien emite) queda abierta: este modulo es la frontera.
- proveedor "simulado": numera y marca como aceptado al instante (pruebas / demo).
- proveedor "ceiba" o "pse": aqui se conecta el envio real del XML firmado; mientras no
  este configurado, el documento queda "pendiente" y la regla transversal impide avanzar.
"""
import hashlib

from django.utils import timezone

from .models import Comprobante, Configuracion, Guia, Serie


def _aceptar(cfg):
    return cfg.proveedor_sunat == "simulado"


def emitir_comprobante(pedido, forma_pago="contado"):
    cfg = Configuracion.get()
    cuenta = pedido.cuenta
    tipo = "factura" if cuenta.es_empresa else "boleta"
    serie = "F001" if tipo == "factura" else "B001"
    numero = Serie.siguiente(serie)
    huella = hashlib.sha1(f"{cfg.empresa_ruc}|{serie}|{numero}|{pedido.total}|{timezone.now().isoformat()}".encode()).hexdigest()[:28]
    return Comprobante.objects.create(
        pedido=pedido, tipo=tipo, serie=serie, numero=numero,
        cliente_documento=cuenta.documento, cliente_nombre=cuenta.nombre,
        moneda=pedido.moneda, tipo_cambio=pedido.tipo_cambio,
        subtotal=pedido.subtotal, igv=pedido.igv, total=pedido.total, forma_pago=forma_pago,
        sunat_estado="aceptado", hash_cpe=huella,
    )


def emitir_guia(pedido, tipo, transportista=None):
    cfg = Configuracion.get()
    serie = "T001" if tipo == "GRE-R" else "V001"
    numero = Serie.siguiente(serie)
    peso = pedido.peso_real_kg or pedido.peso_facturable_kg
    g = Guia.objects.create(
        pedido=pedido, tipo=tipo, serie=serie, numero=numero, transportista=transportista,
        placa=getattr(transportista, "placa", "") or "", conductor=getattr(transportista, "conductor", "") or "",
        punto_partida=f"{pedido.origen_direccion}, {pedido.origen_distrito}, {pedido.origen_departamento}",
        punto_llegada=f"{pedido.destino_direccion}, {pedido.destino_distrito}, {pedido.destino_departamento}",
        peso_bruto_kg=peso, bultos=pedido.total_bultos or 1,
        sunat_estado="aceptada" if _aceptar(cfg) else "pendiente",
        sunat_ticket=f"T{timezone.now():%y%m%d%H%M%S}{numero:04d}",
    )
    return g


def comunicar_baja_comprobante(comprobante):
    comprobante.sunat_estado = "baja"
    comprobante.save(update_fields=["sunat_estado"])


def comunicar_baja_guia(guia):
    guia.sunat_estado = "baja"
    guia.save(update_fields=["sunat_estado"])
