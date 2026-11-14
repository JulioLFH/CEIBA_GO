"""Modelo de datos de Ceiba GO (seccion 03 del documento de diseno)."""
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

DEPARTAMENTOS = [
    (d, d.title()) for d in [
        "AMAZONAS", "ANCASH", "APURIMAC", "AREQUIPA", "AYACUCHO", "CAJAMARCA", "CALLAO",
        "CUSCO", "HUANCAVELICA", "HUANUCO", "ICA", "JUNIN", "LA LIBERTAD", "LAMBAYEQUE",
        "LIMA", "LORETO", "MADRE DE DIOS", "MOQUEGUA", "PASCO", "PIURA", "PUNO",
        "SAN MARTIN", "TACNA", "TUMBES", "UCAYALI",
    ]
]
MONEDAS = [("PEN", "Soles (S/)"), ("USD", "Dólares (US$)")]
SIMBOLO = {"PEN": "S/", "USD": "US$"}


class Configuracion(models.Model):
    """Parametros del motor de tarifas, SUNAT y conexion con Ceiba ERP (registro unico)."""
    empresa_ruc = models.CharField("RUC de Ceiba GO", max_length=11, default="20600000001")
    empresa_razon = models.CharField("Razón social", max_length=150, default="CEIBA GO S.A.C.")
    empresa_direccion = models.CharField(max_length=200, default="Av. Principal 123, Lima")
    igv = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.18"))
    tipo_cambio_usd = models.DecimalField("Tipo de cambio S/ por US$", max_digits=8, decimal_places=4, default=Decimal("3.75"))
    factor_volumetrico = models.DecimalField("Factor volumétrico (kg por m³)", max_digits=8, decimal_places=2, default=Decimal("250"))
    seguro_pct = models.DecimalField("Seguro (% del valor declarado)", max_digits=5, decimal_places=4, default=Decimal("0.01"))
    recargo_fragil = models.DecimalField("Recargo por bulto frágil (S/)", max_digits=8, decimal_places=2, default=Decimal("5.00"))
    modo_erp = models.CharField(
        max_length=12, default="standalone",
        choices=[("standalone", "Independiente (guarda y sincroniza al reconectar)"), ("integrado", "Integrado (siempre conectado)")],
    )
    erp_url = models.URLField("URL de Ceiba ERP", blank=True, help_text="Ej.: http://127.0.0.1:8000 . Vacío = trabaja independiente.")
    erp_token = models.CharField("Token API de Ceiba ERP", max_length=200, blank=True)
    token_decolecta = models.CharField(
        "Token Decolecta (DNI/RUC)", max_length=200, blank=True,
        help_text="El mismo token de Decolecta de Ceiba ERP: autocompleta nombres con el DNI (RENIEC) y razón social y dirección con el RUC (SUNAT).",
    )
    proveedor_sunat = models.CharField(
        max_length=12, default="simulado",
        choices=[("simulado", "Simulado (pruebas)"), ("ceiba", "Ceiba ERP emite"), ("pse", "Proveedor PSE/OSE")],
    )

    class Meta:
        verbose_name = verbose_name_plural = "Configuración"

    def __str__(self):
        return "Configuración de Ceiba GO"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Serie(models.Model):
    """Correlativos de comprobantes y guias (F001, B001, T001, V001)."""
    codigo = models.CharField(max_length=4, unique=True)
    ultimo = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.codigo} · {self.ultimo}"

    @classmethod
    def siguiente(cls, codigo):
        with transaction.atomic():
            s, _ = cls.objects.select_for_update().get_or_create(codigo=codigo)
            s.ultimo += 1
            s.save(update_fields=["ultimo"])
            return s.ultimo


class Cuenta(models.Model):
    TIPOS = [("persona", "Persona"), ("empresa", "Empresa")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cuenta")
    tipo = models.CharField(max_length=8, choices=TIPOS, default="persona")
    documento = models.CharField("DNI / RUC", max_length=11)
    nombre = models.CharField("Nombre o razón social", max_length=150)
    telefono = models.CharField(max_length=20, blank=True)
    direccion = models.CharField("Dirección fiscal", max_length=200, blank=True)
    linea_credito = models.DecimalField("Línea de crédito (S/)", max_digits=12, decimal_places=2, default=Decimal("0"))

    def __str__(self):
        return f"{self.nombre} ({self.documento})"

    @property
    def es_empresa(self):
        return self.tipo == "empresa"

    @property
    def credito_usado(self):
        total = Decimal("0")
        for p in Pago.objects.filter(pedido__cuenta=self, metodo="credito", estado="por_cobrar").select_related("pedido"):
            total += p.monto_pen
        return total

    @property
    def credito_disponible(self):
        return self.linea_credito - self.credito_usado


class Agente(models.Model):
    ROLES = [("recojo", "Recojo (origen)"), ("destino", "Recepción y entrega (destino)"), ("ambos", "Recojo y entrega")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agente")
    nombre = models.CharField(max_length=120)
    telefono = models.CharField(max_length=20, blank=True)
    zona = models.CharField("Departamento / zona", max_length=20, choices=DEPARTAMENTOS)
    rol = models.CharField(max_length=8, choices=ROLES, default="ambos")
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} · {self.get_zona_display()}"


class Transportista(models.Model):
    razon_social = models.CharField(max_length=150)
    ruc = models.CharField(max_length=11)
    tiene_api = models.BooleanField(default=False)
    api_url = models.URLField(blank=True)
    placa = models.CharField("Placa habitual", max_length=10, blank=True)
    conductor = models.CharField(max_length=120, blank=True)
    licencia = models.CharField(max_length=20, blank=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="transportista")
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.razon_social


class TarifaRuta(models.Model):
    """Tarifa por ruta (base + S/ por kg facturable). Si no hay ruta, se usa la regla por regiones."""
    origen = models.CharField(max_length=20, choices=DEPARTAMENTOS)
    destino = models.CharField(max_length=20, choices=DEPARTAMENTOS)
    base = models.DecimalField("Base (S/)", max_digits=8, decimal_places=2)
    por_kg = models.DecimalField("S/ por kg", max_digits=8, decimal_places=2)
    dias = models.PositiveSmallIntegerField("Días estimados", default=2)

    class Meta:
        unique_together = [("origen", "destino")]
        verbose_name = "tarifa por ruta"
        verbose_name_plural = "tarifas por ruta"

    def __str__(self):
        return f"{self.origen} → {self.destino}"


class Pedido(models.Model):
    ESTADOS = [
        ("creado", "Creado"),
        ("pagado", "Pagado / Facturado"),
        ("recojo_asignado", "Recojo asignado"),
        ("recogido", "Recogido"),
        ("en_transito", "En tránsito"),
        ("en_destino", "En destino (almacén)"),
        ("en_reparto", "En reparto"),
        ("entregado", "Entregado"),
        ("fallido", "Fallido"),
        ("devuelto", "Devuelto"),
        ("reprogramado", "Reprogramado"),
        ("anulado", "Anulado"),
    ]
    FLUJO = ["creado", "pagado", "recojo_asignado", "recogido", "en_transito", "en_destino", "en_reparto", "entregado"]
    EXCEPCIONES = ["fallido", "devuelto", "reprogramado", "anulado"]

    codigo = models.CharField("Código de seguimiento", max_length=12, unique=True, editable=False)
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="pedidos")
    # origen
    origen_direccion = models.CharField("Dirección de recojo", max_length=200)
    origen_distrito = models.CharField("Distrito", max_length=80)
    origen_departamento = models.CharField("Departamento", max_length=20, choices=DEPARTAMENTOS)
    origen_referencia = models.CharField("Referencia", max_length=150, blank=True)
    origen_contacto = models.CharField("Contacto en origen", max_length=120)
    origen_telefono = models.CharField("Teléfono", max_length=20)
    # punto exacto en el mapa (WGS84): el agente llega a la puerta aunque la dirección escrita sea imprecisa
    origen_lat = models.DecimalField("Latitud de recojo", max_digits=9, decimal_places=6, null=True, blank=True)
    origen_lng = models.DecimalField("Longitud de recojo", max_digits=9, decimal_places=6, null=True, blank=True)
    # destino
    destino_direccion = models.CharField("Dirección de entrega", max_length=200)
    destino_distrito = models.CharField("Distrito", max_length=80)
    destino_departamento = models.CharField("Departamento", max_length=20, choices=DEPARTAMENTOS)
    destino_referencia = models.CharField("Referencia", max_length=150, blank=True)
    destinatario_nombre = models.CharField("Destinatario", max_length=120)
    destinatario_documento = models.CharField("DNI / RUC del destinatario", max_length=11)
    destinatario_telefono = models.CharField("Teléfono", max_length=20)
    destino_lat = models.DecimalField("Latitud de entrega", max_digits=9, decimal_places=6, null=True, blank=True)
    destino_lng = models.DecimalField("Longitud de entrega", max_digits=9, decimal_places=6, null=True, blank=True)
    # importes (cotizacion congelada)
    moneda = models.CharField(max_length=3, choices=MONEDAS, default="PEN")
    tipo_cambio = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1"))
    peso_real_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    volumen_m3 = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    peso_facturable_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    flete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    seguro = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    recargos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField("Valor de venta", max_digits=12, decimal_places=2, default=0)
    igv = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    dias_estimados = models.PositiveSmallIntegerField(default=2)
    centro_costo = models.CharField(max_length=40, blank=True)
    estado = models.CharField(max_length=16, choices=ESTADOS, default="creado")
    motivo_excepcion = models.CharField(max_length=200, blank=True)
    fecha_reprogramada = models.DateField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return self.codigo

    def save(self, *args, **kwargs):
        if not self.codigo:
            alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            while True:
                c = "CG-" + "".join(secrets.choice(alfabeto) for _ in range(7))
                if not Pedido.objects.filter(codigo=c).exists():
                    self.codigo = c
                    break
        super().save(*args, **kwargs)

    @property
    def simbolo(self):
        return SIMBOLO.get(self.moneda, "S/")

    @property
    def total_pen(self):
        return (self.total * self.tipo_cambio).quantize(Decimal("0.01"))

    @property
    def es_excepcion(self):
        return self.estado in self.EXCEPCIONES

    @property
    def paso_flujo(self):
        """Indice del estado en el flujo normal (para la linea de tiempo)."""
        if self.estado in self.FLUJO:
            return self.FLUJO.index(self.estado)
        ultimo = self.eventos.filter(estado__in=self.FLUJO).order_by("-timestamp").first()
        return self.FLUJO.index(ultimo.estado) if ultimo else 0

    def guia(self, tipo):
        return self.guias.filter(tipo=tipo).exclude(sunat_estado="baja").first()

    def tramo(self, tipo):
        return self.tramos.filter(tipo=tipo).first()

    @property
    def comprobante_vigente(self):
        return getattr(self, "comprobante", None)

    @property
    def total_bultos(self):
        return sum(p.cantidad for p in self.paquetes.all())


class Paquete(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="paquetes")
    descripcion = models.CharField(max_length=150)
    cantidad = models.PositiveIntegerField(default=1)
    peso_kg = models.DecimalField("Peso unitario (kg)", max_digits=10, decimal_places=2)
    largo_cm = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    ancho_cm = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    alto_cm = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    volumen_m3 = models.DecimalField("Volumen total (m³)", max_digits=10, decimal_places=4, default=0)
    fragil = models.BooleanField(default=False)
    valor_declarado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    peso_real_confirmado = models.DecimalField("Peso total confirmado en recojo", max_digits=10, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.cantidad} × {self.descripcion}"

    @property
    def peso_total(self):
        return self.peso_kg * self.cantidad


class Guia(models.Model):
    TIPOS = [("GRE-R", "GRE Remitente"), ("GRE-T", "GRE Transportista")]
    ESTADOS = [("aceptada", "Aceptada por SUNAT"), ("pendiente", "Pendiente"), ("baja", "Dada de baja")]
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="guias")
    tipo = models.CharField(max_length=5, choices=TIPOS)
    serie = models.CharField(max_length=4)
    numero = models.PositiveIntegerField()
    qr_token = models.CharField(max_length=32, unique=True, editable=False)
    transportista = models.ForeignKey(Transportista, null=True, blank=True, on_delete=models.PROTECT)
    placa = models.CharField(max_length=10, blank=True)
    conductor = models.CharField(max_length=120, blank=True)
    punto_partida = models.CharField(max_length=250)
    punto_llegada = models.CharField(max_length=250)
    peso_bruto_kg = models.DecimalField(max_digits=10, decimal_places=2)
    bultos = models.PositiveIntegerField(default=1)
    motivo_traslado = models.CharField(max_length=60, default="Venta / servicio de envío")
    sunat_estado = models.CharField(max_length=10, choices=ESTADOS, default="pendiente")
    sunat_ticket = models.CharField(max_length=40, blank=True)
    emitida = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["emitida"]

    def __str__(self):
        return f"{self.tipo} {self.numero_completo}"

    def save(self, *args, **kwargs):
        if not self.qr_token:
            self.qr_token = secrets.token_hex(8).upper()
        super().save(*args, **kwargs)

    @property
    def numero_completo(self):
        return f"{self.serie}-{self.numero:08d}"

    @property
    def qr_texto(self):
        # Lo que va dentro del QR: tipo, numero y un token que el agente escanea sin digitar
        return f"CEIBAGO|{self.tipo}|{self.numero_completo}|{self.pedido.codigo}|{self.qr_token}"


class Tramo(models.Model):
    TIPOS = [("recojo", "Recojo en origen"), ("linea", "Traslado interprovincial"), ("entrega", "Entrega en destino")]
    ESTADOS = [("pendiente", "Pendiente"), ("asignado", "Asignado"), ("en_curso", "En curso"),
               ("completado", "Completado"), ("fallido", "Fallido")]
    REGISTRO = [("qr", "Escaneo QR"), ("manual", "Registro manual"), ("api", "API del transportista")]
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="tramos")
    tipo = models.CharField(max_length=8, choices=TIPOS)
    orden = models.PositiveSmallIntegerField()
    agente = models.ForeignKey(Agente, null=True, blank=True, on_delete=models.PROTECT, related_name="tramos")
    transportista = models.ForeignKey(Transportista, null=True, blank=True, on_delete=models.PROTECT, related_name="tramos")
    guia = models.ForeignKey(Guia, null=True, blank=True, on_delete=models.PROTECT, related_name="tramos")
    estado = models.CharField(max_length=10, choices=ESTADOS, default="pendiente")
    registro = models.CharField(max_length=6, choices=REGISTRO, blank=True)
    inicio = models.DateTimeField(null=True, blank=True)
    fin = models.DateTimeField(null=True, blank=True)
    evidencia_foto = models.FileField(upload_to="evidencias/%Y/%m/", blank=True)
    firma = models.TextField(blank=True, help_text="Firma del receptor (imagen en base64)")
    receptor_nombre = models.CharField(max_length=120, blank=True)
    receptor_documento = models.CharField(max_length=11, blank=True)
    almacenado = models.BooleanField(default=False)
    ubicacion_almacen = models.CharField(max_length=60, blank=True)
    observaciones = models.CharField(max_length=250, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["pedido", "orden"]

    def __str__(self):
        return f"{self.pedido.codigo} · {self.get_tipo_display()}"

    @property
    def ejecutor(self):
        return self.agente or self.transportista


class Comprobante(models.Model):
    TIPOS = [("boleta", "Boleta de venta electrónica"), ("factura", "Factura electrónica")]
    pedido = models.OneToOneField(Pedido, on_delete=models.CASCADE, related_name="comprobante")
    tipo = models.CharField(max_length=7, choices=TIPOS)
    serie = models.CharField(max_length=4)
    numero = models.PositiveIntegerField()
    cliente_documento = models.CharField(max_length=11)
    cliente_nombre = models.CharField(max_length=150)
    moneda = models.CharField(max_length=3, choices=MONEDAS)
    tipo_cambio = models.DecimalField(max_digits=8, decimal_places=4)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    igv = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    forma_pago = models.CharField(max_length=8, default="contado")
    sunat_estado = models.CharField(max_length=10, default="aceptado",
                                    choices=[("aceptado", "Aceptado"), ("baja", "Comunicada la baja")])
    hash_cpe = models.CharField(max_length=40, blank=True)
    emitido = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero_completo}"

    @property
    def numero_completo(self):
        return f"{self.serie}-{self.numero:08d}"

    @property
    def qr_texto(self):
        # formato SUNAT: RUC|tipo|serie|numero|IGV|total|fecha|tipo doc cliente|doc cliente|hash
        ruc = Configuracion.get().empresa_ruc
        cod = "01" if self.tipo == "factura" else "03"
        tdoc = "6" if len(self.cliente_documento) == 11 else "1"
        return f"{ruc}|{cod}|{self.serie}|{self.numero}|{self.igv}|{self.total}|{self.emitido:%Y-%m-%d}|{tdoc}|{self.cliente_documento}|{self.hash_cpe}"


class Pago(models.Model):
    METODOS = [("yape", "Yape"), ("plin", "Plin"), ("billetera", "Billetera digital"),
               ("tarjeta", "Tarjeta"), ("credito", "Factura a crédito")]
    ESTADOS = [("aprobado", "Aprobado"), ("por_cobrar", "Por cobrar (crédito)"), ("cobrado", "Cobrado"),
               ("rechazado", "Rechazado"), ("extornado", "Extornado / liberado")]
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="pagos")
    metodo = models.CharField(max_length=9, choices=METODOS)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    moneda = models.CharField(max_length=3, choices=MONEDAS)
    tipo_cambio = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1"))
    estado = models.CharField(max_length=10, choices=ESTADOS)
    referencia = models.CharField("N° de operación", max_length=60, blank=True)
    vence = models.DateField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_metodo_display()} {self.moneda} {self.monto}"

    @property
    def monto_pen(self):
        return (self.monto * self.tipo_cambio).quantize(Decimal("0.01"))


class EventoTracking(models.Model):
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="eventos")
    estado = models.CharField(max_length=16, choices=Pedido.ESTADOS)
    descripcion = models.CharField(max_length=250)
    ubicacion = models.CharField(max_length=120, blank=True)
    actor = models.CharField(max_length=120, blank=True)
    publico = models.BooleanField(default=True, help_text="Visible en el portal de seguimiento")
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]

    def __str__(self):
        return f"{self.pedido.codigo} · {self.estado}"


class SyncERP(models.Model):
    """Cola de envio a Ceiba ERP (venta, GRE, CxC, almacen, costo, cobranza, baja, cierre)."""
    TIPOS = [("venta", "Venta (comprobante)"), ("gre", "Guía de remisión"), ("cxc", "Cuenta por cobrar"),
             ("almacen", "Movimiento de almacén"), ("costo", "Centro de costo del flete"),
             ("cobranza", "Cobranza"), ("baja", "Anulación / baja"), ("cierre", "Conciliación de cierre")]
    ESTADOS = [("pendiente", "Pendiente"), ("enviado", "Enviado"), ("error", "Error")]
    pedido = models.ForeignKey(Pedido, null=True, on_delete=models.CASCADE, related_name="sync_erp")
    tipo = models.CharField(max_length=9, choices=TIPOS)
    payload = models.JSONField()
    estado = models.CharField(max_length=9, choices=ESTADOS, default="pendiente")
    intentos = models.PositiveSmallIntegerField(default=0)
    respuesta = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    enviado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado"]
        verbose_name = "sincronización con ERP"
        verbose_name_plural = "sincronizaciones con ERP"

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.estado}"
