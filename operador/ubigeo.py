"""Ubigeos INEI (25 departamentos, 196 provincias, 1874 distritos), copiados de Ceiba ERP (core/data/ubigeos.csv)."""
import csv
from functools import lru_cache
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent / 'data' / 'ubigeos.csv'


@lru_cache(maxsize=1)
def tabla():
    """{ubigeo: (departamento, provincia, distrito)}"""
    with open(ARCHIVO, encoding='utf-8') as f:
        return {fila['ubigeo']: (fila['departamento'], fila['provincia'], fila['distrito'])
                for fila in csv.DictReader(f)}
