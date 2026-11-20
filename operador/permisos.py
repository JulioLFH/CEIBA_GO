"""Quién hace qué en el operador logístico de Ceiba GO.

- operar (app del celular: recojo, ingreso, llegada, entrega): agentes y personal de operaciones (staff).
- planificar y configurar (pantallas de escritorio, consolidar, emitir guías, clientes, estados): staff.
Con el módulo apagado nadie lo ve, salvo la configuración para encenderlo."""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def puede_operar(user):
    return user.is_authenticated and user.is_active and (user.is_staff or hasattr(user, 'agente'))


def puede_planificar(user):
    return user.is_authenticated and user.is_active and user.is_staff


def _activo():
    from .models import ConfigOperador
    return ConfigOperador.actual().activo


def oficina(vista):
    """Pantalla de escritorio: solo staff y con el módulo activo (si está apagado, lleva a la configuración)."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not puede_planificar(request.user):
            raise PermissionDenied
        if not _activo():
            return redirect('operador:configuracion')
        return vista(request, *args, **kwargs)
    return envoltura


def celular(vista):
    """App del operador: agentes y staff, con el módulo activo."""
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if not puede_operar(request.user) or not _activo():
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura
