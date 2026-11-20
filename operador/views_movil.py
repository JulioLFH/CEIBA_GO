"""App del operador en el celular: una acción por pantalla («Escanear» y luego «Confirmar»), letra grande, nada que
digitar (salvo el DNI de quien recibe), verde con sonido si está bien y rojo con el motivo si no, y sin señal guarda
todo en el teléfono y lo envía al volver la conexión. También el webhook de WhatsApp (Meta) del Bloque 1."""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import servicios as srv
from .permisos import celular, puede_planificar
from .xml_guias import ErrorXML

log = logging.getLogger(__name__)
VERSION_APP = '1.0.0'


def _json(datos, status=200):
    return JsonResponse(datos, status=status, json_dumps_params={'ensure_ascii': False})


@celular
def movil(request):
    datos = {
        'usuario': request.user.get_full_name() or request.user.username,
        'pasos': [{'clave': k, 'nombre': v} for k, v in srv.PASOS.items()],
        'pide_foto': srv.PIDE_FOTO,
        'puede': True,
        'urls': {'pendientes': reverse('operador:movil_pendientes'),
                 'verificar': reverse('operador:movil_verificar'),
                 'registrar': reverse('operador:movil_registrar'),
                 'sistema': reverse('operador:tablero') if puede_planificar(request.user) else reverse('inicio')},
    }
    return render(request, 'operador/movil.html', {'datos': datos, 'version': VERSION_APP})


@celular
@require_GET
def pendientes(request):
    paso = request.GET.get('paso', '')
    return _json({'paso': paso, 'items': srv.pendientes(request.user, paso)})


def _ident(request):
    valor = request.POST.get('id', '')
    return int(valor) if valor.isdigit() else None


@celular
@require_POST
def verificar(request):
    try:
        info = srv.verificar(request.user, request.POST.get('paso', ''), request.POST.get('qr', ''), _ident(request))
    except srv.ErrorOperador as exc:
        return _json({'ok': False, 'repetida': exc.repetida, 'mensaje': str(exc)})
    return _json({'ok': True, **info})


def _cuando(texto):
    """Hora en que el operador escaneó (si el envío se demoró por falta de señal). Nunca en el futuro."""
    try:
        valor = datetime.fromisoformat((texto or '').replace('Z', '+00:00'))
    except ValueError:
        return None
    if timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    ahora = timezone.now()
    return valor if valor <= ahora else ahora


@celular
@require_POST
def registrar(request):
    P = request.POST
    try:
        mensaje = srv.registrar(request.user, P.get('paso', ''), P.get('qr', ''), _ident(request),
                                request.FILES.get('foto'), P.get('dni', ''), P.get('firma', ''), P.get('latitud'),
                                P.get('longitud'), _cuando(P.get('cuando')))
    except srv.ErrorOperador as exc:
        # un envío repetido (se reintentó sin señal) ya quedó registrado: para el celular está bien
        return _json({'ok': exc.repetida, 'repetida': exc.repetida, 'mensaje': str(exc), 'id_local': P.get('id_local')})
    except ErrorXML as exc:
        return _json({'ok': False, 'mensaje': str(exc), 'id_local': P.get('id_local')})
    return _json({'ok': True, 'mensaje': mensaje, 'id_local': P.get('id_local')})


# ---------------------------------------------------------------- WhatsApp (webhook de Meta)
@csrf_exempt
def whatsapp(request):
    """GET: verificación del webhook (WHATSAPP_VERIFY_TOKEN). POST: mensajes, firmados por Meta con
    WHATSAPP_APP_SECRET (X-Hub-Signature-256). Los secretos vienen del entorno, nunca del código."""
    if request.method == 'GET':
        token = os.environ.get('WHATSAPP_VERIFY_TOKEN', '')
        if (token and request.GET.get('hub.mode') == 'subscribe'
                and hmac.compare_digest(request.GET.get('hub.verify_token', ''), token)):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponseForbidden()
    if request.method != 'POST':
        return HttpResponse(status=405)
    secreto = os.environ.get('WHATSAPP_APP_SECRET', '')
    firma = request.headers.get('X-Hub-Signature-256', '')
    esperada = 'sha256=' + hmac.new(secreto.encode(), request.body, hashlib.sha256).hexdigest() if secreto else ''
    if not secreto or not hmac.compare_digest(firma, esperada):
        return HttpResponseForbidden()
    try:
        cuerpo = json.loads(request.body or b'{}')
    except ValueError:
        return HttpResponse(status=400)
    from .canales import whatsapp_a_solicitud
    for entrada in cuerpo.get('entry', []) or []:
        for cambio in entrada.get('changes', []) or []:
            for mensaje in (cambio.get('value') or {}).get('messages', []) or []:
                try:
                    whatsapp_a_solicitud(mensaje)
                except Exception:  # noqa: BLE001  (Meta reintenta si no respondemos 200: se registra y se sigue)
                    log.exception('WhatsApp: no se pudo procesar el mensaje %s', mensaje.get('id'))
    return HttpResponse('ok', content_type='text/plain')
