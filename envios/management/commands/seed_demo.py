"""Carga usuarios y datos de demostracion: operador, clientes, agentes por zona y transportistas.

Uso:  python manage.py seed_demo
Contrasena de todos los usuarios de demo: ceiba2026  (cambiela en produccion)
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from envios.models import Agente, Configuracion, Cuenta, TarifaRuta, Transportista

CLAVE = "ceiba2026"


def usuario(nombre, nombre_completo, staff=False):
    u, creado = User.objects.get_or_create(username=nombre, defaults={"first_name": nombre_completo})
    if creado:
        u.set_password(CLAVE)
        u.is_staff = u.is_superuser = staff
        u.save()
    return u


class Command(BaseCommand):
    help = "Datos de demostracion de Ceiba GO"

    def handle(self, *args, **opts):
        Configuracion.get()
        usuario("operador", "Operaciones Ceiba GO", staff=True)

        Cuenta.objects.get_or_create(user=usuario("cliente", "María Quispe"), defaults=dict(
            tipo="persona", documento="45879632", nombre="María Quispe Huamán", telefono="987654321",
            direccion="Jr. Las Flores 245, San Juan de Lurigancho"))
        Cuenta.objects.get_or_create(user=usuario("empresa", "Constructora Andina"), defaults=dict(
            tipo="empresa", documento="20512345678", nombre="CONSTRUCTORA ANDINA S.A.C.", telefono="014445566",
            direccion="Av. Javier Prado Este 1450, San Isidro", linea_credito=Decimal("5000")))

        agentes = [
            ("agente_lima", "Carlos Ramos", "LIMA", "ambos"),
            ("agente_lima2", "Lucía Torres", "LIMA", "destino"),
            ("agente_arequipa", "José Mamani", "AREQUIPA", "ambos"),
            ("agente_cusco", "Rosa Condori", "CUSCO", "ambos"),
            ("agente_trujillo", "Pedro Vásquez", "LA LIBERTAD", "ambos"),
            ("agente_piura", "Ana Chávez", "PIURA", "ambos"),
        ]
        for u, n, zona, rol in agentes:
            Agente.objects.get_or_create(user=usuario(u, n), defaults=dict(nombre=n, zona=zona, rol=rol, telefono="900000000"))

        Transportista.objects.get_or_create(ruc="20498765432", defaults=dict(
            razon_social="TRANSPORTES ANDINOS DEL SUR S.A.C.", tiene_api=True, api_url="https://api.ejemplo-transporte.pe",
            placa="ABC-123", conductor="Luis Huamán", licencia="Q12345678", user=usuario("transporte", "Transportes Andinos")))
        Transportista.objects.get_or_create(ruc="20601234567", defaults=dict(
            razon_social="EXPRESO NORTE CARGA E.I.R.L.", tiene_api=False, placa="T4X-908", conductor="Miguel Saavedra",
            licencia="Q87654321", user=usuario("transporte2", "Expreso Norte")))

        for o, d, base, kg, dias in [
            ("LIMA", "AREQUIPA", "20.00", "2.50", 2), ("AREQUIPA", "LIMA", "20.00", "2.50", 2),
            ("LIMA", "CUSCO", "24.00", "2.90", 3), ("LIMA", "LA LIBERTAD", "18.00", "2.20", 2),
            ("LIMA", "PIURA", "22.00", "2.60", 2), ("LIMA", "LIMA", "8.00", "1.20", 1),
        ]:
            TarifaRuta.objects.get_or_create(origen=o, destino=d, defaults=dict(base=Decimal(base), por_kg=Decimal(kg), dias=dias))

        self.stdout.write(self.style.SUCCESS(f"Datos de demo listos. Contraseña de los usuarios de demo: {CLAVE}"))
