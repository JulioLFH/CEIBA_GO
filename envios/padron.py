"""Consulta de DNI (RENIEC) y RUC (SUNAT) via Decolecta, la misma cuenta que usa Ceiba ERP.

GET https://api.decolecta.com/v1/reniec/dni?numero={dni}   (Authorization: Bearer {token})
GET https://api.decolecta.com/v1/sunat/ruc?numero={ruc}
El token se registra en Configuracion (o en la variable de entorno DECOLECTA_TOKEN).
"""
import json
import logging
import os
import unicodedata
import urllib.error
import urllib.request

from django.core.cache import cache

from .models import DEPARTAMENTOS, Configuracion

URL_DNI = "https://api.decolecta.com/v1/reniec/dni?numero={n}"
URL_RUC = "https://api.decolecta.com/v1/sunat/ruc?numero={n}"
MENSAJES = {401: "el token de Decolecta no es válido", 404: "no se encontró ese número",
            422: "no se encontró ese número", 429: "se agotaron las consultas del plan de Decolecta"}
log = logging.getLogger(__name__)
_DEPS = {d for d, _ in DEPARTAMENTOS}


class ErrorPadron(Exception):
    pass


def _texto(v):
    return " ".join(str(v or "").split())


def _departamento(v):
    d = unicodedata.normalize("NFKD", _texto(v).upper()).encode("ascii", "ignore").decode()
    return d if d in _DEPS else ""


def _pedir(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                               "User-Agent": "Ceiba-GO"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def validar(numero):
    numero = (numero or "").strip()
    if not numero.isdigit() or len(numero) not in (8, 11):
        raise ErrorPadron("Ingrese un DNI de 8 dígitos o un RUC de 11.")
    if len(numero) == 11:
        if numero[:2] not in ("10", "15", "17", "20"):
            raise ErrorPadron("El RUC debe empezar con 10, 15, 17 o 20.")
        pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
        resto = 11 - sum(int(a) * b for a, b in zip(numero[:10], pesos)) % 11
        if (resto - 10 if resto >= 10 else resto) != int(numero[10]):
            raise ErrorPadron("El RUC no es válido (dígito verificador).")
    return numero


def consultar(numero):
    """{'tipo', 'numero', 'nombre', 'direccion', 'distrito', 'departamento', 'alerta'} o ErrorPadron."""
    numero = validar(numero)
    clave = f"padron-{numero}"
    datos = cache.get(clave)
    if datos:
        return datos
    token = (Configuracion.get().token_decolecta or os.environ.get("DECOLECTA_TOKEN", "")).strip()
    if not token:
        raise ErrorPadron("Falta el token de Decolecta para consultar DNI/RUC (Configuración).")
    es_ruc = len(numero) == 11
    try:
        r = _pedir((URL_RUC if es_ruc else URL_DNI).format(n=numero), token)
    except urllib.error.HTTPError as e:
        raise ErrorPadron(f"No se pudo consultar: {MENSAJES.get(e.code, f'error {e.code}')}.") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log.info("Consulta DNI/RUC %s no disponible: %s", numero, e)
        raise ErrorPadron("El servicio de consulta DNI/RUC no responde. Complete los datos a mano.") from e

    if es_ruc:
        estado, condicion = _texto(r.get("estado")).upper(), _texto(r.get("condicion")).upper()
        alertas = [x for x in (estado if estado not in ("", "ACTIVO") else "",
                               condicion if condicion not in ("", "HABIDO") else "") if x]
        datos = {"tipo": "ruc", "numero": numero, "nombre": _texto(r.get("razon_social")),
                 "direccion": _texto(r.get("direccion")), "distrito": _texto(r.get("distrito")).title(),
                 "departamento": _departamento(r.get("departamento")),
                 "alerta": f"RUC {' · '.join(alertas)} en SUNAT" if alertas else ""}
    else:
        partes = [r.get("first_name"), r.get("first_last_name"), r.get("second_last_name")]
        nombre = _texto(" ".join(p for p in partes if p)) or _texto(r.get("full_name"))
        datos = {"tipo": "dni", "numero": numero, "nombre": nombre.title(), "direccion": "",
                 "distrito": "", "departamento": "", "alerta": ""}
    if not datos["nombre"]:
        raise ErrorPadron("No se encontraron datos para ese número.")
    cache.set(clave, datos, 86400)
    return datos
