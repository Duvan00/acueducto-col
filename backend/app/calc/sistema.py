"""Sistemas que abastecen varias localidades (regionales / veredales).

Cada localidad se proyecta por separado con su propio método. La población
servida en el año t es

    Ps,i(t) = cobertura_i · P_i(t)     si t ≥ año de ingreso (etapa) de la localidad
            = 0                         en caso contrario

y la del sistema es la suma de las Ps,i. Antes de sumar se detectan
duplicidades: la misma unidad seleccionada dos veces, una unidad y su
ancestro/descendiente (p. ej. municipio + vereda del mismo municipio, o
"centros poblados y rural disperso" + una vereda), y superposiciones
geométricas reportadas por PostGIS.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .proyeccion import PuntoCensal, proyectar, ResultadoProyeccion


@dataclass
class LocalidadSistema:
    unidad_id: int
    nombre: str
    censos: list[PuntoCensal]
    metodo: str
    cobertura_pct: float = 100.0
    anio_ingreso: int | None = None
    poblacion_base: float | None = None
    anio_base: int | None = None
    tasa_manual: float | None = None
    serie_dane: dict[int, float] | None = None
    ancestros: list[int] = field(default_factory=list)   # ids de unidades que la contienen
    area: str = "total"


def detectar_duplicidades(locs: list[LocalidadSistema], superposiciones: list[dict] | None = None) -> list[dict]:
    out = []
    vistos = {}
    for l in locs:
        k = (l.unidad_id, l.area)
        if k in vistos:
            out.append({"nivel": "critica", "codigo": "UNIDAD_REPETIDA",
                        "mensaje": f"'{l.nombre}' está seleccionada más de una vez."})
        vistos[k] = l
    for l in locs:
        if l.area != "total" and (l.unidad_id, "total") in vistos:
            out.append({"nivel": "critica", "codigo": "AREA_CONTENIDA_EN_TOTAL",
                        "mensaje": f"'{l.nombre}' es un área de una unidad también seleccionada con su total."})
    ids = {l.unidad_id: l for l in locs}
    for l in locs:
        for a in l.ancestros:
            if a in ids:
                out.append({"nivel": "critica", "codigo": "JERARQUIA_SUPERPUESTA",
                            "mensaje": f"'{l.nombre}' está contenida en '{ids[a].nombre}', también seleccionada: "
                                       "sus habitantes se contarían dos veces. Retire una de las dos o reduzca la cobertura."})
    for s in superposiciones or []:
        if max(s.get("fraccion_a") or 0, s.get("fraccion_b") or 0) > 0.01:
            na, nb = ids.get(s["a"]), ids.get(s["b"])
            out.append({"nivel": "advertencia", "codigo": "SUPERPOSICION_GEOMETRICA",
                        "mensaje": f"Las áreas de '{na.nombre if na else s['a']}' y '{nb.nombre if nb else s['b']}' "
                                   f"se superponen ({s['fraccion_a']*100:.1f} % / {s['fraccion_b']*100:.1f} %). Verifique que la "
                                   "población de una no incluya la de la otra; si la incluye, retire una o ajuste coberturas."})
    return out


def consolidar(locs: list[LocalidadSistema], anio_inicial: int, anio_final: int,
               superposiciones=None, etapas: list[dict] | None = None) -> dict:
    adv = detectar_duplicidades(locs, superposiciones)
    detalle = []
    total = {t: 0.0 for t in range(anio_inicial, anio_final + 1)}
    for l in locs:
        r: ResultadoProyeccion = proyectar(l.metodo, l.censos, l.anio_base, l.poblacion_base, anio_final,
                                           anio_inicial, tasa_manual=l.tasa_manual, serie_dane=l.serie_dane)
        cob = l.cobertura_pct / 100
        serie = []
        for p in r.serie:
            t = p["anio"]
            if t < anio_inicial:
                continue
            activa = l.anio_ingreso is None or t >= l.anio_ingreso
            ps = (p["poblacion"] or 0) * cob if activa else 0.0
            total[t] += ps
            serie.append({"anio": t, "poblacion": p["poblacion"], "servida": ps, "clasificacion": p["clasificacion"]})
        if l.cobertura_pct < 100:
            adv.append({"nivel": "info", "codigo": "COBERTURA_PARCIAL",
                        "mensaje": f"'{l.nombre}': cobertura del {l.cobertura_pct:g} %."})
        detalle.append({"unidad_id": l.unidad_id, "nombre": l.nombre, "metodo": l.metodo, "tasa": r.tasa,
                        "cobertura_pct": l.cobertura_pct, "anio_ingreso": l.anio_ingreso, "serie": serie,
                        "advertencias": [a.__dict__ for a in r.advertencias]})
    serie_total = [{"anio": t, "poblacion_servida": v} for t, v in total.items()]
    cortes = []
    for e in etapas or []:
        fin = min(e["anio_fin"], anio_final)
        cortes.append({**e, "poblacion_servida": total.get(fin)})
    return {"localidades": detalle, "serie_total": serie_total, "etapas": cortes,
            "poblacion_diseno": total[anio_final], "advertencias": adv}
