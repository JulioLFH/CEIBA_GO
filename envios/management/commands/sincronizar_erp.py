from django.core.management.base import BaseCommand

from envios import erp


class Command(BaseCommand):
    help = "Envia a Ceiba ERP la cola pendiente (modo standalone: al reconectar)"

    def handle(self, *args, **opts):
        ok, err, sin = erp.sincronizar()
        if sin:
            self.stdout.write(f"Sin URL de Ceiba ERP: {sin} registros quedan guardados.")
        else:
            self.stdout.write(f"Enviados: {ok} · con error: {err}")
