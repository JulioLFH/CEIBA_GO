from django import forms
from django.contrib.auth.models import User

from .models import Cuenta, Pedido


class RegistroForm(forms.Form):
    tipo = forms.ChoiceField(choices=Cuenta.TIPOS, widget=forms.RadioSelect, initial="persona")
    documento = forms.CharField(label="DNI (persona) o RUC (empresa)", max_length=11, widget=forms.TextInput(attrs={
        "data-doc": "1", "data-doc-nombre": "id_nombre", "data-doc-direccion": "id_direccion", "data-doc-tipo": "tipo",
        "inputmode": "numeric", "autocomplete": "off", "placeholder": "Escriba su DNI o RUC y se completan sus datos"}))
    nombre = forms.CharField(label="Nombre completo o razón social", max_length=150)
    telefono = forms.CharField(max_length=20)
    direccion = forms.CharField(label="Dirección fiscal", max_length=200, required=False)
    email = forms.EmailField(label="Correo")
    usuario = forms.CharField(max_length=30)
    clave = forms.CharField(label="Contraseña", widget=forms.PasswordInput, min_length=6)

    def clean_usuario(self):
        u = self.cleaned_data["usuario"].strip()
        if User.objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("Ese usuario ya existe.")
        return u

    def clean(self):
        d = super().clean()
        doc, tipo = (d.get("documento") or "").strip(), d.get("tipo")
        if tipo == "empresa" and not (len(doc) == 11 and doc.isdigit() and doc[:2] in ("10", "15", "17", "20")):
            self.add_error("documento", "Ingrese un RUC válido de 11 dígitos.")
        if tipo == "persona" and not (len(doc) == 8 and doc.isdigit()):
            self.add_error("documento", "Ingrese un DNI de 8 dígitos.")
        return d


class PedidoForm(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = [
            "origen_direccion", "origen_distrito", "origen_departamento", "origen_referencia",
            "origen_contacto", "origen_telefono",
            "destino_direccion", "destino_distrito", "destino_departamento", "destino_referencia",
            "destinatario_nombre", "destinatario_documento", "destinatario_telefono", "moneda",
            "origen_lat", "origen_lng", "destino_lat", "destino_lng",
        ]
        widgets = {
            "origen_lat": forms.HiddenInput, "origen_lng": forms.HiddenInput,
            "destino_lat": forms.HiddenInput, "destino_lng": forms.HiddenInput,
            # DNI -> nombre (RENIEC); RUC -> razon social y, si estan vacios, direccion/distrito/departamento (SUNAT)
            "destinatario_documento": forms.TextInput(attrs={
                "data-doc": "1", "data-doc-nombre": "id_destinatario_nombre", "data-doc-direccion": "id_destino_direccion",
                "data-doc-distrito": "id_destino_distrito", "data-doc-departamento": "id_destino_departamento",
                "inputmode": "numeric", "autocomplete": "off"}),
        }

    # el punto exacto en el mapa es obligatorio en los envíos nuevos (los antiguos no lo tienen)
    PERU = (-18.40, 0.05, -81.40, -68.60)   # latitud min/max, longitud min/max (con margen)
    MENSAJE = {"origen": "Marque en el mapa el punto exacto de recojo.",
               "destino": "Marque en el mapa el punto exacto de entrega."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for lado in ("origen", "destino"):
            for eje in ("lat", "lng"):
                campo = self.fields[f"{lado}_{eje}"]
                campo.required = True
                campo.error_messages["required"] = self.MENSAJE[lado]

    def clean(self):
        d = super().clean()
        lat_min, lat_max, lng_min, lng_max = self.PERU
        puntos = {}
        for lado in ("origen", "destino"):
            lat, lng = d.get(f"{lado}_lat"), d.get(f"{lado}_lng")
            if lat is None or lng is None:
                continue
            if not (lat_min <= float(lat) <= lat_max and lng_min <= float(lng) <= lng_max):
                self.add_error(f"{lado}_lat", f"El punto de {'recojo' if lado == 'origen' else 'entrega'} debe estar en Perú.")
                continue
            puntos[lado] = (float(lat), float(lng))
        if len(puntos) == 2 and distancia_km(puntos["origen"], puntos["destino"]) < 0.02:
            self.add_error("destino_lat", "El punto de entrega es el mismo que el de recojo.")
        return d


def distancia_km(a, b):
    """Distancia en línea recta entre dos puntos (lat, lng), en km."""
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (*a, *b))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))
