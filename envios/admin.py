from django.contrib import admin

from . import models as m


@admin.register(m.Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not m.Configuracion.objects.exists()


@admin.register(m.Cuenta)
class CuentaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "documento", "telefono", "linea_credito")
    list_filter = ("tipo",)
    search_fields = ("nombre", "documento")


@admin.register(m.Agente)
class AgenteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "zona", "rol", "telefono", "activo")
    list_filter = ("zona", "rol", "activo")


@admin.register(m.Transportista)
class TransportistaAdmin(admin.ModelAdmin):
    list_display = ("razon_social", "ruc", "tiene_api", "placa", "activo")


@admin.register(m.TarifaRuta)
class TarifaRutaAdmin(admin.ModelAdmin):
    list_display = ("origen", "destino", "base", "por_kg", "dias")
    list_filter = ("origen",)


class PaqueteInline(admin.TabularInline):
    model = m.Paquete
    extra = 0


class TramoInline(admin.TabularInline):
    model = m.Tramo
    extra = 0
    fields = ("orden", "tipo", "agente", "transportista", "guia", "estado", "registro", "inicio", "fin")


@admin.register(m.Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "cuenta", "origen_departamento", "destino_departamento", "estado", "moneda", "total", "creado")
    list_filter = ("estado", "moneda", "origen_departamento", "destino_departamento")
    search_fields = ("codigo", "cuenta__nombre", "destinatario_nombre")
    inlines = [PaqueteInline, TramoInline]
    readonly_fields = ("codigo",)


admin.site.register(m.Guia)
admin.site.register(m.Comprobante)
admin.site.register(m.Pago)
admin.site.register(m.EventoTracking)
admin.site.register(m.SyncERP)
admin.site.register(m.Serie)
