"""Servidor de produccion de Ceiba GO (waitress) en el puerto 8040."""
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ceibago.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from waitress import serve  # noqa: E402

from ceibago.wsgi import application  # noqa: E402

if __name__ == "__main__":
    puerto = int(os.environ.get("PUERTO", "8040"))
    print(f"Ceiba GO en http://127.0.0.1:{puerto}  (no cierre esta ventana)")
    # solo dentro del servidor: al público llega por https://go.ceiba-tecnologic.com (IIS), no por IP:puerto.
    # HOST=0.0.0.0 lo vuelve a abrir a la red si alguna vez hiciera falta.
    host = os.environ.get("HOST", "127.0.0.1")
    serve(application, host=host, port=puerto, threads=8)
