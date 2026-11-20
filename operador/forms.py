from django import forms
from django.contrib.auth.models import User

from .models import ClienteLogistico, Consolidado, ConfigOperador, EstadoLogistico, SolicitudRecojo

MOTIVOS_TRASLADO = [('01', '01 · Venta'), ('02', '02 · Compra'), ('04', '04 · Traslado entre establecimientos'),
                    ('08', '08 · Importación'), ('09', '09 · Exportación'), ('13', '13 · Otros'),
                    ('14', '14 · Venta sujeta a confirmación'), ('18', '18 · Traslado emisor itinerante'),
                    ('19', '19 · Traslado a zona primaria')]


def _operadores():
    return User.objects.filter(is_active=True).filter(models_q_operador()).order_by('username')


def models_q_operador():
    from django.db.models import Q
    return Q(is_staff=True) | Q(agente__isnull=False)


def _empresas():
    from envios.models import Cuenta
    return Cuenta.objects.filter(tipo='empresa', user__is_active=True).order_by('nombre')


def _transportistas():
    from envios.models import Transportista
    return Transportista.objects.filter(activo=True).order_by('razon_social')


class SolicitudForm(forms.ModelForm):
    class Meta:
        model = SolicitudRecojo
        fields = ['cliente', 'almacen', 'bultos', 'contacto', 'fecha_recojo', 'operador', 'mensaje']
        widgets = {'mensaje': forms.Textarea(attrs={'rows': 3}), 'fecha_recojo': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}
        labels = {'almacen': 'Almacén de origen (vacío = el de la ficha del cliente)', 'mensaje': 'Indicaciones'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cliente'].queryset = _empresas()
        self.fields['operador'].queryset = _operadores()
        self.fields['operador'].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields['almacen'].required = False


class ClienteLogisticoForm(forms.ModelForm):
    correos = forms.CharField(label='Correos autorizados para pedir recojos', required=False,
                              widget=forms.Textarea(attrs={'rows': 3}),
                              help_text='Uno por línea. Un correo de estas direcciones crea una solicitud de recojo.')
    celulares = forms.CharField(label='Celulares autorizados (WhatsApp)', required=False,
                                widget=forms.Textarea(attrs={'rows': 3}),
                                help_text='Uno por línea. Se comparan por sus últimos 9 dígitos (con o sin +51).')

    class Meta:
        model = ClienteLogistico
        fields = ['servicio_logistico', 'almacen_direccion', 'almacen_ubigeo', 'almacen_referencia', 'contacto']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            autorizados = list(self.instance.autorizados.all())
            self.initial['correos'] = '\n'.join(a.valor for a in autorizados if a.tipo == 'CORREO')
            self.initial['celulares'] = '\n'.join(a.valor for a in autorizados if a.tipo == 'CELULAR')

    @staticmethod
    def _lineas(texto):
        return [x.strip() for x in (texto or '').replace(',', '\n').replace(';', '\n').splitlines() if x.strip()]

    def clean_correos(self):
        correos = self._lineas(self.cleaned_data.get('correos'))
        validar = forms.EmailField().clean
        for c in correos:
            try:
                validar(c)
            except forms.ValidationError:
                raise forms.ValidationError(f'«{c}» no es un correo válido.')
        return correos

    def clean_celulares(self):
        from .models import clave_celular
        celulares = self._lineas(self.cleaned_data.get('celulares'))
        for c in celulares:
            if len(clave_celular(c)) < 9:
                raise forms.ValidationError(f'«{c}» no tiene 9 dígitos.')
        return celulares

    def clean(self):
        datos = super().clean()
        from .models import RemitenteAutorizado
        for tipo, campo in (('CORREO', 'correos'), ('CELULAR', 'celulares')):
            for valor in datos.get(campo) or []:
                otro = RemitenteAutorizado.objects.filter(tipo=tipo, clave=RemitenteAutorizado.normalizar(tipo, valor))
                if self.instance.pk:
                    otro = otro.exclude(cliente=self.instance)
                otro = otro.select_related('cliente__cuenta').first()
                if otro is not None:
                    self.add_error(campo, f'«{valor}» ya está autorizado para {otro.cliente.cuenta.nombre}.')
        return datos

    def save(self, commit=True):
        from .models import RemitenteAutorizado
        obj = super().save(commit=commit)
        nuevos = {('CORREO', RemitenteAutorizado.normalizar('CORREO', v)): v for v in self.cleaned_data['correos']}
        nuevos.update({('CELULAR', RemitenteAutorizado.normalizar('CELULAR', v)): v
                       for v in self.cleaned_data['celulares']})
        for a in obj.autorizados.all():
            if (a.tipo, a.clave) not in nuevos:
                a.delete()
            else:
                nuevos.pop((a.tipo, a.clave))
        for (tipo, _), valor in nuevos.items():
            RemitenteAutorizado.objects.create(cliente=obj, tipo=tipo, valor=valor)
        return obj


class EstadoForm(forms.ModelForm):
    class Meta:
        model = EstadoLogistico
        fields = ['nombre', 'color', 'sigla', 'inicial', 'orden', 'disparador', 'activo']
        widgets = {'color': forms.TextInput(attrs={'type': 'color'})}


class ConfigForm(forms.ModelForm):
    motivo_traslado = forms.ChoiceField(label='Motivo de traslado (catálogo 20)', choices=MOTIVOS_TRASLADO,
                                        help_text='Por confirmar con el contador: Go traslada mercadería de terceros')

    class Meta:
        model = ConfigOperador
        fields = ['activo', 'partida_direccion', 'partida_ubigeo', 'serie_consolidada', 'serie_ultimo_tramo',
                  'motivo_traslado', 'descripcion_motivo', 'ultimo_tramo_por']


class EmitirForm(forms.Form):
    agencia_nombre = forms.CharField(label='Agencia de destino', max_length=200, required=False,
                                     help_text='Vacío = Go recibe en destino')
    agencia_ruc = forms.CharField(label='RUC de la agencia', max_length=11, required=False)
    llegada_ubigeo = forms.CharField(label='Ubigeo de llegada', max_length=6)
    llegada_direccion = forms.CharField(label='Dirección de llegada', max_length=250)
    transportista = forms.ModelChoiceField(label='Transportista', queryset=None, required=False)
    fecha = forms.DateField(label='Inicio del traslado', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['transportista'].queryset = _transportistas()


class TransportistaForm(forms.Form):
    transportista = forms.ModelChoiceField(label='Transportista', queryset=None, required=False)
    tipo = forms.ChoiceField(label='Documento', choices=Consolidado.DOCS_TRANSPORTISTA)
    numero = forms.CharField(label='Serie-número', max_length=30, required=False)
    fecha = forms.DateField(label='Fecha', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), required=False)
    peso = forms.DecimalField(label='Peso (kg)', max_digits=12, decimal_places=2, required=False, min_value=0)
    bultos = forms.IntegerField(label='Bultos', required=False, min_value=0)
    archivo = forms.FileField(label='Foto o PDF del documento', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['transportista'].queryset = _transportistas()


class CostoForm(forms.Form):
    descripcion = forms.CharField(label='Concepto', max_length=150, initial='Flete')
    monto = forms.DecimalField(label='Monto (S/)', max_digits=12, decimal_places=2, min_value=0)
