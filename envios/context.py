def rol_de(user):
    if not user.is_authenticated:
        return None
    if user.is_staff:
        return "operador"
    if hasattr(user, "agente"):
        return "agente"
    if hasattr(user, "transportista"):
        return "transportista"
    if hasattr(user, "cuenta"):
        return "cliente"
    return None


def rol(request):
    return {"rol": rol_de(request.user)}
