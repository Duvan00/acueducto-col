"""Carga inicial: normativa, DIVIPOLA semilla (no oficial) y conjunto de demostración ficticio."""
from __future__ import annotations

import json

from .calc import normativa
from .db import RAIZ
from .importers import divipola
from .importers.comun import iniciar_carga, cerrar_carga, leer_tabla, registrar_fuente


def cargar_normativa(con):
    if con.execute("SELECT count(*) n FROM parametro_normativo").fetchone()["n"]:
        return 0
    for p in normativa.cargar():
        con.execute("""INSERT INTO parametro_normativo (clave, valor, unidad, condicion, norma, articulo, texto_referencia,
                          estado_verificacion, nota, referencia_verificacion, vigente_desde)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (p["clave"], p["valor"], p.get("unidad"), json.dumps(p.get("condicion", {})), p["norma"],
                     p.get("articulo"), p.get("texto_referencia"), p.get("estado_verificacion", "pendiente"),
                     p.get("nota"), "data/seed/parametros_normativos.json", None))
    return len(normativa.cargar())


def cargar_divipola_semilla(con):
    ruta = RAIZ / "data" / "seed" / "divipola_municipios_npm2021.csv"
    data = ruta.read_bytes()
    filas = leer_tabla(data, ruta.name, divipola.ALIAS_MUNICIPIOS)
    return divipola.importar_filas(
        con, filas, ruta.name, data, "1.0.3 (2021)", "https://www.npmjs.com/package/divipola",
        entidad="Copia no oficial (paquete npm 'divipola', licencia MIT)",
        producto="Listado de municipios DIVIPOLA — copia secundaria",
        clasificacion="pendiente_verificacion",
        notas="Semilla para operar sin conexión. Puede no reflejar municipios creados después de 2021. "
              "Reemplácela importando la DIVIPOLA vigente del DANE (scripts/actualizar_fuentes.py).")


def _poligono_circulo(cx, cy, r, n=24):
    import math
    pts = [[cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)] for i in range(n)]
    return pts + [pts[0]]


def cargar_demo(con):
    d = json.loads((RAIZ / "data" / "demo" / "demo.json").read_text(encoding="utf-8"))
    if con.execute("SELECT 1 FROM unidad_territorial WHERE codigo=%s", (d["municipio"]["codigo"],)).fetchone():
        return {"omitido": True}
    f = d["fuente"]
    fid = registrar_fuente(con, f["entidad"], f["producto"], f["version"], notas=d["_advertencia"])
    cid = iniciar_carga(con, fid, "data/demo/demo.json", None, "demo v1")
    pais = con.execute("SELECT id FROM unidad_territorial WHERE tipo='pais'").fetchone()

    def ins(tipo, cod, nombre, padre, clase, anillo, alt=None, sistema="DIVIPOLA", centro=None):
        gj = json.dumps({"type": "Polygon", "coordinates": [anillo]})
        return con.execute(
            """INSERT INTO unidad_territorial (tipo, codigo, sistema_codigo, nombre, padre_id, cod_departamento, cod_municipio,
                   clase, fuente_id, carga_id, clasificacion, es_demostracion, altitud_msnm, altitud_fuente, geom, geom_fuente_id, centroide)
               VALUES (%s,%s,%s,%s,%s,'XX',%s,%s,%s,%s,'pendiente_verificacion',true,%s,'Ficticio',
                       ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)),%s,
                       ST_PointOnSurface(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))) RETURNING id""",
            (tipo, cod, sistema, nombre, padre, None if tipo == "departamento" else d["municipio"]["codigo"], clase, fid, cid, alt,
             gj, fid, gj)).fetchone()["id"]

    mp = d["municipio_poligono"]
    dep = ins("departamento", d["departamento"]["codigo"], d["departamento"]["nombre"], pais and pais["id"], None,
              [[-79.75, 3.9], [-79.4, 3.9], [-79.4, 4.2], [-79.75, 4.2], [-79.75, 3.9]])
    m = d["municipio"]
    mid = ins("municipio", m["codigo"], m["nombre"], dep, "mixto", mp, m["altitud_msnm"])
    ids = {m["codigo"]: mid}
    for u in d["unidades"]:
        anillo = u.get("poligono") or _poligono_circulo(*u["centro"], u["radio"])
        ids[u["codigo"]] = ins(u["tipo"], u["codigo"], u["nombre"], mid, u["clase"] if "clase" in u else "rural",
                               anillo, u.get("altitud_msnm"), u.get("sistema", "DIVIPOLA"))

    def dato(uid, anio, area, var, val, clasif, serie, metodo, soporte=None):
        con.execute("""INSERT INTO dato_demografico (unidad_id, anio, area, variable, valor, clasificacion, fuente_id,
                          carga_id, serie, metodologia, soporte) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (uid, anio, area, var, val, clasif, fid, cid, serie, metodo, soporte))

    n = 0
    for c in d["censos"]:
        for var in ("poblacion", "hombres", "mujeres", "viviendas", "viviendas_ocupadas", "hogares"):
            if var in c:
                dato(ids[c["codigo"]], c["anio"], c["area"], var, c[var], "censo_oficial_dane", c["serie"],
                     "FICTICIO — simula un resultado censal")
                n += 1
    p = d["proyeccion"]
    for area, base in p["base"].items():
        for y in range(p["anio_inicial"], p["anio_final"] + 1):
            dato(mid, y, area, "poblacion", round(base * (1 + p["tasas"][area]) ** (y - p["anio_inicial"])),
                 "proyeccion_oficial_dane", p["serie"], "FICTICIO — simula una proyección oficial")
            n += 1
    for c in d["campo"]:
        dato(ids[c["codigo"]], c["anio"], "total", c["variable"], c["valor"], "informacion_campo", "CAMPO_DEMO",
             "FICTICIO — simula información de campo", c["soporte"])
        n += 1
    cerrar_carga(con, cid, n, ["Conjunto ficticio de demostración"])
    return {"unidades": len(ids), "datos": n}


def inicializar(demo: bool = True, reiniciar: bool = False):
    from .db import conexion, migrar
    migrar(reiniciar)
    with conexion() as con:
        r = {"normativa": cargar_normativa(con)}
        if not con.execute("SELECT 1 FROM unidad_territorial WHERE tipo='municipio' AND NOT es_demostracion LIMIT 1").fetchone():
            r["divipola"] = cargar_divipola_semilla(con)
        if demo:
            r["demo"] = cargar_demo(con)
    return r


if __name__ == "__main__":
    import sys
    print(inicializar(demo="--sin-demo" not in sys.argv, reiniciar="--reiniciar" in sys.argv))
