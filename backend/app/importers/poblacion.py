"""Importación de series de población.

1. Proyecciones / retroproyecciones municipales por área del DANE
   (serie 2018-2042 y retroproyección 1985-2017 basadas en el CNPV 2018).
   Se aceptan los dos formatos usados por el DANE:
     * largo: una fila por municipio-año-área (DP, DPNOM, DPMP, AÑO, ÁREA GEOGRÁFICA, Población)
     * ancho: una fila por municipio-área con una columna por año.
2. Resultados censales por municipio y área (CNPV 2018, CG 2005, CNPV 1993):
   población total, hombres, mujeres, viviendas, hogares.
3. Datos de campo u oficiales de otra entidad para cualquier unidad.

Una nueva versión de una serie NO borra la anterior: las filas previas de la
misma familia se marcan vigente = false.
"""
from __future__ import annotations

import re

from .comun import cerrar_carga, codigo, iniciar_carga, leer_tabla, normalizar, numero, registrar_fuente

ALIAS_PROY = {
    "cod_mpio": ["dpmp", "cod_mpio", "codigo_municipio", "mpio", "cod_dane", "codigo_dane"],
    "nom_mpio": ["municipio", "mpnom", "nombre_municipio"],
    "anio": ["ano", "año", "anio", "year"],
    "area": ["area_geografica", "area", "zona", "area_geografica_"],
    "poblacion": ["poblacion", "total", "poblacion_total", "total_general"],
    "hombres": ["hombres", "total_hombres"],
    "mujeres": ["mujeres", "total_mujeres"],
}
ALIAS_CENSO = {
    "codigo": ["cod_mpio", "dpmp", "codigo_municipio", "codigo_dane", "codigo", "cod_dane", "divipola"],
    "area": ["area_geografica", "area", "clase", "zona"],
    "poblacion": ["poblacion", "personas", "total_personas", "poblacion_total", "stp27_pers"],
    "hombres": ["hombres"],
    "mujeres": ["mujeres"],
    "viviendas": ["viviendas", "total_viviendas", "stvivienda"],
    "viviendas_ocupadas": ["viviendas_ocupadas", "viviendas_ocupadas_con_personas_presentes"],
    "hogares": ["hogares", "total_hogares", "sthogar"],
}


def area_canonica(v) -> str:
    n = normalizar(v)
    if not n or n.startswith("total"):
        return "total"
    if n.startswith("cabecera") or n in ("urbano", "urbana", "1"):
        return "cabecera"
    if "rural" in n or "centro" in n or n in ("resto", "2", "3"):
        return "centros_poblados_rural_disperso"
    raise ValueError(f"Área geográfica no reconocida: {v!r}")


def _unidad_por_codigo(con, cod: str, cache: dict) -> int | None:
    if cod not in cache:
        r = con.execute("SELECT id FROM unidad_territorial WHERE codigo=%s AND tipo IN ('municipio','departamento',"
                        "'centro_poblado','cabecera_municipal','corregimiento','inspeccion_policia','caserio','vereda',"
                        "'sector_rural','seccion_rural') ORDER BY tipo='municipio' DESC LIMIT 1", (cod,)).fetchone()
        cache[cod] = r["id"] if r else None
    return cache[cod]


def _insertar(con, unidad_id, anio, area, variable, valor, clasif, fuente_id, carga_id, serie, metodologia,
              familia: str | None, soporte=None):
    if familia:
        con.execute("""UPDATE dato_demografico SET vigente=false WHERE unidad_id=%s AND anio=%s AND area=%s
                       AND variable=%s AND serie LIKE %s AND serie<>%s AND vigente""",
                    (unidad_id, anio, area, variable, familia + "%", serie))
    con.execute("""INSERT INTO dato_demografico (unidad_id, anio, area, variable, valor, clasificacion, fuente_id,
                       carga_id, serie, metodologia, soporte)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (unidad_id, anio, area, variable, serie) WHERE vigente
                   DO UPDATE SET valor=EXCLUDED.valor, carga_id=EXCLUDED.carga_id, fuente_id=EXCLUDED.fuente_id""",
                (unidad_id, anio, area, variable, valor, clasif, fuente_id, carga_id, serie, metodologia, soporte))


def importar_proyecciones(con, data: bytes, nombre: str, version: str, url: str | None = None,
                          producto="Proyecciones y retroproyecciones de población municipal por área (base CNPV 2018)",
                          hoja: str | None = None) -> dict:
    """version: fecha/versión publicada por el DANE, p. ej. '2025-08-08'. Define la serie PROY_MUN_AREA_<version>."""
    filas = leer_tabla(data, nombre, ALIAS_PROY, hoja)
    fuente_id = registrar_fuente(con, "DANE", producto, version, url, nivel_desagregacion="municipio × área geográfica")
    carga_id = iniciar_carga(con, fuente_id, nombre, data, "proyecciones_municipales v1")
    serie = f"PROY_MUN_AREA_{version}"
    cache, n, adv, faltantes = {}, 0, [], set()
    metodologia = ("Proyección oficial DANE (método de componentes demográficos, base CNPV 2018 conciliado). "
                   "Valores tal como fueron publicados.")
    for f in filas:
        cod = codigo(f.get("cod_mpio"), 5)
        if not cod:
            continue
        uid = _unidad_por_codigo(con, cod, cache)
        if uid is None:
            faltantes.add(cod)
            continue
        area = area_canonica(f.get("area"))
        if f.get("anio") is not None and f.get("poblacion") is not None:        # formato largo
            pares = [(int(numero(f["anio"])), numero(f["poblacion"]), numero(f.get("hombres")), numero(f.get("mujeres")))]
        else:                                                                   # formato ancho
            pares = [(int(k), numero(v), None, None) for k, v in f["_extra"].items()
                     if re.fullmatch(r"(19|20)\d\d", k) and numero(v) is not None]
        for anio, pob, h, m in pares:
            clasif = "proyeccion_oficial_dane"
            for var, val in (("poblacion", pob), ("hombres", h), ("mujeres", m)):
                if val is not None:
                    _insertar(con, uid, anio, area, var, val, clasif, fuente_id, carga_id, serie,
                              metodologia + (" Retroproyección." if anio < 2018 else ""), "PROY_MUN_AREA_")
                    n += 1
    if faltantes:
        adv.append(f"{len(faltantes)} códigos sin unidad territorial (importe primero la DIVIPOLA): "
                   + ", ".join(sorted(faltantes)[:20]))
    cerrar_carga(con, carga_id, n, adv)
    return {"carga_id": carga_id, "serie": serie, "registros": n, "advertencias": adv}


def importar_tabla_poblacion(con, data: bytes, nombre: str, anio: int, serie: str, clasificacion: str,
                             entidad: str, producto: str, version: str | None = None, url: str | None = None,
                             metodologia: str = "", soporte: str | None = None, hoja: str | None = None) -> dict:
    """Censos (clasificacion='censo_oficial_dane'), datos de otras entidades o de campo."""
    if clasificacion == "estimacion_tecnica":
        raise ValueError("Las estimaciones técnicas las genera la aplicación; no se importan como tabla.")
    if clasificacion == "censo_oficial_dane" and entidad != "DANE":
        raise ValueError("Un dato censal oficial del DANE debe tener como entidad 'DANE'.")
    if serie.startswith("EST_"):
        raise ValueError("El prefijo EST_ está reservado para estimaciones técnicas.")
    filas = leer_tabla(data, nombre, ALIAS_CENSO, hoja)
    fuente_id = registrar_fuente(con, entidad, producto, version, url)
    carga_id = iniciar_carga(con, fuente_id, nombre, data, "tabla_poblacion v1")
    cache, n, faltantes, adv = {}, 0, set(), []
    for f in filas:
        raw = str(f.get("codigo") or "").strip()
        cod = re.sub(r"\D", "", raw)
        cod = cod.zfill(5) if len(cod) in (4, 5) else cod.zfill(8) if len(cod) in (7, 8) else raw
        uid = _unidad_por_codigo(con, cod, cache)
        if uid is None:
            faltantes.add(cod)
            continue
        area = area_canonica(f.get("area")) if f.get("area") not in (None, "") else "total"
        valores = {v: numero(f.get(v)) for v in ("poblacion", "hombres", "mujeres", "viviendas", "viviendas_ocupadas", "hogares")}
        if valores["hombres"] and valores["mujeres"] and valores["poblacion"]:
            if abs(valores["hombres"] + valores["mujeres"] - valores["poblacion"]) > 1:
                adv.append(f"{cod} {area}: hombres + mujeres ≠ población")
        for var, val in valores.items():
            if val is not None:
                _insertar(con, uid, anio, area, var, val, clasificacion, fuente_id, carga_id, serie, metodologia,
                          None, soporte)
                n += 1
    if faltantes:
        adv.append(f"{len(faltantes)} códigos sin unidad territorial: " + ", ".join(sorted(faltantes)[:20]))
    cerrar_carga(con, carga_id, n, adv)
    return {"carga_id": carga_id, "serie": serie, "registros": n, "advertencias": adv}
