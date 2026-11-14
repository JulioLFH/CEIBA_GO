"""Motor de tarifas (paso 3): base por ruta + S/ por kg facturable.

Decision 1 del documento: se cobra por el MAYOR entre el peso real y el peso volumetrico
(volumen m3 x factor volumetrico). Si la ruta no tiene tarifa propia (TarifaRuta) se usa
la regla por regiones de abajo. Seguro sobre el valor declarado y recargo por bulto fragil.
"""
from decimal import ROUND_HALF_UP, Decimal

from .models import Configuracion, TarifaRuta

D = Decimal
CENT = D("0.01")

REGION = {
    "LIMA": "centro", "CALLAO": "centro", "ICA": "centro", "JUNIN": "centro", "PASCO": "centro",
    "HUANCAVELICA": "centro", "HUANUCO": "centro", "ANCASH": "centro",
    "LA LIBERTAD": "norte", "LAMBAYEQUE": "norte", "PIURA": "norte", "TUMBES": "norte", "CAJAMARCA": "norte",
    "AREQUIPA": "sur", "MOQUEGUA": "sur", "TACNA": "sur", "PUNO": "sur", "CUSCO": "sur",
    "APURIMAC": "sur", "AYACUCHO": "sur",
    "LORETO": "selva", "UCAYALI": "selva", "SAN MARTIN": "selva", "AMAZONAS": "selva", "MADRE DE DIOS": "selva",
}

# (base S/, S/ por kg, dias)
REGLA = {
    "mismo_departamento": (D("8.00"), D("1.20"), 1),
    "misma_region": (D("15.00"), D("2.00"), 2),
    "otra_region": (D("22.00"), D("2.80"), 3),
    "selva": (D("30.00"), D("3.50"), 4),
}


def _q(x):
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(v, defecto="0"):
    try:
        return D(str(v).strip() or defecto)
    except Exception:
        return D(defecto)


def regla_ruta(origen, destino):
    t = TarifaRuta.objects.filter(origen=origen, destino=destino).first()
    if t:
        return t.base, t.por_kg, t.dias, f"Tarifa de ruta {origen.title()} → {destino.title()}"
    if origen == destino:
        clave = "mismo_departamento"
    elif "selva" in (REGION.get(origen), REGION.get(destino)):
        clave = "selva"
    elif REGION.get(origen) == REGION.get(destino):
        clave = "misma_region"
    else:
        clave = "otra_region"
    base, kg, dias = REGLA[clave]
    return base, kg, dias, clave.replace("_", " ").capitalize()


def normalizar_paquetes(filas):
    """Convierte filas del formulario/JSON en dicts numericos y calcula el volumen."""
    salida = []
    for f in filas:
        cant = int(_dec(f.get("cantidad"), "1") or 1)
        peso = _dec(f.get("peso_kg"))
        largo, ancho, alto = _dec(f.get("largo_cm")), _dec(f.get("ancho_cm")), _dec(f.get("alto_cm"))
        if cant <= 0 or peso <= 0:
            continue
        vol = (largo * ancho * alto / D("1000000") * cant).quantize(D("0.0001"))
        salida.append({
            "descripcion": (f.get("descripcion") or "Bulto").strip()[:150],
            "cantidad": cant, "peso_kg": peso, "largo_cm": largo, "ancho_cm": ancho, "alto_cm": alto,
            "volumen_m3": vol,
            "fragil": str(f.get("fragil")).lower() in ("1", "true", "on", "si"),
            "valor_declarado": max(_dec(f.get("valor_declarado")), D("0")),
        })
    return salida


def cotizar(origen, destino, paquetes, moneda="PEN"):
    """Devuelve el detalle de la cotizacion. Importes en la moneda pedida."""
    cfg = Configuracion.get()
    paquetes = normalizar_paquetes(paquetes)
    base, por_kg, dias, regla = regla_ruta(origen, destino)

    peso_real = sum((p["peso_kg"] * p["cantidad"] for p in paquetes), D("0"))
    volumen = sum((p["volumen_m3"] for p in paquetes), D("0"))
    peso_vol = (volumen * cfg.factor_volumetrico).quantize(CENT)
    facturable = max(peso_real, peso_vol).quantize(CENT)

    flete = base + por_kg * facturable
    seguro = sum((p["valor_declarado"] for p in paquetes), D("0")) * cfg.seguro_pct
    fragiles = sum(p["cantidad"] for p in paquetes if p["fragil"])
    recargos = cfg.recargo_fragil * fragiles

    tc = cfg.tipo_cambio_usd if moneda == "USD" else D("1")
    flete, seguro, recargos = _q(flete / tc), _q(seguro / tc), _q(recargos / tc)
    subtotal = flete + seguro + recargos
    igv = _q(subtotal * cfg.igv)
    return {
        "ok": bool(paquetes) and bool(origen) and bool(destino),
        "moneda": moneda, "tipo_cambio": str(tc), "regla": regla, "dias": dias,
        "base": str(_q(base / tc)), "por_kg": str(_q(por_kg / tc)),
        "peso_real_kg": str(peso_real.quantize(CENT)), "volumen_m3": str(volumen),
        "peso_volumetrico_kg": str(peso_vol), "peso_facturable_kg": str(facturable),
        "cobra_por": "volumen" if peso_vol > peso_real else "peso",
        "flete": str(flete), "seguro": str(seguro), "recargos": str(recargos), "bultos_fragiles": fragiles,
        "subtotal": str(subtotal), "igv": str(igv), "total": str(subtotal + igv),
        "paquetes": paquetes,
    }
