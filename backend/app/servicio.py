"""Orquesta consultas a la base y el motor de cálculo. Lo usan la API y la memoria,
de modo que lo que se ve en pantalla y lo que se exporta salen del mismo cálculo."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from .calc import caudales, normativa, proyeccion, sistema
from .calc.proyeccion import PuntoCensal

VERSION_MOTOR = "0.1.0"

ETIQUETA_CLASIF = {
    "censo_oficial_dane": "Dato censal oficial DANE",
    "proyeccion_oficial_dane": "Proyección oficial DANE",
    "oficial_otra_entidad": "Información oficial de otra entidad",
    "estimacion_tecnica": "Estimación técnica (calculada por la aplicación)",
    "informacion_campo": "Información de campo (usuario)",
    "pendiente_verificacion": "Dato pendiente de verificación",
}


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def unidad(con, uid: int) -> dict | None:
    u = con.execute("""SELECT id, tipo, codigo, sistema_codigo, nombre, padre_id, cod_departamento, cod_municipio, clase,
                              categoria_divipola, clasificacion, es_demostracion, altitud_msnm, altitud_fuente,
                              round(area_km2::numeric, 3) area_km2,
                              ST_X(centroide) lon, ST_Y(centroide) lat, geom IS NOT NULL tiene_geom
                       FROM unidad_territorial WHERE id=%s""", (uid,)).fetchone()
    if not u:
        return None
    u["ancestros"] = con.execute("""WITH RECURSIVE a AS (
            SELECT id, padre_id, tipo, nombre, codigo, 0 n FROM unidad_territorial WHERE id=%s
            UNION ALL SELECT p.id, p.padre_id, p.tipo, p.nombre, p.codigo, a.n+1 FROM unidad_territorial p JOIN a ON p.id=a.padre_id)
        SELECT id, tipo, nombre, codigo FROM a WHERE n>0 ORDER BY n DESC""", (uid,)).fetchall()
    return u


def demografia(con, uid: int) -> dict:
    filas = con.execute("""SELECT d.anio, d.area, d.variable, d.valor::float valor, d.clasificacion, d.serie, d.metodologia,
                                  d.soporte, f.entidad, f.producto, f.version, f.url, f.fecha_consulta
                           FROM dato_demografico d JOIN fuente f ON f.id=d.fuente_id
                           WHERE d.unidad_id=%s AND d.vigente ORDER BY d.anio, d.area, d.variable""", (uid,)).fetchall()
    censos, proy, otros = {}, {}, []
    for f in filas:
        f["fuente"] = f"{f['entidad']} — {f['producto']}" + (f" ({f['version']})" if f["version"] else "")
        f["fecha_consulta"] = f["fecha_consulta"] and f["fecha_consulta"].isoformat()
        if f["clasificacion"] == "censo_oficial_dane":
            k = (f["anio"], f["area"])
            c = censos.setdefault(k, {"anio": f["anio"], "area": f["area"], "serie": f["serie"], "fuente": f["fuente"],
                                      "clasificacion": f["clasificacion"], "metodologia": f["metodologia"]})
            c[f["variable"]] = f["valor"]
        elif f["clasificacion"] == "proyeccion_oficial_dane" and f["variable"] == "poblacion":
            proy.setdefault(f["area"], {"serie": f["serie"], "fuente": f["fuente"], "valores": {}})["valores"][f["anio"]] = f["valor"]
        else:
            otros.append(f)
    for c in censos.values():
        if c.get("poblacion") and c.get("viviendas_ocupadas"):
            c["personas_por_vivienda"] = c["poblacion"] / c["viviendas_ocupadas"]
        if c.get("poblacion") and c.get("hogares"):
            c["personas_por_hogar"] = c["poblacion"] / c["hogares"]
    return {"censos": sorted(censos.values(), key=lambda c: (c["area"], c["anio"])), "proyecciones_oficiales": proy,
            "otros": otros}


def parametros(con) -> list[dict]:
    rows = con.execute("SELECT clave, valor::float valor, unidad, condicion, norma, articulo, texto_referencia, "
                       "estado_verificacion, nota FROM parametro_normativo WHERE vigente_hasta IS NULL ORDER BY id").fetchall()
    return rows or normativa.cargar()


# ---------------------------------------------------------------------------
# Modelos de entrada
# ---------------------------------------------------------------------------

class CensoManual(BaseModel):
    anio: int
    poblacion: float
    fuente: str = "Ingresado por el usuario"
    clasificacion: str = "informacion_campo"
    comparable: bool = True
    nota: str = ""


class LocalidadReq(BaseModel):
    unidad_id: int | None = None
    nombre: str | None = None
    area: Literal["total", "cabecera", "centros_poblados_rural_disperso"] = "total"
    metodo: Literal["aritmetico", "geometrico", "exponencial", "dane_oficial"] = "geometrico"
    censos_manual: list[CensoManual] = []
    excluir_anios: list[int] = []               # censos que el usuario descarta
    no_comparables: list[int] = []              # censos marcados como no comparables
    cobertura_pct: float = Field(100, ge=0, le=100)
    anio_ingreso: int | None = None
    anio_base: int | None = None
    poblacion_base: float | None = None
    poblacion_base_soporte: str = ""
    tasa_manual: float | None = None
    # Para unidades sin serie censal propia (veredas): adoptar la tasa intercensal del
    # área correspondiente del municipio (rural para veredas/centros poblados, cabecera para la cabecera).
    tasa_referencia: Literal["ninguna", "municipio_area"] = "ninguna"
    justificacion: str = ""


class DemandaReq(BaseModel):
    tipo: str
    caudal_lps: float = Field(ge=0)
    justificacion: str = ""
    descripcion: str = ""
    afecta_k1: bool = True
    afecta_k2: bool = True
    incluye_perdidas: bool = False
    incluida_en_dotacion: bool = False


class CaudalesReq(BaseModel):
    altitud_msnm: float | None = None
    dotacion_neta: float | None = None
    perdidas_pct: float = 25
    k1: float | None = None
    k2: float | None = None
    k1_por_macromedicion: bool = False
    k2_por_macromedicion: bool = False
    regimen: Literal["general", "rural_esquema_diferencial"] = "general"
    justificaciones: dict[str, str] = {}


class EtapaReq(BaseModel):
    nombre: str
    anio_inicio: int
    anio_fin: int
    descripcion: str = ""


class ProyectoInfo(BaseModel):
    nombre: str = "Proyecto sin nombre"
    codigo: str = ""
    entidad: str = ""
    elaboro: str = ""
    descripcion: str = ""
    tipo: Literal["urbano", "rural", "regional"] = "urbano"


class CalculoReq(BaseModel):
    proyecto: ProyectoInfo = ProyectoInfo()
    localidades: list[LocalidadReq]
    anio_inicial: int = Field(default_factory=lambda: dt.date.today().year)
    periodo_diseno: int = 25
    criterio_tasa: Literal["promedio", "ultimo_periodo"] = "promedio"
    caudales: CaudalesReq = CaudalesReq()
    demandas: list[DemandaReq] = []
    etapas: list[EtapaReq] = []
    justificacion_periodo: str = ""


# ---------------------------------------------------------------------------
# Cálculo integral
# ---------------------------------------------------------------------------

def _datos_localidad(con, l: LocalidadReq) -> dict:
    info, demo, censos, serie_dane, fuentes = None, False, [], None, set()
    if l.unidad_id:
        info = unidad(con, l.unidad_id)
        if not info:
            raise ValueError(f"Unidad {l.unidad_id} no existe")
        demo = info["es_demostracion"]
        dem = demografia(con, l.unidad_id)
        for c in dem["censos"]:
            if c["area"] == l.area and c.get("poblacion") and c["anio"] not in l.excluir_anios:
                censos.append(PuntoCensal(c["anio"], c["poblacion"], c["fuente"], c["clasificacion"],
                                          c["anio"] not in l.no_comparables,
                                          "marcado no comparable por el usuario" if c["anio"] in l.no_comparables else ""))
                fuentes.add(c["fuente"])
        p = dem["proyecciones_oficiales"].get(l.area)
        if p:
            serie_dane = {int(k): v for k, v in p["valores"].items()}
            fuentes.add(p["fuente"])
    for c in l.censos_manual:
        censos.append(PuntoCensal(c.anio, c.poblacion, c.fuente, c.clasificacion, c.comparable, c.nota))
        fuentes.add(c.fuente)
    if info and l.poblacion_base is None and not censos:
        # Sin censos propios: la información de campo / otra entidad / estimación más reciente
        # puede servir de población base, conservando su clasificación.
        cand = [o for o in dem["otros"] if o["variable"] == "poblacion" and o["area"] == l.area]
        if cand:
            ult = max(cand, key=lambda o: o["anio"])
            l = l.model_copy(update={"poblacion_base": ult["valor"], "anio_base": ult["anio"],
                                     "poblacion_base_soporte": f"{ETIQUETA_CLASIF[ult['clasificacion']]}: {ult['fuente']}"
                                                               + (f" — {ult['soporte']}" if ult.get("soporte") else "")})
            fuentes.add(ult["fuente"])
    tasa_ref = None
    if info and l.tasa_referencia == "municipio_area" and l.tasa_manual is None:
        mpio = next((a for a in info["ancestros"] if a["tipo"] == "municipio"), None)
        if not mpio:
            raise ValueError(f"{info['nombre']}: no tiene municipio asociado para tomar la tasa de referencia.")
        area_ref = "cabecera" if info["tipo"] == "cabecera_municipal" else "centros_poblados_rural_disperso"
        cm = [PuntoCensal(c["anio"], c["poblacion"], c["fuente"]) for c in demografia(con, mpio["id"])["censos"]
              if c["area"] == area_ref and c.get("poblacion") and c["anio"] not in l.no_comparables]
        metodo_ref = l.metodo if l.metodo != "dane_oficial" else "geometrico"
        tasa_ref = proyeccion.tasa_adoptada(proyeccion.tasas_intercensales(cm), metodo_ref)
        if tasa_ref is None:
            raise ValueError(f"El municipio {mpio['nombre']} no tiene censos comparables del área {area_ref}.")
        if metodo_ref == "aritmetico" and l.poblacion_base and cm:
            # El crecimiento absoluto del municipio no es transferible: se escala a la población de la unidad.
            tasa_ref = tasa_ref * l.poblacion_base / cm[-1].poblacion
        l = l.model_copy(update={"tasa_manual": tasa_ref, "justificacion": (l.justificacion + " " if l.justificacion else "")
                                 + f"Tasa de referencia: {metodo_ref} intercensal del área '{area_ref}' del municipio "
                                   f"{mpio['nombre']} ({tasa_ref:.5g})."})
    nombre = l.nombre or (info["nombre"] if info else "Localidad")
    if info and l.area != "total":
        nombre += {"cabecera": " — cabecera", "centros_poblados_rural_disperso": " — centros poblados y rural disperso"}[l.area]
    return {"info": info, "demo": demo, "censos": censos, "serie_dane": serie_dane, "nombre": nombre, "fuentes": fuentes,
            "req": l, "tasa_referencia": tasa_ref}


def calcular(con, req: CalculoReq) -> dict:
    if not req.localidades:
        raise ValueError("Seleccione al menos una localidad.")
    params = parametros(con)
    p_per = normativa.buscar(params, "periodo_diseno")
    adv = []
    if p_per and req.periodo_diseno != p_per["valor"]:
        adv.append({"nivel": "advertencia", "codigo": "PERIODO_DISTINTO",
                    "mensaje": f"Período de diseño de {req.periodo_diseno} años distinto de {p_per['valor']:g} años "
                               f"({p_per['articulo']}); requiere análisis técnico-económico que lo justifique."
                               + (f" Justificación: {req.justificacion_periodo}" if req.justificacion_periodo else "")})
    anio_final = req.anio_inicial + req.periodo_diseno

    locs, detalle_loc, fuentes, demo = [], [], set(), False
    for l in req.localidades:
        d = _datos_localidad(con, l)
        l = d["req"]
        demo |= d["demo"]
        fuentes |= d["fuentes"]
        comp = proyeccion.comparar_metodos(d["censos"], l.anio_base, l.poblacion_base, anio_final,
                                           req.criterio_tasa, d["serie_dane"], anio_inicial=req.anio_inicial)
        if l.metodo not in comp["resultados"] and l.tasa_manual is None:
            raise ValueError(f"{d['nombre']}: el método {l.metodo} no es aplicable — "
                             + comp["errores"].get(l.metodo, ""))
        sel = proyeccion.proyectar(l.metodo, d["censos"], l.anio_base, l.poblacion_base, anio_final, req.anio_inicial,
                                   l.tasa_manual, req.criterio_tasa, d["serie_dane"])
        if l.poblacion_base is not None:
            sel.advertencias.append(proyeccion.Advertencia("info", "POBLACION_BASE_AJUSTADA",
                f"Población base adoptada ({l.poblacion_base:,.0f} hab en {sel.anio_base}). "
                f"Soporte: {l.poblacion_base_soporte or 'NO DECLARADO'}"))
            if not l.poblacion_base_soporte:
                sel.advertencias.append(proyeccion.Advertencia("critica", "AJUSTE_SIN_SOPORTE",
                    "El ajuste de población base no tiene soporte documental."))
        ancestros = [a["id"] for a in (d["info"] or {}).get("ancestros", [])]
        # Seleccionar el área 'cabecera' de un municipio equivale a su unidad cabecera_municipal: se trata como ancestro.
        locs.append(sistema.LocalidadSistema(l.unidad_id or -len(locs) - 1, d["nombre"], d["censos"], l.metodo,
                                             l.cobertura_pct, l.anio_ingreso, l.poblacion_base, l.anio_base,
                                             l.tasa_manual, d["serie_dane"], ancestros, l.area))
        detalle_loc.append({
            "unidad": d["info"], "nombre": d["nombre"], "area": l.area, "metodo": l.metodo,
            "censos": [c.__dict__ for c in d["censos"]],
            "tasas": proyeccion.tasas_intercensales(d["censos"]),
            "serie_dane": d["serie_dane"],
            "comparacion": {m: r.as_dict() for m, r in comp["resultados"].items()},
            "errores_metodos": comp["errores"],
            "recomendacion": comp["recomendacion"],
            "seleccion": sel.as_dict(),
            "justificacion": proyeccion.justificacion_texto(l.metodo, sel, comp["recomendacion"], l.justificacion),
            "cobertura_pct": l.cobertura_pct, "anio_ingreso": l.anio_ingreso,
            "poblacion_base_soporte": l.poblacion_base_soporte, "tasa_referencia": d["tasa_referencia"],
        })

    superp = []
    ids = [x["unidad"]["id"] for x in detalle_loc if x["unidad"] and x["area"] == "total"
           and x["unidad"]["tipo"] not in ("municipio", "departamento", "pais")]
    if len(ids) > 1:
        superp = [dict(r) for r in con.execute("SELECT a, b, fraccion_a::float, fraccion_b::float FROM f_superposiciones(%s)",
                                               (ids,)).fetchall()]
    # Municipio (área cabecera o rural) + unidades que pertenecen a esa misma área → doble conteo
    for a in locs:
        for b in locs:
            if a is not b and a.unidad_id in b.ancestros and a.area != "total":
                tipo_b = next((x["unidad"]["tipo"] for x in detalle_loc if x["unidad"] and x["unidad"]["id"] == b.unidad_id), "")
                misma = (a.area == "cabecera") == (tipo_b == "cabecera_municipal")
                if not misma:
                    b.ancestros = [x for x in b.ancestros if x != a.unidad_id]
    cons = sistema.consolidar(locs, req.anio_inicial, anio_final, superp, [e.model_dump() for e in req.etapas])
    for x, c in zip(detalle_loc, cons["localidades"]):
        x["serie_servida"] = c["serie"]

    P = cons["poblacion_diseno"]
    altitud = req.caudales.altitud_msnm
    if altitud is None:
        alts = [x["unidad"]["altitud_msnm"] for x in detalle_loc if x["unidad"] and x["unidad"]["altitud_msnm"] is not None]
        if alts:
            altitud = float(sum(alts) / len(alts))
            adv.append({"nivel": "info", "codigo": "ALTITUD_PROMEDIO",
                        "mensaje": f"Altitud promedio tomada de las localidades: {altitud:.0f} m s.n.m. Verifíquela."})
    entrada_q = caudales.EntradaCaudales(
        poblacion=P, altitud_msnm=altitud, dotacion_neta=req.caudales.dotacion_neta,
        perdidas_pct=req.caudales.perdidas_pct, k1=req.caudales.k1, k2=req.caudales.k2,
        k1_por_macromedicion=req.caudales.k1_por_macromedicion, k2_por_macromedicion=req.caudales.k2_por_macromedicion,
        regimen=req.caudales.regimen, demandas=[caudales.DemandaAdicional(**d.model_dump()) for d in req.demandas],
        justificaciones=req.caudales.justificaciones)
    q = caudales.calcular(entrada_q, params)

    # Caudales al final de cada etapa (mismos parámetros, población de la etapa)
    q_etapas = []
    for e in cons["etapas"]:
        if e["poblacion_servida"]:
            qe = caudales.calcular(caudales.EntradaCaudales(**{**entrada_q.__dict__, "poblacion": e["poblacion_servida"]}), params)
            q_etapas.append({**e, "qmd_lps": qe["qmd_lps"], "QMD_lps": qe["QMD_lps"], "QMH_lps": qe["QMH_lps"],
                             "k1": qe["k1"], "k2": qe["k2"]})

    if demo:
        adv.insert(0, {"nivel": "critica", "codigo": "DATOS_DEMOSTRACION",
                       "mensaje": "El cálculo usa DATOS FICTICIOS DE DEMOSTRACIÓN. No es válido para ningún diseño."})
    pendientes = [x["nombre"] for x in detalle_loc if x["unidad"] and x["unidad"]["clasificacion"] == "pendiente_verificacion"
                  and not x["unidad"]["es_demostracion"]]
    if pendientes:
        adv.append({"nivel": "advertencia", "codigo": "UNIDAD_PENDIENTE",
                    "mensaje": "Unidades territoriales con codificación pendiente de verificación (semilla no oficial): "
                               + ", ".join(pendientes) + ". Importe la DIVIPOLA vigente."})
    sin_datos = [x["nombre"] for x in detalle_loc if not x["censos"] and not x["serie_dane"]
                 and x["seleccion"]["poblacion_base"] is None]
    if sin_datos:
        adv.append({"nivel": "critica", "codigo": "SIN_DATOS",
                    "mensaje": "Sin información demográfica: " + ", ".join(sin_datos)})

    resultado = {
        "version_motor": VERSION_MOTOR, "fecha": dt.datetime.now().isoformat(timespec="seconds"),
        "proyecto": req.proyecto.model_dump(), "anio_inicial": req.anio_inicial, "anio_final": anio_final,
        "periodo_diseno": req.periodo_diseno, "periodo_norma": p_per, "criterio_tasa": req.criterio_tasa,
        "localidades": detalle_loc, "sistema": cons, "caudales": q, "caudales_etapas": q_etapas,
        "parametros_normativos": params, "fuentes": sorted(fuentes), "es_demostracion": demo,
        "advertencias": adv + cons["advertencias"] + q["advertencias"],
        "limitaciones": limitaciones(detalle_loc, q),
    }
    entrada = req.model_dump()
    resultado["sha256"] = hashlib.sha256(json.dumps(entrada, sort_keys=True, default=str).encode()).hexdigest()
    return resultado


def limitaciones(detalle_loc, q) -> list[str]:
    out = [
        "Las proyecciones calculadas por los métodos aritmético, geométrico y exponencial son estimaciones técnicas y "
        "no constituyen cifras oficiales del DANE.",
        "Los censos de 1993, 2005 y 2018 difieren en cobertura, omisión censal y ajustes de conciliación; las tasas "
        "intercensales deben interpretarse con cautela, especialmente si hubo cambios de límites territoriales.",
        "El DANE no publica población oficial por vereda: cualquier cifra veredal es una estimación técnica o "
        "información de campo y así se identifica.",
    ]
    if any(x["unidad"] and x["unidad"]["tipo"] == "vereda" for x in detalle_loc):
        out.append("Los límites veredales no coinciden necesariamente con las secciones o sectores censales del MGN.")
    if any(a["codigo"].endswith("SIN_MEDICION") for a in q["advertencias"]):
        out.append("Los coeficientes K1 y K2 no provienen de registros de macromedición; se adoptaron como máximos "
                   "normativos y deben revisarse cuando existan registros.")
    if q.get("dotacion_neta") == q.get("dotacion_neta_max"):
        out.append("Se adoptó la dotación neta máxima normativa; no se dispuso de consumos medidos que la sustenten.")
    return out


def guardar_calculo(con, req: CalculoReq, res: dict, proyecto_id: int | None = None) -> int:
    return con.execute("INSERT INTO calculo (proyecto_id, version_motor, entrada, resultado, sha256) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                       (proyecto_id, VERSION_MOTOR, json.dumps(req.model_dump(), default=str),
                        json.dumps(res, default=str), res["sha256"])).fetchone()["id"]
