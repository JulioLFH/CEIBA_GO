"""Ceiba GO <-> Ceiba ERP (seccion 06).

Todo lo que el ERP necesita se encola en SyncERP (venta, GRE, CxC, almacen, costo,
cobranza, baja y cierre). Modo standalone: si no hay URL o no hay conexion, la cola queda
pendiente y se reintenta al reconectar (comando `manage.py sincronizar_erp` o boton en Operaciones).
"""
import json
import urllib.error
import urllib.request

from django.utils import timezone

from .models import Configuracion, SyncERP


def encolar(pedido, tipo, payload):
    payload = {"origen": "ceiba_go", "pedido": pedido.codigo if pedido else None, **payload}
    item = SyncERP.objects.create(pedido=pedido, tipo=tipo, payload=json.loads(json.dumps(payload, default=str)))
    if Configuracion.get().modo_erp == "integrado":
        enviar(item)
    return item


def enviar(item, cfg=None):
    cfg = cfg or Configuracion.get()
    if not cfg.erp_url:
        return False
    url = cfg.erp_url.rstrip("/") + f"/api/ceibago/{item.tipo}/"
    req = urllib.request.Request(
        url, data=json.dumps(item.payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Token {cfg.erp_token}"},
    )
    item.intentos += 1
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            item.respuesta = r.read(2000).decode("utf-8", "replace")
            item.estado = "enviado"
            item.enviado = timezone.now()
    except (urllib.error.URLError, OSError, ValueError) as e:
        item.estado = "error"
        item.respuesta = str(e)[:500]
    item.save()
    return item.estado == "enviado"


def sincronizar():
    """Envia la cola pendiente en orden. Devuelve (enviados, fallidos, sin_conexion)."""
    cfg = Configuracion.get()
    pendientes = SyncERP.objects.exclude(estado="enviado").order_by("creado")
    if not cfg.erp_url:
        return 0, 0, pendientes.count()
    ok = err = 0
    for item in pendientes:
        if enviar(item, cfg):
            ok += 1
        else:
            err += 1
    return ok, err, 0
