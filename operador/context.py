def operador(request):
    """Para el menú de Ceiba GO: si el operador logístico está activo y el usuario puede usarlo."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    try:
        from .models import ConfigOperador
        from .permisos import puede_operar, puede_planificar
        activo = ConfigOperador.actual().activo
    except Exception:  # noqa: BLE001  (base sin migrar: el menú de siempre)
        return {}
    return {'ol_activo': activo, 'ol_oficina': activo and puede_planificar(user),
            'ol_celular': activo and puede_operar(user), 'ol_config': puede_planificar(user)}
