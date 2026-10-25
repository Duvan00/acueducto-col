"""Acceso a los parámetros normativos (fuente única: data/seed/parametros_normativos.json,
cargada en la tabla parametro_normativo)."""
from __future__ import annotations

import json
from pathlib import Path

RUTA = Path(__file__).resolve().parents[3] / "data" / "seed" / "parametros_normativos.json"


def cargar(ruta: Path = RUTA) -> list[dict]:
    return json.loads(ruta.read_text(encoding="utf-8"))["parametros"]


def _cumple(cond: dict, altitud=None, poblacion=None, regimen=None) -> bool:
    chk = {
        "altitud_min": lambda v: altitud is not None and altitud >= v,
        "altitud_max": lambda v: altitud is not None and altitud <= v,
        "altitud_min_excl": lambda v: altitud is not None and altitud > v,
        "altitud_max_excl": lambda v: altitud is not None and altitud < v,
        "poblacion_max": lambda v: poblacion is not None and poblacion <= v,
        "poblacion_min_excl": lambda v: poblacion is not None and poblacion > v,
        "regimen": lambda v: regimen == v,
    }
    return all(chk[k](v) for k, v in cond.items())


def buscar(params: list[dict], clave: str, **ctx) -> dict | None:
    for p in params:
        if p["clave"] == clave and _cumple(p.get("condicion", {}), **ctx):
            return p
    return None


def referencia(p: dict | None) -> str:
    if not p:
        return "Sin parámetro normativo aplicable"
    estado = {"verificado_texto_oficial": "verificado",
              "verificado_fuente_secundaria": "verificado en fuente secundaria — cotejar texto oficial",
              "pendiente": "PENDIENTE DE VERIFICACIÓN"}.get(p.get("estado_verificacion"), "")
    return f"{p['norma']}, {p.get('articulo', '')} [{estado}]"
