"""Importación de la Codificación de la División Político-Administrativa (DIVIPOLA).

Acepta:
  * Archivo de municipios (CSV/XLSX del DANE o exportación de datos.gov.co).
  * Archivo de centros poblados (código de 8 dígitos: DDMMMCCC) con su tipo.
  * Respuesta JSON del API Socrata de datos.gov.co (misma estructura).

Los departamentos se crean a partir de los códigos presentes en el archivo.
"""
from __future__ import annotations

import json

import httpx

from .comun import cerrar_carga, codigo, iniciar_carga, leer_tabla, numero, registrar_fuente

ALIAS_MUNICIPIOS = {
    "cod_dpto": ["codigo_departamento", "cod_depto", "dpto_ccdgo", "codigo_dane_del_departamento", "cod_dep"],
    "nom_dpto": ["dpto", "departamento", "nombre_departamento", "dpto_cnmbr"],
    "cod_mpio": ["codigo_municipio", "cod_municipio", "mpio_cdpmp", "codigo_dane_del_municipio", "divipola"],
    "nom_mpio": ["municipio", "nombre_municipio", "mpio_cnmbr"],
    "tipo": ["tipo_municipio", "tipo_entidad"],
    "longitud": ["lon", "x"],
    "latitud": ["lat", "y"],
}
ALIAS_CP = {
    **ALIAS_MUNICIPIOS,
    "cod_cp": ["codigo_centro_poblado", "cod_centro_poblado", "codigo_cp", "cpob_ccdgo", "codigo_dane_cp"],
    "nom_cp": ["nombre_centro_poblado", "centro_poblado", "cpob_cnmbr", "nombre_cp"],
    "tipo_cp": ["tipo_centro_poblado", "tipo", "cpob_ctipo"],
}
TIPOS_CP = {"CM": "cabecera_municipal", "C": "corregimiento", "CD": "corregimiento", "IP": "inspeccion_policia",
            "IPM": "inspeccion_policia", "IPD": "inspeccion_policia", "CAS": "caserio", "CP": "centro_poblado"}


def _upsert(con, tipo, cod, nombre, padre, dpto, mpio, fuente_id, carga_id, clase=None, cat=None,
            lon=None, lat=None, demo=False) -> int:
    return con.execute(
        """INSERT INTO unidad_territorial (tipo, codigo, nombre, padre_id, cod_departamento, cod_municipio, clase,
               categoria_divipola, fuente_id, carga_id, clasificacion, centroide, es_demostracion)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   CASE WHEN %s::float IS NOT NULL AND %s::float IS NOT NULL THEN ST_SetSRID(ST_MakePoint(%s,%s),4326) END, %s)
           ON CONFLICT (sistema_codigo, codigo, tipo) DO UPDATE SET nombre=EXCLUDED.nombre, padre_id=EXCLUDED.padre_id,
               categoria_divipola=EXCLUDED.categoria_divipola, fuente_id=EXCLUDED.fuente_id, carga_id=EXCLUDED.carga_id,
               clasificacion=EXCLUDED.clasificacion,
               centroide=coalesce(EXCLUDED.centroide, unidad_territorial.centroide), actualizado_en=now()
           RETURNING id""",
        (tipo, cod, nombre, padre, dpto, mpio, clase, cat, fuente_id, carga_id, _clasif(demo),
         lon, lat, lon, lat, demo)).fetchone()["id"]


def _clasif(demo: bool) -> str:
    # La DIVIPOLA es codificación oficial del DANE, no un dato censal: se
    # etiqueta como información oficial administrativa. Lo ficticio, pendiente.
    return "pendiente_verificacion" if demo else "oficial_otra_entidad"


def importar_filas(con, filas: list[dict], archivo: str, data: bytes | None, version: str | None,
                   url: str | None = None, demo: bool = False, entidad: str = "DANE",
                   producto: str = "DIVIPOLA — Codificación de la División Político-Administrativa",
                   clasificacion: str | None = None, notas: str | None = None) -> dict:
    fuente_id = registrar_fuente(con, entidad, producto, version, url, notas=notas,
                                 nivel_desagregacion="departamento, municipio, centro poblado")
    carga_id = iniciar_carga(con, fuente_id, archivo, data, "divipola v1")
    pais = con.execute("SELECT id FROM unidad_territorial WHERE tipo='pais' AND codigo='170'").fetchone()
    pais_id = pais["id"] if pais else _upsert(con, "pais", "170", "Colombia", None, None, None, fuente_id, carga_id)
    deptos, mpios, n, adv = {}, {}, 0, []
    for f in filas:
        cd = codigo(f.get("cod_dpto"), 2)
        cm = codigo(f.get("cod_mpio"), 5)
        if cm and not cd:
            cd = cm[:2]
        if cm and len(cm) == 5 and cd and cm[:2] != cd:
            adv.append(f"Código de municipio {cm} no corresponde al departamento {cd}")
        if not cd:
            continue
        if cd not in deptos:
            deptos[cd] = _upsert(con, "departamento", cd, str(f.get("nom_dpto") or cd).strip(), pais_id, cd, None,
                                 fuente_id, carga_id, demo=demo)
        if cm and cm not in mpios:
            mpios[cm] = _upsert(con, "municipio", cm, str(f.get("nom_mpio") or cm).strip(), deptos[cd], cd, cm,
                                fuente_id, carga_id, "mixto", f.get("tipo"),
                                numero(f.get("longitud")), numero(f.get("latitud")), demo)
            n += 1
        ccp = codigo(f.get("cod_cp"), 8)
        if ccp and cm:
            t = str(f.get("tipo_cp") or "").strip().upper()
            tipo = TIPOS_CP.get(t, "centro_poblado")
            _upsert(con, tipo, ccp, str(f.get("nom_cp") or ccp).strip(), mpios[cm], cd, cm, fuente_id, carga_id,
                    "urbano" if tipo == "cabecera_municipal" else "rural", t or None,
                    numero(f.get("longitud")), numero(f.get("latitud")), demo)
            n += 1
    con.execute("UPDATE unidad_territorial SET clasificacion=%s WHERE carga_id=%s",
                (clasificacion or _clasif(demo), carga_id))
    cerrar_carga(con, carga_id, n, adv)
    return {"carga_id": carga_id, "departamentos": len(deptos), "registros": n, "advertencias": adv}


def importar_archivo(con, data: bytes, nombre: str, version: str | None = None, url: str | None = None, demo=False):
    if nombre.lower().endswith(".json"):
        filas = _desde_socrata(json.loads(data))
    else:
        filas = leer_tabla(data, nombre, ALIAS_CP)
    return importar_filas(con, filas, nombre, data, version, url, demo)


def _desde_socrata(registros: list[dict]) -> list[dict]:
    from .comun import normalizar
    out = []
    for r in registros:
        rn = {normalizar(k): v for k, v in r.items()}
        d = {}
        for canon, alias in ALIAS_CP.items():
            for a in [canon] + alias:
                if normalizar(a) in rn:
                    d[canon] = rn[normalizar(a)]
                    break
        out.append(d)
    return out


def descargar_socrata(dataset_id: str, dominio: str = "www.datos.gov.co", limite: int = 50_000) -> bytes:
    """Descarga un conjunto del portal de datos abiertos (API SODA). Requiere salida a internet."""
    url = f"https://{dominio}/resource/{dataset_id}.json"
    r = httpx.get(url, params={"$limit": limite}, timeout=120)
    r.raise_for_status()
    return r.content
