from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("ingresar/", auth_views.LoginView.as_view(), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("registro/", views.registro, name="registro"),
    path("inicio/", views.inicio, name="inicio"),
    # portal de seguimiento (sin cuenta)
    path("seguimiento/", views.seguimiento, name="seguimiento"),
    path("s/<str:codigo>/", views.seguimiento, name="seguimiento_codigo"),
    path("api/cotizar/", views.api_cotizar, name="api_cotizar"),
    path("api/documento/", views.api_documento, name="api_documento"),
    # documentos
    path("doc/<str:codigo>/comprobante/", views.comprobante, name="comprobante"),
    path("doc/<str:codigo>/guia/<str:tipo>/", views.guia, name="guia"),
    # cliente
    path("app/", views.cliente_panel, name="cliente_panel"),
    path("app/nuevo/", views.cliente_nuevo, name="cliente_nuevo"),
    path("app/pedido/<str:codigo>/pagar/", views.cliente_pagar, name="cliente_pagar"),
    path("app/pedido/<str:codigo>/", views.cliente_pedido, name="cliente_pedido"),
    # agentes
    path("agente/", views.agente_panel, name="agente_panel"),
    path("agente/pedido/<str:codigo>/", views.agente_pedido, name="agente_pedido"),
    path("escanear/", views.escanear, name="escanear"),
    # transporte
    path("transporte/", views.transp_panel, name="transp_panel"),
    # operaciones
    path("ops/", views.ops_panel, name="ops_panel"),
    path("ops/pedidos/", views.ops_pedidos, name="ops_pedidos"),
    path("ops/pedido/<str:codigo>/", views.ops_pedido, name="ops_pedido"),
    path("ops/erp/", views.ops_erp, name="ops_erp"),
]
