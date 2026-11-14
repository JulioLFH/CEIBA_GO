import json
import secrets
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import erp, servicios
from .context import rol_de
from .forms import PedidoForm, RegistroForm
from .models import Agente, Configuracion, Cuenta, Pago, Pedido, SyncERP, Transportista
from .servicios import FlujoError
from .tarifas import cotizar


def requiere(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def inner(request, *a, **kw):
            if rol_de(request.user) not in roles:
                return HttpResponseForbidden("No tiene acceso a esta pantalla.")
            return fn(request, *a, **kw)
        return inner
    return deco


def _accion(request, fn, *args, ok="Listo.", **kw):
    """Ejecuta un paso del flujo y muestra el resultado; devuelve True si avanzo."""
    try:
        fn(*args, **kw)
        messages.success(request, ok)
        return True
    except FlujoError as e:
        messages.error(request, str(e))
        return False


# ======================================================================= Publico
def home(request):
    return render(request, "envios/home.html")


def registro(request):
    form = RegistroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            u = User.objects.create_user(d["usuario"], d["email"], d["clave"], first_name=d["nombre"][:150])
            Cuenta.objects.create(user=u, tipo=d["tipo"], documento=d["documento"], nombre=d["nombre"],
                                  telefono=d["telefono"], direccion=d["direccion"])
        login(request, u)
        messages.success(request, "Cuenta creada. Ya puede armar su primer envío.")
        return redirect("cliente_panel")
    return render(request, "envios/registro.html", {"form": form})


@login_required
def inicio(request):
    destino = {"operador": "ops_panel", "agente": "agente_panel", "transportista": "transp_panel", "cliente": "cliente_panel"}
    r = rol_de(request.user)
    if not r:
        messages.error(request, "Su usuario no tiene un rol en Ceiba GO.")
        return redirect("home")
    return redirect(destino[r])


def seguimiento(request, codigo=None):
    codigo = (codigo or request.GET.get("codigo", "")).strip().upper()
    pedido = None
    if codigo:
        pedido = Pedido.objects.filter(codigo=codigo).first()
        if not pedido:
            messages.error(request, f"No encontramos el envío {codigo}.")
    ctx = {"codigo": codigo, "pedido": pedido}
    if pedido:
        ctx["eventos"] = pedido.eventos.filter(publico=True).order_by("-timestamp", "-id")
        ctx["pasos"] = _pasos(pedido)
    return render(request, "envios/seguimiento.html", ctx)


def _pasos(pedido):
    actual = pedido.paso_flujo
    return [{"clave": c, "nombre": dict(Pedido.ESTADOS)[c], "hecho": i < actual or pedido.estado == "entregado",
             "actual": i == actual and pedido.estado != "entregado"} for i, c in enumerate(Pedido.FLUJO)]


@require_POST
def api_cotizar(request):
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "JSON inválido"}, status=400)
    if not d.get("origen") or not d.get("destino"):
        return JsonResponse({"ok": False, "error": "Elija departamento de origen y de destino."})
    cot = cotizar(d["origen"], d["destino"], d.get("paquetes", []), "USD" if d.get("moneda") == "USD" else "PEN")
    cot.pop("paquetes")
    return JsonResponse(cot)


def api_documento(request):
    """Autocompletado: DNI -> nombres (RENIEC); RUC -> razon social y direccion fiscal (SUNAT)."""
    from django.core.cache import cache

    from .padron import ErrorPadron, consultar
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
    clave = f"padron-ip-{ip}"
    usados = cache.get(clave, 0)
    if usados >= 40 and not request.user.is_authenticated:  # cuida el cupo del plan ante abusos
        return JsonResponse({"ok": False, "error": "Demasiadas consultas; intente en unos minutos."}, status=429)
    cache.set(clave, usados + 1, 3600)
    try:
        return JsonResponse({"ok": True, **consultar(request.GET.get("numero", ""))})
    except ErrorPadron as e:
        return JsonResponse({"ok": False, "error": str(e)})


def _puede_ver(user, pedido):
    r = rol_de(user)
    return r in ("operador", "agente", "transportista") or (r == "cliente" and pedido.cuenta.user_id == user.id)


@login_required
def comprobante(request, codigo):
    p = get_object_or_404(Pedido, codigo=codigo)
    if not _puede_ver(request.user, p) or not hasattr(p, "comprobante"):
        raise Http404
    return render(request, "envios/comprobante.html", {"p": p, "c": p.comprobante, "cfg": Configuracion.get()})


@login_required
def guia(request, codigo, tipo):
    p = get_object_or_404(Pedido, codigo=codigo)
    g = p.guias.filter(tipo=tipo.upper()).order_by("-emitida").first()
    if not _puede_ver(request.user, p) or not g:
        raise Http404
    return render(request, "envios/guia.html", {"p": p, "g": g, "cfg": Configuracion.get()})


# ======================================================================= Cliente (pasos 1-4)
@requiere("cliente")
def cliente_panel(request):
    cuenta = request.user.cuenta
    pedidos = cuenta.pedidos.all()
    activos = pedidos.exclude(estado__in=["entregado", "anulado", "devuelto"])
    return render(request, "envios/cliente_panel.html", {
        "cuenta": cuenta, "activos": activos, "historial": pedidos.filter(estado__in=["entregado", "anulado", "devuelto"])[:20],
    })


@requiere("cliente")
def cliente_nuevo(request):
    form = PedidoForm(request.POST or None, initial={"moneda": "PEN", "origen_contacto": request.user.cuenta.nombre,
                                                     "origen_telefono": request.user.cuenta.telefono})
    filas = []
    if request.method == "POST":
        g = request.POST.getlist
        for i in range(len(g("descripcion"))):
            filas.append({k: (g(k)[i] if i < len(g(k)) else "") for k in
                          ("descripcion", "cantidad", "peso_kg", "largo_cm", "ancho_cm", "alto_cm", "valor_declarado", "fragil")})
        if form.is_valid():
            datos = {k: v for k, v in form.cleaned_data.items() if k != "moneda"}
            try:
                p = servicios.crear_pedido(request.user.cuenta, datos, filas, form.cleaned_data["moneda"])
                return redirect("cliente_pagar", codigo=p.codigo)
            except FlujoError as e:
                messages.error(request, str(e))
    return render(request, "envios/cliente_nuevo.html", {"form": form, "filas": filas or [{"cantidad": 1}]})


@requiere("cliente")
def cliente_pagar(request, codigo):
    p = get_object_or_404(Pedido, codigo=codigo, cuenta__user=request.user)
    if p.estado != "creado":
        return redirect("cliente_pedido", codigo=codigo)
    if request.method == "POST":
        metodo = request.POST.get("metodo", "")
        accion = request.POST.get("accion")
        if accion == "anular":
            if _accion(request, servicios.anular, p, request.POST.get("motivo") or "Cancelado por el cliente",
                       actor=request.user.cuenta.nombre, ok="Envío cancelado."):
                return redirect("cliente_panel")
        else:
            # Pasarela de pagos (simulada): devuelve el numero de operacion aprobado
            ref = "" if metodo == "credito" else f"{metodo[:2].upper()}-{secrets.token_hex(4).upper()}"
            if _accion(request, servicios.pagar, p, metodo, ref,
                       ok="Pago registrado: comprobante y guía de remisión emitidos."):
                return redirect("cliente_pedido", codigo=codigo)
    return render(request, "envios/cliente_pagar.html", {"p": p, "cuenta": request.user.cuenta})


@requiere("cliente")
def cliente_pedido(request, codigo):
    p = get_object_or_404(Pedido, codigo=codigo, cuenta__user=request.user)
    return render(request, "envios/pedido_detalle.html", _ctx_detalle(request, p))


def _ctx_detalle(request, p):
    return {"p": p, "pasos": _pasos(p), "eventos": p.eventos.order_by("-timestamp", "-id"),
            "tramos": p.tramos.select_related("agente", "transportista", "guia"), "guias": p.guias.all(),
            "pagos": p.pagos.all(), "seguimiento_url": request.build_absolute_uri(f"/s/{p.codigo}/")}


# ======================================================================= Agentes (pasos 8, 11, 12)
@requiere("agente")
def agente_panel(request):
    a = request.user.agente
    recojos = Pedido.objects.filter(estado="recojo_asignado", tramos__tipo="recojo", tramos__agente=a)
    por_recibir = Pedido.objects.filter(estado="en_transito", destino_departamento=a.zona) if a.rol != "recojo" else Pedido.objects.none()
    repartos = Pedido.objects.filter(estado__in=["en_destino", "reprogramado", "en_reparto"], tramos__tipo="entrega", tramos__agente=a)
    sin_asignar = Pedido.objects.filter(estado__in=["en_destino"], destino_departamento=a.zona, tramos__tipo="entrega",
                                        tramos__agente__isnull=True) if a.rol != "recojo" else Pedido.objects.none()
    hechos = Pedido.objects.filter(Q(tramos__agente=a), estado__in=["entregado", "recogido", "en_transito"]).distinct()[:15]
    return render(request, "envios/agente_panel.html", {"a": a, "recojos": recojos, "por_recibir": por_recibir,
                                                         "repartos": repartos, "sin_asignar": sin_asignar, "hechos": hechos})


@requiere("agente", "transportista")
def escanear(request):
    """Lee el QR de una guia y lleva al pedido correspondiente."""
    if request.method == "POST":
        try:
            g = servicios.leer_qr(request.POST.get("qr"))
            destino = "agente_pedido" if rol_de(request.user) == "agente" else "transp_panel"
            if destino == "transp_panel":
                return redirect(f"/transporte/?qr={g.qr_texto}")
            return redirect(f"/agente/pedido/{g.pedido.codigo}/?qr={g.qr_texto}")
        except FlujoError as e:
            messages.error(request, str(e))
    return render(request, "envios/escanear.html")


@requiere("agente")
def agente_pedido(request, codigo):
    a = request.user.agente
    p = get_object_or_404(Pedido, codigo=codigo)
    if request.method == "POST":
        acc, P, F = request.POST.get("accion"), request.POST, request.FILES
        if acc == "recojo":
            _accion(request, servicios.registrar_recojo, p, a, P.get("qr"), P.get("peso_real") or None,
                    F.get("foto"), P.get("observaciones", ""), ok="Recojo registrado.")
        elif acc == "recepcion":
            _accion(request, servicios.recepcionar_destino, p, a, P.get("qr"), P.get("almacenar") == "on",
                    P.get("ubicacion", ""), ok="Recepción registrada en destino.")
        elif acc == "tomar":
            _accion(request, servicios.asignar_reparto, p, a, actor=a.nombre, ok="Entrega asignada a usted.")
        elif acc == "reparto":
            _accion(request, servicios.iniciar_reparto, p, a, ok="Salió a reparto.")
        elif acc == "entrega":
            _accion(request, servicios.entregar, p, a, P.get("qr"), P.get("receptor_nombre", "").strip(),
                    P.get("receptor_documento", "").strip(), P.get("firma", ""), F.get("foto"),
                    ok="Entrega registrada. Pedido cerrado.")
        elif acc == "fallido":
            _accion(request, servicios.registrar_fallido, p, a, P.get("motivo", "").strip(), ok="Entrega fallida registrada.")
        return redirect("agente_pedido", codigo=codigo)
    ctx = _ctx_detalle(request, p)
    ctx.update({"a": a, "qr": request.GET.get("qr", ""), "t_recojo": p.tramo("recojo"), "t_entrega": p.tramo("entrega")})
    return render(request, "envios/agente_pedido.html", ctx)


# ======================================================================= Transporte (paso 10)
@requiere("transportista")
def transp_panel(request):
    t = request.user.transportista
    if request.method == "POST":
        p = get_object_or_404(Pedido, codigo=request.POST.get("codigo"), tramos__transportista=t)
        _accion(request, servicios.registrar_en_ruta, p, request.POST.get("qr"), request.POST.get("ubicacion", "").strip(),
                t.razon_social, ok="Punto de control registrado.")
        return redirect("transp_panel")
    qr = request.GET.get("qr", "")
    sel = ""
    if qr:
        try:
            g = servicios.leer_qr(qr)
            sel = g.pedido.codigo
        except FlujoError as e:
            messages.error(request, str(e))
    en_ruta = Pedido.objects.filter(estado="en_transito", tramos__tipo="linea", tramos__transportista=t)
    hechos = Pedido.objects.filter(tramos__tipo="linea", tramos__transportista=t).exclude(estado="en_transito")[:15]
    return render(request, "envios/transp_panel.html", {"t": t, "en_ruta": en_ruta, "hechos": hechos, "qr": qr, "sel": sel})


# ======================================================================= Operaciones (pasos 7, 9, 13, 14)
@requiere("operador")
def ops_panel(request):
    conteo = dict(Pedido.objects.values_list("estado").annotate(n=Count("id")))
    ctx = {
        "estados": [(c, n, conteo.get(c, 0)) for c, n in Pedido.ESTADOS],
        "por_asignar": Pedido.objects.filter(estado="pagado"),
        "por_transporte": Pedido.objects.filter(estado="recogido"),
        "en_destino_sin_agente": Pedido.objects.filter(estado__in=["en_destino", "reprogramado"], tramos__tipo="entrega", tramos__agente__isnull=True),
        "fallidos": Pedido.objects.filter(estado="fallido"),
        "por_cobrar": Pago.objects.filter(estado="por_cobrar").select_related("pedido__cuenta"),
        "erp_pendientes": SyncERP.objects.exclude(estado="enviado").count(),
        "cfg": Configuracion.get(),
        "recientes": Pedido.objects.select_related("cuenta")[:12],
    }
    return render(request, "envios/ops_panel.html", ctx)


@requiere("operador")
def ops_pedidos(request):
    qs = Pedido.objects.select_related("cuenta")
    estado, q = request.GET.get("estado", ""), request.GET.get("q", "").strip()
    if estado:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(Q(codigo__icontains=q) | Q(cuenta__nombre__icontains=q) | Q(destinatario_nombre__icontains=q)
                       | Q(destino_departamento__icontains=q) | Q(origen_departamento__icontains=q))
    return render(request, "envios/ops_pedidos.html", {"pedidos": qs[:200], "estado": estado, "q": q, "ESTADOS": Pedido.ESTADOS})


@requiere("operador")
def ops_pedido(request, codigo):
    p = get_object_or_404(Pedido, codigo=codigo)
    actor = request.user.get_full_name() or request.user.username
    if request.method == "POST":
        acc, P = request.POST.get("accion"), request.POST
        if acc == "asignar_recojo":
            _accion(request, servicios.asignar_recojo, p, get_object_or_404(Agente, pk=P.get("agente")), actor=actor, ok="Recojo asignado.")
        elif acc == "transporte":
            _accion(request, servicios.entregar_a_transporte, p, get_object_or_404(Transportista, pk=P.get("transportista")),
                    P.get("registro", "manual"), P.get("placa", "").strip(), P.get("conductor", "").strip(), actor=actor,
                    ok="GRE Transportista emitida y paquete entregado a transporte.")
        elif acc == "asignar_reparto":
            _accion(request, servicios.asignar_reparto, p, get_object_or_404(Agente, pk=P.get("agente")), actor=actor, ok="Entrega asignada.")
        elif acc == "reprogramar":
            try:
                fecha = date.fromisoformat(P.get("fecha", ""))
            except ValueError:
                messages.error(request, "Indique una fecha válida.")
            else:
                _accion(request, servicios.reprogramar, p, fecha, actor=actor, ok="Entrega reprogramada.")
        elif acc == "devolver":
            _accion(request, servicios.devolver, p, P.get("motivo", "").strip() or "Sin motivo", actor=actor, ok="Envío marcado como devuelto.")
        elif acc == "anular":
            _accion(request, servicios.anular, p, P.get("motivo", "").strip(), actor=actor,
                    ok="Pedido anulado y baja comunicada a SUNAT.")
        elif acc == "cobranza":
            pago = get_object_or_404(Pago, pk=P.get("pago"), pedido=p)
            _accion(request, servicios.registrar_cobranza, pago, P.get("referencia", "").strip() or "Cobranza", actor=actor,
                    ok="Cobranza registrada.")
        return redirect("ops_pedido", codigo=codigo)
    ctx = _ctx_detalle(request, p)
    ctx.update({
        "agentes_origen": Agente.objects.filter(activo=True, rol__in=["recojo", "ambos"]).order_by("-zona", "nombre"),
        "agentes_destino": Agente.objects.filter(activo=True, rol__in=["destino", "ambos"]),
        "sugerido_origen": servicios.sugerir_agente(p.origen_departamento, "recojo"),
        "sugerido_destino": servicios.sugerir_agente(p.destino_departamento, "destino"),
        "transportistas": Transportista.objects.filter(activo=True),
        "sync": p.sync_erp.all(), "hoy": date.today().isoformat(),
    })
    return render(request, "envios/ops_pedido.html", ctx)


@requiere("operador")
def ops_erp(request):
    if request.method == "POST":
        ok, err, sin = erp.sincronizar()
        if sin:
            messages.warning(request, f"Modo independiente: no hay URL de Ceiba ERP configurada. {sin} registros quedan guardados para sincronizar al reconectar.")
        else:
            (messages.success if not err else messages.warning)(request, f"Sincronización: {ok} enviados, {err} con error.")
        return redirect("ops_erp")
    estado = request.GET.get("estado", "")
    qs = SyncERP.objects.select_related("pedido")
    if estado:
        qs = qs.filter(estado=estado)
    resumen = dict(SyncERP.objects.values_list("estado").annotate(n=Count("id")))
    return render(request, "envios/ops_erp.html", {"items": qs[:300], "estado": estado, "resumen": resumen, "cfg": Configuracion.get()})
