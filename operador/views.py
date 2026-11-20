"""Pantallas de escritorio del operador logístico (personal de operaciones): tablero, solicitudes, guías de los
clientes, planificación por destino, consolidados, bitácora, costo por guía, mensajes entrantes y configuración."""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from envios.models import Configuracion, Cuenta

from . import servicios as srv
from .forms import (ClienteLogisticoForm, ConfigForm, CostoForm, EmitirForm, EstadoForm, SolicitudForm,
                    TransportistaForm, models_q_operador)
from .models import (BitacoraEstado, ClienteLogistico, ConfigOperador, Consolidado, CostoTransporte, EstadoLogistico,
                     GuiaCliente, GuiaGo, MensajeEntrante, SolicitudRecojo)
from .permisos import oficina, puede_planificar
from .xml_guias import ErrorXML

MAX_XML = 5 * 1024 * 1024


def _pagina(request, qs, n=50):
    return Paginator(qs, n).get_page(request.GET.get('page'))


@oficina
def tablero(request):
    EstadoLogistico.cargar_base()
    conteo = dict(GuiaCliente.objects.values_list('estado').annotate(n=Count('id')))
    hoy = timezone.localdate()
    return render(request, 'operador/tablero.html', {
        'estados': [(e, conteo.get(e.pk, 0)) for e in EstadoLogistico.objects.filter(activo=True)],
        'solicitudes': SolicitudRecojo.objects.filter(estado='ASIGNADO').select_related('cliente', 'operador')[:20],
        'grupos': Consolidado.objects.filter(estado__in=('ARMANDO', 'CONFIRMADO')).annotate(n=Count('guias'))[:20],
        'en_trayecto': Consolidado.objects.filter(estado='EN_TRAYECTO').select_related('guia', 'transportista')[:20],
        'entregadas_hoy': GuiaCliente.objects.filter(entregado_en__date=hoy).count(),
        'pendientes_datos': GuiaCliente.objects.filter(datos_completos=False).exclude(estado__codigo='ENTREGADO').count(),
        'mensajes_sin_solicitud': MensajeEntrante.objects.exclude(resultado='SOLICITUD')
                                                         .filter(recibido__date__gte=hoy - timedelta(days=7)).count(),
    })


# ---------------------------------------------------------------- solicitudes
@oficina
def solicitudes(request):
    qs = SolicitudRecojo.objects.select_related('cliente', 'operador').annotate(n_guias=Count('guias'))
    estado = request.GET.get('estado', 'ASIGNADO')
    if estado in dict(SolicitudRecojo.ESTADOS):
        qs = qs.filter(estado=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(cliente__nombre__icontains=q) | Q(remitente__icontains=q))
    return render(request, 'operador/solicitudes.html', {
        'page_obj': _pagina(request, qs.order_by('-creado', '-id')), 'estado': estado, 'q': q,
        'estados': SolicitudRecojo.ESTADOS})


@oficina
def solicitud_nueva(request):
    form = SolicitudForm(request.POST or None, initial={'cliente': request.GET.get('cliente')})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        sol = srv.crear_solicitud(d['cliente'], 'MANUAL', '', '', d.get('mensaje') or '', request.user, unir=False,
                                  operador=d.get('operador'), almacen=d.get('almacen'), bultos=d.get('bultos'),
                                  contacto=d.get('contacto'), fecha=d.get('fecha_recojo'))
        messages.success(request, f'Solicitud {sol.numero} registrada.')
        return redirect('operador:solicitud', sol.pk)
    return render(request, 'operador/solicitud_nueva.html', {'form': form})


@oficina
def solicitud(request, pk):
    sol = get_object_or_404(SolicitudRecojo.objects.select_related('cliente', 'operador', 'atendida_por'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'asignar':
                sol.operador = User.objects.filter(models_q_operador(), pk=request.POST.get('operador') or 0,
                                                   is_active=True).first()
                sol.save(update_fields=['operador'])
                messages.success(request, 'Operador asignado.' if sol.operador else 'Operador quitado.')
            elif accion == 'xml':
                archivo = request.FILES.get('xml')
                if archivo is None:
                    raise srv.ErrorOperador('Adjunte el XML de la guía.')
                if archivo.size > MAX_XML:
                    raise srv.ErrorOperador('El XML supera los 5 MB.')
                guia, nueva = srv.registrar_guia_xml(archivo.read(), sol.cliente, sol, request.user)
                messages.success(request, f'Guía {guia.numero_completo} {"cargada" if nueva else "ya estaba"}.')
            elif accion == 'anular':
                if sol.estado != 'ASIGNADO':
                    raise srv.ErrorOperador('Solo se anula una solicitud que aún no se atendió.')
                srv._estado_solicitud(sol, 'ANULADA', request.user, 'PANTALLA',
                                      request.POST.get('motivo', '').strip()[:200] or 'Anulada')
                sol.save(update_fields=['estado'])
                messages.success(request, f'Solicitud {sol.numero} anulada.')
        except (srv.ErrorOperador, ErrorXML) as exc:
            messages.error(request, str(exc))
        return redirect('operador:solicitud', pk)
    return render(request, 'operador/solicitud.html', {
        'sol': sol, 'guias': sol.guias.select_related('estado', 'consolidado'),
        'bitacora': sol.bitacora.select_related('usuario'), 'adjuntos': sol.adjuntos.all(),
        'operadores': User.objects.filter(models_q_operador(), is_active=True).order_by('username')})


# ---------------------------------------------------------------- guías de los clientes
@oficina
def guias(request):
    qs = GuiaCliente.objects.select_related('estado', 'cliente', 'consolidado', 'ultimo_tramo')
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado__codigo=estado)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(numero__icontains=q) | Q(serie__icontains=q) | Q(cliente__nombre__icontains=q) |
                       Q(destinatario_nombre__icontains=q) | Q(emisor_ruc__icontains=q) |
                       Q(departamento__icontains=q) | Q(provincia__icontains=q))
    if request.GET.get('pendientes'):
        qs = qs.filter(datos_completos=False)
    return render(request, 'operador/guias.html', {
        'page_obj': _pagina(request, qs.order_by('-creado', '-id')), 'estado': estado, 'q': q,
        'pendientes': bool(request.GET.get('pendientes')), 'estados': EstadoLogistico.objects.all()})


@oficina
def guia(request, pk):
    g = get_object_or_404(GuiaCliente.objects.select_related('estado', 'cliente', 'solicitud', 'consolidado__guia',
                                                             'ultimo_tramo'), pk=pk)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'estado':  # cambio manual (ej. «Actualizar»): queda en la bitácora
                nuevo = get_object_or_404(EstadoLogistico, pk=request.POST.get('estado') or 0, activo=True)
                srv.cambiar_estado(g, nuevo.codigo, request.user, 'PANTALLA',
                                   request.POST.get('detalle', '').strip()[:200] or 'Cambio manual')
                messages.success(request, f'Estado: {nuevo.nombre}.')
            elif accion == 'xml':
                archivo = request.FILES.get('xml')
                if archivo is None:
                    raise srv.ErrorOperador('Adjunte el XML de la guía.')
                if archivo.size > MAX_XML:
                    raise srv.ErrorOperador('El XML supera los 5 MB.')
                nueva, _ = srv.registrar_guia_xml(archivo.read(), g.cliente, g.solicitud, request.user)
                if nueva.pk != g.pk:
                    raise srv.ErrorOperador(f'El XML es de otra guía ({nueva.numero_completo}): se cargó aparte.')
                messages.success(request, 'Datos de la guía completados con su XML.')
        except (srv.ErrorOperador, ErrorXML) as exc:
            messages.error(request, str(exc))
        return redirect('operador:guia', pk)
    return render(request, 'operador/guia.html', {
        'g': g, 'bitacora': g.bitacora.select_related('usuario', 'estado'), 'adjuntos': g.adjuntos.all(),
        'estados': EstadoLogistico.objects.filter(activo=True)})


# ---------------------------------------------------------------- planificación y consolidados
@oficina
def planificacion(request):
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'mover':
                g = get_object_or_404(GuiaCliente, pk=request.POST.get('guia') or 0)
                destino = request.POST.get('grupo', '')
                grupo = None if destino == 'nuevo' else get_object_or_404(Consolidado, pk=destino or 0)
                grupo = srv.mover_guia(g, grupo, request.user)
                messages.success(request, f'Guía {g.numero_completo} movida a {grupo.numero}.')
            elif accion == 'confirmar':
                grupo = srv.confirmar_grupo(get_object_or_404(Consolidado, pk=request.POST.get('grupo') or 0),
                                            request.user)
                messages.success(request, f'Grupo {grupo.numero} confirmado: ya puede emitir su guía consolidada.')
                return redirect('operador:consolidado', grupo.pk)
            elif accion == 'custodia':
                n = 0
                for g in GuiaCliente.objects.filter(pk__in=request.POST.getlist('guias'), estado__codigo='CONFIRMADO'):
                    srv.cambiar_estado(g, 'EN_CUSTODIA', request.user, 'PANTALLA', 'Ingreso al almacén (logística)')
                    n += 1
                messages.success(request, f'{n} guía(s) en custodia.')
        except srv.ErrorOperador as exc:
            messages.error(request, str(exc))
        return redirect('operador:planificacion')
    grupos, sin_destino = srv.tablero()
    return render(request, 'operador/planificacion.html', {
        'grupos': grupos, 'sin_destino': sin_destino, 'abiertos': [g for g, _ in grupos]})


@oficina
def consolidados(request):
    qs = Consolidado.objects.select_related('guia', 'transportista').annotate(n=Count('guias'))
    estado = request.GET.get('estado', '')
    qs = qs.filter(estado=estado) if estado in dict(Consolidado.ESTADOS) else qs.exclude(estado='ANULADO')
    return render(request, 'operador/consolidados.html', {
        'page_obj': _pagina(request, qs.order_by('-creado', '-id')), 'estado': estado,
        'estados': Consolidado.ESTADOS})


@oficina
def consolidado(request, pk):
    c = get_object_or_404(Consolidado.objects.select_related('guia', 'transportista'), pk=pk)
    accion = request.POST.get('accion') if request.method == 'POST' else None
    emitir = EmitirForm(request.POST if accion == 'emitir' else None,
                        initial={'fecha': timezone.localdate(), 'transportista': c.transportista_id,
                                 'agencia_nombre': c.agencia_nombre, 'agencia_ruc': c.agencia_ruc,
                                 'llegada_ubigeo': c.llegada_ubigeo, 'llegada_direccion': c.llegada_direccion})
    transporte = TransportistaForm(request.POST if accion == 'transportista' else None,
                                   request.FILES if accion == 'transportista' else None,
                                   initial={'transportista': c.transportista_id, 'tipo': c.doc_transportista_tipo or '31',
                                            'numero': c.doc_transportista_numero, 'fecha': c.doc_transportista_fecha,
                                            'peso': c.doc_transportista_peso, 'bultos': c.doc_transportista_bultos})
    costo = CostoForm(request.POST if accion == 'costo' else None)
    if accion:
        try:
            if accion == 'confirmar':
                srv.confirmar_grupo(c, request.user)
                messages.success(request, 'Grupo confirmado.')
            elif accion == 'emitir':
                if not emitir.is_valid():
                    raise srv.ErrorOperador('Revise los datos de la guía consolidada.')
                d = emitir.cleaned_data
                gr = srv.emitir_consolidada(c, request.user, d['agencia_nombre'], d['agencia_ruc'], d['llegada_ubigeo'],
                                            d['llegada_direccion'], d['transportista'], d['fecha'])
                messages.success(request, f'Guía consolidada {gr.numero_completo} emitida con {c.guias.count()} '
                                          f'guía(s) relacionadas. Las guías están En trayecto.')
            elif accion == 'transportista':
                if not transporte.is_valid():
                    raise srv.ErrorOperador('Revise los datos del transportista.')
                d = transporte.cleaned_data
                if d['archivo'] is not None:
                    srv.validar_documento(d['archivo'])
                srv.registrar_transportista(c, request.user, d['transportista'], d['tipo'], d['numero'], d['fecha'],
                                            d['peso'], d['bultos'], '', d['archivo'])
                messages.success(request, 'Documento del transportista ligado a la guía consolidada.')
            elif accion == 'costo':
                if not costo.is_valid():
                    raise srv.ErrorOperador('Revise el concepto y el monto.')
                CostoTransporte.objects.create(consolidado=c, usuario=request.user, **costo.cleaned_data)
                messages.success(request, 'Costo registrado y repartido entre las guías.')
            elif accion == 'quitar_costo':
                CostoTransporte.objects.filter(consolidado=c, pk=request.POST.get('costo') or 0).delete()
                messages.success(request, 'Costo quitado.')
            return redirect('operador:consolidado', pk)
        except srv.ErrorOperador as exc:
            messages.error(request, str(exc))
            if accion not in ('emitir', 'transportista', 'costo'):
                return redirect('operador:consolidado', pk)
    total, por_guia = srv.costos(c)
    return render(request, 'operador/consolidado.html', {
        'c': c, 'guias': c.guias.select_related('estado', 'cliente', 'ultimo_tramo'), 'emitir': emitir,
        'transporte': transporte, 'costo_form': costo, 'total_costo': total,
        'costos': {g.pk: v for g, v in por_guia}, 'lineas_costo': c.costos.select_related('usuario'),
        'adjuntos': c.adjuntos.all(),
        'ultimos': GuiaGo.objects.filter(guias_cliente__consolidado=c).distinct().prefetch_related('relacionados')})


@oficina
def hoja_despacho(request, pk):
    """Hoja que viaja con el despacho: su QR lo escanea el operador de destino."""
    c = get_object_or_404(Consolidado.objects.select_related('guia', 'transportista'), pk=pk)
    return render(request, 'operador/hoja_despacho.html', {
        'c': c, 'guias': c.guias.select_related('cliente'), 'empresa': Configuracion.get()})


@oficina
def guia_go(request, pk):
    """Guía remitente de Go (consolidada o del último tramo) con sus documentos relacionados."""
    gr = get_object_or_404(GuiaGo.objects.select_related('transportista'), pk=pk)
    return render(request, 'operador/guia_go.html', {
        'gr': gr, 'relacionados': gr.relacionados.all(), 'adjuntos': gr.adjuntos.all(), 'empresa': Configuracion.get(),
        'consolidado': Consolidado.objects.filter(guia=gr).first(),
        'guias_cliente': gr.guias_cliente.select_related('cliente')})


# ---------------------------------------------------------------- reportes
@oficina
def bitacora(request):
    qs = BitacoraEstado.objects.select_related('guia', 'solicitud', 'usuario', 'estado')
    try:
        desde = date.fromisoformat(request.GET.get('desde', ''))
    except ValueError:
        desde = timezone.localdate() - timedelta(days=7)
    qs = qs.filter(fecha__date__gte=desde)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(guia__numero__icontains=q) | Q(solicitud__numero__icontains=q) | Q(detalle__icontains=q) |
                       Q(usuario__username__icontains=q))
    return render(request, 'operador/bitacora.html', {
        'page_obj': _pagina(request, qs.order_by('-fecha', '-id'), 100), 'desde': desde, 'q': q})


@oficina
def costos(request):
    filas = []
    for c in Consolidado.objects.filter(guia__isnull=False).select_related('guia').order_by('-creado')[:100]:
        total, por_guia = srv.costos(c)
        filas.append((c, total, por_guia))
    return render(request, 'operador/costos.html', {'filas': filas})


@oficina
def mensajes(request):
    """Correos y WhatsApp recibidos: los que no crearon solicitud (remitente no autorizado o cliente sin crédito)
    quedan aquí para que operaciones los revise."""
    qs = MensajeEntrante.objects.select_related('solicitud')
    resultado = request.GET.get('resultado', 'PENDIENTE')
    if resultado == 'PENDIENTE':
        qs = qs.exclude(resultado='SOLICITUD')
    elif resultado in dict(MensajeEntrante.RESULTADOS):
        qs = qs.filter(resultado=resultado)
    return render(request, 'operador/mensajes.html', {
        'page_obj': _pagina(request, qs.order_by('-recibido', '-id')), 'resultado': resultado,
        'resultados': MensajeEntrante.RESULTADOS})


# ---------------------------------------------------------------- configuración
@oficina
def clientes(request):
    qs = Cuenta.objects.filter(tipo='empresa')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(documento__icontains=q))
    elif not request.GET.get('todos'):  # por defecto, los que ya usan el servicio o tienen crédito
        qs = qs.filter(Q(servicio_logistico__isnull=False) | Q(linea_credito__gt=0))
    qs = qs.select_related('servicio_logistico', 'user').annotate(n_autorizados=Count('servicio_logistico__autorizados'))
    return render(request, 'operador/clientes.html', {
        'page_obj': _pagina(request, qs.order_by('nombre')), 'q': q, 'todos': bool(request.GET.get('todos'))})


@oficina
def cliente(request, pk):
    cuenta = get_object_or_404(Cuenta.objects.select_related('user'), pk=pk)
    perfil = ClienteLogistico.objects.filter(cuenta=cuenta).first() or ClienteLogistico(cuenta=cuenta)
    form = ClienteLogisticoForm(request.POST or None, instance=perfil)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Servicio logístico de {cuenta.nombre} guardado.')
        return redirect('operador:cliente', pk)
    return render(request, 'operador/cliente.html', {'form': form, 'cuenta': cuenta, 'perfil': perfil})


@oficina
def estados(request):
    EstadoLogistico.cargar_base()
    if request.method == 'POST':
        e = get_object_or_404(EstadoLogistico, pk=request.POST.get('estado') or 0)
        form = EstadoForm(request.POST, instance=e, prefix=f'e{e.pk}')
        if form.is_valid():
            form.save()
            if e.inicial:  # un solo estado inicial
                EstadoLogistico.objects.exclude(pk=e.pk).update(inicial=False)
            messages.success(request, f'Estado «{e.nombre}» guardado.')
        else:
            messages.error(request, 'Revise los datos del estado.')
        return redirect('operador:estados')
    return render(request, 'operador/estados.html', {
        'filas': [(e, EstadoForm(instance=e, prefix=f'e{e.pk}')) for e in EstadoLogistico.objects.all()]})


@login_required
def configuracion(request):
    """Encender el módulo y sus datos de emisión. Solo staff (la única pantalla que se ve con el módulo apagado)."""
    if not puede_planificar(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    cfg = ConfigOperador.actual()
    form = ConfigForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        if cfg.activo:
            EstadoLogistico.cargar_base()
        messages.success(request, 'Configuración guardada.' + (' El operador logístico está activo.' if cfg.activo
                                                                else ' El operador logístico está apagado.'))
        return redirect('operador:configuracion')
    return render(request, 'operador/configuracion.html', {
        'form': form, 'cfg': cfg, 'empresa': Configuracion.get()})
