"""Estimación de población de veredas u otras unidades sin dato censal propio.

Todas las funciones devuelven valores clasificados como 'estimacion_tecnica'
(o 'informacion_campo' si el dato base proviene de un conteo del usuario), con
la descripción de la metodología. Nunca se asigna a una vereda la población
total del área rural del municipio.

Métodos:
 A. Prorrateo de unidades censales (sectores/secciones rurales del MGN
    integrado con el CNPV 2018) según la fracción de viviendas —o, en su
    defecto, de área— de cada unidad censal que cae dentro de la vereda.
 B. Viviendas × habitantes por vivienda (conteo de viviendas ocupadas en
    campo o en imagen, y PPV del rural disperso del municipio, CNPV 2018).
 C. Hogares × personas por hogar.
 D. Censo local / encuesta del usuario (dato de campo, no estimación).
"""
from __future__ import annotations


def prorrateo_unidades_censales(partes: list[dict]) -> dict:
    """partes: [{codigo, poblacion, viviendas_total, viviendas_en_vereda | fraccion_area}]"""
    total, detalle, usa_area = 0.0, [], False
    for p in partes:
        if p.get("viviendas_en_vereda") is not None and p.get("viviendas_total"):
            f = p["viviendas_en_vereda"] / p["viviendas_total"]
            base = "viviendas"
        elif p.get("fraccion_area") is not None:
            f = p["fraccion_area"]
            base = "área"
            usa_area = True
        else:
            raise ValueError(f"Unidad {p.get('codigo')}: falta fracción de viviendas o de área")
        if not 0 <= f <= 1:
            raise ValueError("Fracción fuera de [0,1]")
        v = p["poblacion"] * f
        total += v
        detalle.append({**p, "fraccion": f, "base": base, "aporte": v})
    adv = []
    if usa_area:
        adv.append("Se usó fracción de área en al menos una unidad: supone población uniformemente distribuida, "
                   "lo cual rara vez ocurre en zonas rurales. Prefiera el conteo de viviendas.")
    return {"poblacion": total, "clasificacion": "estimacion_tecnica", "detalle": detalle, "advertencias": adv,
            "metodologia": "Prorrateo de la población de unidades censales del MGN/CNPV 2018 según la fracción de "
                           "viviendas (o de área) de cada unidad contenida en la vereda."}


def viviendas_por_ppv(viviendas_ocupadas: float, ppv: float, fuente_viviendas: str, fuente_ppv: str) -> dict:
    if viviendas_ocupadas < 0 or ppv <= 0:
        raise ValueError("Valores inválidos")
    return {"poblacion": viviendas_ocupadas * ppv, "clasificacion": "estimacion_tecnica", "advertencias": [],
            "metodologia": f"Viviendas ocupadas ({viviendas_ocupadas:g}; {fuente_viviendas}) × habitantes por "
                           f"vivienda ({ppv:g}; {fuente_ppv})."}


def hogares_por_pph(hogares: float, pph: float, fuente_hogares: str, fuente_pph: str) -> dict:
    if hogares < 0 or pph <= 0:
        raise ValueError("Valores inválidos")
    return {"poblacion": hogares * pph, "clasificacion": "estimacion_tecnica", "advertencias": [],
            "metodologia": f"Hogares ({hogares:g}; {fuente_hogares}) × personas por hogar ({pph:g}; {fuente_pph})."}


def validar_contra_rural(estimaciones_veredas: dict[str, float], poblacion_rural_municipio: float) -> list[str]:
    """La suma de veredas estimadas no debería superar la población rural oficial del municipio."""
    s = sum(estimaciones_veredas.values())
    if poblacion_rural_municipio and s > poblacion_rural_municipio * 1.02:
        return [f"La suma de veredas estimadas ({s:,.0f}) supera la población de centros poblados y rural disperso "
                f"del municipio ({poblacion_rural_municipio:,.0f}). Revise superposiciones o fuentes."]
    return []
