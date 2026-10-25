#!/usr/bin/env python3
"""Importación verificable de fuentes oficiales (DANE / datos.gov.co).

Cada importación registra la fuente, la versión, el archivo y su SHA-256, y
conserva las versiones anteriores de las series. Ejemplos:

  # DIVIPOLA desde el API SODA de datos.gov.co (requiere internet; confirme el id del conjunto en el portal)
  python scripts/actualizar_fuentes.py divipola --socrata <id-conjunto> --version 2026-10

  # DIVIPOLA o centros poblados desde un archivo descargado del DANE
  python scripts/actualizar_fuentes.py divipola --archivo DIVIPOLA_CentrosPoblados.xlsx --version 2026-10

  # Proyecciones municipales por área (fecha de actualización publicada por el DANE)
  python scripts/actualizar_fuentes.py proyecciones --archivo PPED-AreaMun-2018-2042.xlsx --version 2025-08-08 \
         --url https://www.dane.gov.co/index.php/estadisticas-por-tema/demografia-y-poblacion/proyecciones-de-poblacion

  # Resultados censales por municipio y área
  python scripts/actualizar_fuentes.py censo --archivo CNPV2018_municipios.xlsx --anio 2018 --serie CNPV2018 \
         --producto "CNPV 2018 — población censada por municipio y área"

  # Cartografía (MGN municipios / veredas) en shapefile comprimido
  python scripts/actualizar_fuentes.py capa --archivo MGN_MPIO_POLITICO.zip --tipo municipio \
         --campo-codigo MPIO_CDPMP --campo-nombre MPIO_CNMBR --producto "MGN 2023 — municipios" --version 2023
  python scripts/actualizar_fuentes.py capa --archivo CRVeredas.zip --tipo vereda --campo-codigo CODIGO_VER \
         --campo-nombre NOMBRE_VER --campo-padre DPTOMPIO --producto "Veredas de Colombia" --version 2020
  # (verifique los nombres de campo en el diccionario de datos de la versión descargada)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import conexion, migrar  # noqa: E402
from app.importers import divipola, geo, poblacion  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("divipola")
    d.add_argument("--archivo")
    d.add_argument("--socrata")
    d.add_argument("--version", required=True)
    d.add_argument("--url")
    p = sub.add_parser("proyecciones")
    p.add_argument("--archivo", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--url")
    p.add_argument("--hoja")
    c = sub.add_parser("censo")
    c.add_argument("--archivo", required=True)
    c.add_argument("--anio", type=int, required=True)
    c.add_argument("--serie", required=True)
    c.add_argument("--producto", required=True)
    c.add_argument("--entidad", default="DANE")
    c.add_argument("--clasificacion", default="censo_oficial_dane")
    c.add_argument("--version")
    c.add_argument("--url")
    c.add_argument("--metodologia", default="")
    c.add_argument("--hoja")
    g = sub.add_parser("capa")
    g.add_argument("--archivo", required=True)
    g.add_argument("--tipo", required=True)
    g.add_argument("--campo-codigo", required=True)
    g.add_argument("--campo-nombre", required=True)
    g.add_argument("--campo-padre")
    g.add_argument("--entidad", default="DANE")
    g.add_argument("--producto", required=True)
    g.add_argument("--version")
    g.add_argument("--srid", type=int)
    g.add_argument("--relacionar-con", help="tipo de unidad con la que calcular intersecciones (p. ej. sector_rural)")
    a = ap.parse_args()

    migrar()
    with conexion("importador") as con:
        if a.cmd == "divipola":
            if a.socrata:
                data, nombre = divipola.descargar_socrata(a.socrata), f"{a.socrata}.json"
                url = a.url or f"https://www.datos.gov.co/resource/{a.socrata}.json"
            else:
                data, nombre, url = Path(a.archivo).read_bytes(), Path(a.archivo).name, a.url
            r = divipola.importar_archivo(con, data, nombre, a.version, url)
        elif a.cmd == "proyecciones":
            f = Path(a.archivo)
            r = poblacion.importar_proyecciones(con, f.read_bytes(), f.name, a.version, a.url, hoja=a.hoja)
        elif a.cmd == "censo":
            f = Path(a.archivo)
            r = poblacion.importar_tabla_poblacion(con, f.read_bytes(), f.name, a.anio, a.serie, a.clasificacion, a.entidad,
                                                   a.producto, a.version, a.url, a.metodologia, hoja=a.hoja)
        else:
            f = Path(a.archivo)
            gj = geo.a_geojson(f.read_bytes(), f.name)
            r = geo.cargar_capa(con, gj, a.tipo, a.campo_codigo, a.campo_nombre, a.campo_padre,
                                {"entidad": a.entidad, "producto": a.producto, "version": a.version}, a.srid,
                                "MGN" if a.entidad == "DANE" else "LOCAL")
            if a.relacionar_con:
                mpios = {x["cod_municipio"] for x in con.execute(
                    "SELECT DISTINCT cod_municipio FROM unidad_territorial WHERE tipo=%s AND cod_municipio IS NOT NULL",
                    (a.tipo,)).fetchall()}
                r["relaciones"] = sum(geo.relacionar_por_geometria(con, a.tipo, a.relacionar_con, m) for m in mpios)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
