from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

admin.site.site_header = "Ceiba GO · Administración"
admin.site.site_title = "Ceiba GO"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("envios.urls")),
    path("operador/", include("operador.urls")),
    # evidencias (fotos de recojo / POD): las sirve la propia app
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
