from django.contrib import admin

from .models import (BitacoraEstado, ClienteLogistico, ConfigOperador, Consolidado, EstadoLogistico, GuiaCliente,
                     GuiaGo, MensajeEntrante, RemitenteAutorizado, SolicitudRecojo)


class AutorizadoInline(admin.TabularInline):
    model = RemitenteAutorizado
    extra = 0
    fields = ['tipo', 'valor']


@admin.register(ClienteLogistico)
class ClienteLogisticoAdmin(admin.ModelAdmin):
    list_display = ['cuenta', 'servicio_logistico', 'almacen_direccion']
    search_fields = ['cuenta__nombre', 'cuenta__documento']
    inlines = [AutorizadoInline]


@admin.register(SolicitudRecojo)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ['numero', 'cliente', 'canal', 'estado', 'operador', 'creado']
    list_filter = ['estado', 'canal']
    search_fields = ['numero', 'cliente__nombre', 'remitente']


@admin.register(GuiaCliente)
class GuiaClienteAdmin(admin.ModelAdmin):
    list_display = ['serie', 'numero', 'cliente', 'estado', 'departamento', 'provincia', 'creado']
    list_filter = ['estado']
    search_fields = ['serie', 'numero', 'cliente__nombre', 'destinatario_nombre']


@admin.register(Consolidado)
class ConsolidadoAdmin(admin.ModelAdmin):
    list_display = ['numero', 'departamento', 'provincia', 'estado', 'guia', 'creado']
    list_filter = ['estado']


@admin.register(GuiaGo)
class GuiaGoAdmin(admin.ModelAdmin):
    list_display = ['serie', 'numero', 'clase', 'destinatario_nombre', 'sunat_estado', 'fecha']
    list_filter = ['clase', 'sunat_estado']


@admin.register(MensajeEntrante)
class MensajeAdmin(admin.ModelAdmin):
    list_display = ['recibido', 'canal', 'remitente', 'resultado', 'solicitud']
    list_filter = ['canal', 'resultado']


admin.site.register(EstadoLogistico)
admin.site.register(ConfigOperador)
admin.site.register(BitacoraEstado)
