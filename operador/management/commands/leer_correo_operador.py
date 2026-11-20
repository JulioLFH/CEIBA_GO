"""Lee el buzón de pedidos de recojo y crea las solicitudes de los remitentes autorizados. Programarlo cada pocos
minutos. Los mensajes procesados se marcan leídos.

La conexión se toma de variables de entorno (nunca del código): OPERADOR_IMAP_SERVIDOR, OPERADOR_IMAP_PUERTO (993),
OPERADOR_IMAP_USUARIO y OPERADOR_IMAP_CLAVE."""
import email
import imaplib
import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Crea solicitudes de recojo con los correos nuevos del buzón de pedidos.'

    def handle(self, *args, **opciones):
        from operador import servicios
        from operador.canales import correo_a_solicitud
        if not servicios.activo():
            self.stdout.write('El operador logístico no está activo: no se lee el buzón.')
            return
        servidor, usuario = os.environ.get('OPERADOR_IMAP_SERVIDOR'), os.environ.get('OPERADOR_IMAP_USUARIO')
        clave = os.environ.get('OPERADOR_IMAP_CLAVE')
        if not (servidor and usuario and clave):
            raise CommandError('Falta configurar OPERADOR_IMAP_SERVIDOR, OPERADOR_IMAP_USUARIO y OPERADOR_IMAP_CLAVE.')
        imap = imaplib.IMAP4_SSL(servidor, int(os.environ.get('OPERADOR_IMAP_PUERTO') or 993))
        total = leidos = 0
        try:
            imap.login(usuario, clave)
            imap.select('INBOX')
            _, ids = imap.search(None, 'UNSEEN')
            for num in ids[0].split():
                _, partes = imap.fetch(num, '(RFC822)')
                leidos += 1
                if correo_a_solicitud(email.message_from_bytes(partes[0][1])) is not None:
                    total += 1
                imap.store(num, '+FLAGS', '\\Seen')
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
        self.stdout.write(f'{leidos} correo(s) leído(s); {total} solicitud(es) de recojo.')
