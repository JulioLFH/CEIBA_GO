from io import BytesIO

import qrcode
import qrcode.image.svg
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def qr_svg(texto, tam=160):
    img = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    svg = svg[svg.find("<svg"):]
    return mark_safe(svg.replace("<svg ", f'<svg width="{tam}" height="{tam}" class="qr" ', 1))


@register.filter
def dinero(valor, simbolo="S/"):
    try:
        return f"{simbolo} {float(valor):,.2f}"
    except (TypeError, ValueError):
        return ""


ESTADO_COLOR = {
    "creado": "gris", "pagado": "azul", "recojo_asignado": "azul", "recogido": "naranja",
    "en_transito": "morado", "en_destino": "naranja", "en_reparto": "naranja", "entregado": "verde",
    "fallido": "rojo", "devuelto": "rojo", "reprogramado": "ambar", "anulado": "rojo",
}


@register.filter
def color_estado(estado):
    return ESTADO_COLOR.get(estado, "gris")
