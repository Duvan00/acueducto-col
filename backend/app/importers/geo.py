"""Lectura de archivos geográficos (GeoJSON, KML, Shapefile .zip) a GeoJSON
y carga de cartografía (MGN del DANE, capa de veredas) en PostGIS."""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile

import shapefile  # pyshp

from .comun import cerrar_carga, codigo, iniciar_carga, registrar_fuente


def a_geojson(data: bytes, nombre: str) -> dict:
    n = nombre.lower()
    if n.endswith((".geojson", ".json")):
        gj = json.loads(data)
        if gj.get("type") == "Feature":
            gj = {"type": "FeatureCollection", "features": [gj]}
        elif gj.get("type") != "FeatureCollection":
            gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": gj}]}
        return gj
    if n.endswith(".kml"):
        return _kml(data)
    if n.endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml = next(f for f in z.namelist() if f.lower().endswith(".kml"))
            return _kml(z.read(kml))
    if n.endswith(".zip"):
        return _shp_zip(data)
    raise ValueError("Formato no soportado: use .geojson, .json, .kml, .kmz o .zip (shapefile)")


def _coords(txt: str):
    out = []
    for t in txt.split():
        p = t.split(",")
        out.append([float(p[0]), float(p[1])])
    return out


def _kml(data: bytes) -> dict:
    root = ET.fromstring(data)
    ns = {"k": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {"k": ""}
    q = (lambda t: f"{{{ns['k']}}}{t}") if ns["k"] else (lambda t: t)
    feats = []
    for pm in root.iter(q("Placemark")):
        nombre = pm.findtext(q("name")) or ""
        polys = []
        for pol in pm.iter(q("Polygon")):
            anillos = []
            ob = pol.find(f"{q('outerBoundaryIs')}/{q('LinearRing')}/{q('coordinates')}")
            if ob is None:
                continue
            anillos.append(_coords(ob.text))
            for ib in pol.findall(f"{q('innerBoundaryIs')}/{q('LinearRing')}/{q('coordinates')}"):
                anillos.append(_coords(ib.text))
            polys.append(anillos)
        if polys:
            geom = {"type": "MultiPolygon", "coordinates": polys}
        else:
            pt = pm.find(f".//{q('Point')}/{q('coordinates')}")
            if pt is None:
                continue
            geom = {"type": "Point", "coordinates": _coords(pt.text)[0]}
        feats.append({"type": "Feature", "properties": {"nombre": nombre}, "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def _shp_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        nombres = z.namelist()
        base = next((f[:-4] for f in nombres if f.lower().endswith(".shp")), None)
        if not base:
            raise ValueError("El .zip no contiene un .shp")
        get = lambda ext: next((z.read(f) for f in nombres if f.lower() == (base + ext).lower()), None)
        prj = (get(".prj") or b"").decode(errors="ignore")
        r = shapefile.Reader(shp=io.BytesIO(get(".shp")), dbf=io.BytesIO(get(".dbf")),
                             shx=io.BytesIO(get(".shx")) if get(".shx") else None, encoding="utf-8",
                             encodingErrors="replace")
        gj = r.__geo_interface__
        gj["_prj"] = prj
        gj["_proyectado"] = prj.strip().upper().startswith("PROJCS")
        return gj


def srid_sugerido(gj: dict) -> int:
    """4326/4686 para coordenadas geográficas; si el .prj es proyectado se exige indicar el SRID
    (p. ej. 9377 MAGNA-SIRGAS Origen Nacional, 3116 MAGNA Bogotá)."""
    prj = gj.get("_prj", "")
    if gj.get("_proyectado"):
        if "Origen-Nacional" in prj or "Origen_Nacional" in prj:
            return 9377
        raise ValueError("El shapefile está en un sistema proyectado: indique el código EPSG (srid).")
    return 4326


def cargar_capa(con, gj: dict, tipo: str, campo_codigo: str, campo_nombre: str, campo_padre: str | None,
                fuente: dict, srid: int | None = None, sistema_codigo: str = "MGN",
                actualizar_existentes: bool = True) -> dict:
    """Carga polígonos como unidades territoriales. Si ya existe la unidad (p. ej. municipio de la DIVIPOLA)
    sólo se le asigna la geometría."""
    srid = srid or srid_sugerido(gj)
    fuente_id = registrar_fuente(con, **fuente)
    carga_id = iniciar_carga(con, fuente_id, fuente.get("producto"), None, f"capa_{tipo} v1")
    n, adv = 0, []
    for f in gj["features"]:
        p = f.get("properties") or {}
        cod = str(p.get(campo_codigo) or "").strip()
        nom = str(p.get(campo_nombre) or cod).strip()
        if not cod:
            adv.append("Elemento sin código omitido")
            continue
        if tipo == "municipio":
            cod = codigo(cod, 5)
        elif tipo == "departamento":
            cod = codigo(cod, 2)
        geom = json.dumps(f["geometry"])
        g_sql = "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), %s), 4326)), 3))"
        existe = con.execute("SELECT id FROM unidad_territorial WHERE codigo=%s AND tipo=%s", (cod, tipo)).fetchone()
        if existe and actualizar_existentes:
            con.execute(f"UPDATE unidad_territorial SET geom={g_sql}, geom_fuente_id=%s, centroide=coalesce(centroide, "
                        f"ST_PointOnSurface({g_sql})), actualizado_en=now() WHERE id=%s",
                        (geom, srid, fuente_id, geom, srid, existe["id"]))
        else:
            padre = None
            if campo_padre and p.get(campo_padre):
                pc = codigo(p.get(campo_padre), 5)
                r = con.execute("SELECT id FROM unidad_territorial WHERE codigo=%s AND tipo='municipio'", (pc,)).fetchone()
                padre = r["id"] if r else None
                if not padre:
                    adv.append(f"{cod}: municipio {pc} no encontrado")
            mp = con.execute("SELECT cod_departamento, cod_municipio FROM unidad_territorial WHERE id=%s",
                             (padre,)).fetchone() if padre else None
            con.execute(
                f"""INSERT INTO unidad_territorial (tipo, codigo, sistema_codigo, nombre, padre_id, cod_departamento,
                        cod_municipio, clase, fuente_id, carga_id, clasificacion, geom, geom_fuente_id, centroide, atributos)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'oficial_otra_entidad',{g_sql},%s,ST_PointOnSurface({g_sql}),%s)
                    ON CONFLICT (sistema_codigo, codigo, tipo) DO UPDATE SET geom=EXCLUDED.geom, nombre=EXCLUDED.nombre""",
                (tipo, cod, sistema_codigo, nom, padre, mp and mp["cod_departamento"], mp and mp["cod_municipio"],
                 "rural" if tipo in ("vereda", "sector_rural", "seccion_rural") else None, fuente_id, carga_id,
                 geom, srid, fuente_id, geom, srid, json.dumps({k: v for k, v in p.items() if isinstance(v, (str, int, float))},
                                                               ensure_ascii=False)))
        n += 1
    cerrar_carga(con, carga_id, n, adv)
    return {"carga_id": carga_id, "registros": n, "advertencias": adv}


def relacionar_por_geometria(con, tipo_origen: str, tipo_destino: str, cod_municipio: str) -> int:
    """Calcula relaciones de intersección (p. ej. vereda ↔ sector rural) con fracción de área.
    La fracción de viviendas se completa cuando hay manzanas/viviendas georreferenciadas o conteo de campo."""
    r = con.execute(
        """INSERT INTO relacion_territorial (unidad_origen, unidad_destino, tipo_relacion, fraccion_area, metodo, clasificacion)
           SELECT o.id, d.id, CASE WHEN ST_Covers(o.geom, d.geom) THEN 'contiene' ELSE 'intersecta' END,
                  least(1, ST_Area(ST_Intersection(o.geom, d.geom)::geography) / nullif(ST_Area(d.geom::geography), 0)),
                  'Intersección geométrica PostGIS', 'estimacion_tecnica'
           FROM unidad_territorial o JOIN unidad_territorial d
             ON o.cod_municipio = d.cod_municipio AND ST_Intersects(o.geom, d.geom) AND NOT ST_Touches(o.geom, d.geom)
           WHERE o.tipo=%s AND d.tipo=%s AND o.cod_municipio=%s
           ON CONFLICT (unidad_origen, unidad_destino, tipo_relacion) DO UPDATE SET fraccion_area=EXCLUDED.fraccion_area""",
        (tipo_origen, tipo_destino, cod_municipio))
    return r.rowcount
