"""API REST de la plataforma (FastAPI)."""
from __future__ import annotations

import json
import os
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import servicio, validaciones
from .calc import estimacion
from .db import RAIZ, conexion
from .importers import divipola, geo, poblacion
from .reports import memoria

app = FastAPI(title="Plataforma demográfica y de caudales para acueductos — Colombia", version=servicio.VERSION_MOTOR)
TOKEN_ADMIN = os.environ.get("TOKEN_ADMIN")   # protege las rutas de importación si está definido


def _admin(token: str | None):
    if TOKEN_ADMIN and token != TOKEN_ADMIN:
        raise HTTPException(403, "Token de administración inválido")


def _err(e: Exception):
    raise HTTPException(422, str(e))


# ------------------------------------------------------------------ territorio

@app.get("/api/departamentos")
def departamentos():
    with conexion() as con:
        return con.execute("""SELECT id, codigo, nombre, clasificacion, es_demostracion FROM unidad_territorial
                              WHERE tipo='departamento' ORDER BY es_demostracion, nombre""").fetchall()


@app.get("/api/municipios")
def municipios(dpto: str):
    with conexion() as con:
        return con.execute("""SELECT id, codigo, nombre, clasificacion, es_demostracion, altitud_msnm::float
                              FROM unidad_territorial WHERE tipo='municipio' AND cod_departamento=%s ORDER BY nombre""",
                           (dpto,)).fetchall()


@app.get("/api/buscar")
def buscar(q: str = Query(min_length=2), municipio: str | None = None, tipo: str | None = None, limite: int = 30):
    """Búsqueda por nombre (sin tildes, por similitud) o por código DANE/DIVIPOLA."""
    with conexion() as con:
        return con.execute(
            """SELECT u.id, u.tipo, u.codigo, u.nombre, u.cod_municipio, m.nombre municipio, d.nombre departamento,
                      u.clasificacion, u.es_demostracion
               FROM unidad_territorial u
               LEFT JOIN unidad_territorial m ON m.codigo = u.cod_municipio AND m.tipo='municipio'
               LEFT JOIN unidad_territorial d ON d.codigo = u.cod_departamento AND d.tipo='departamento'
               WHERE (u.codigo LIKE %(q)s || '%%' OR u.nombre_normalizado LIKE '%%' || lower(f_unaccent(%(q)s)) || '%%'
                      OR u.nombre_normalizado %% lower(f_unaccent(%(q)s)))
                 AND (%(m)s::text IS NULL OR u.cod_municipio = %(m)s)
                 AND (%(t)s::text IS NULL OR u.tipo::text = %(t)s)
                 AND u.tipo NOT IN ('pais')
               ORDER BY (u.codigo = %(q)s) DESC, similarity(u.nombre_normalizado, lower(f_unaccent(%(q)s))) DESC
               LIMIT %(l)s""", {"q": q.strip(), "m": municipio, "t": tipo, "l": limite}).fetchall()


@app.get("/api/unidades/{uid}")
def get_unidad(uid: int):
    with conexion() as con:
        u = servicio.unidad(con, uid)
        if not u:
            raise HTTPException(404, "No existe")
        u["hijos"] = con.execute("""SELECT id, tipo, codigo, nombre, clasificacion, es_demostracion FROM unidad_territorial
                                    WHERE padre_id=%s ORDER BY tipo, nombre""", (uid,)).fetchall()
        u["relaciones"] = con.execute("""SELECT r.tipo_relacion, r.fraccion_area::float, r.fraccion_viviendas::float, r.metodo,
                                                d.id, d.tipo, d.codigo, d.nombre
                                         FROM relacion_territorial r JOIN unidad_territorial d ON d.id=r.unidad_destino
                                         WHERE r.unidad_origen=%s""", (uid,)).fetchall()
        return u


@app.get("/api/unidades/{uid}/demografia")
def get_demografia(uid: int):
    with conexion() as con:
        if not servicio.unidad(con, uid):
            raise HTTPException(404, "No existe")
        d = servicio.demografia(con, uid)
        d["etiquetas"] = servicio.ETIQUETA_CLASIF
        return d


class DatoUsuario(BaseModel):
    anio: int
    variable: Literal["poblacion", "hombres", "mujeres", "viviendas", "viviendas_ocupadas", "hogares"]
    valor: float
    area: Literal["total", "cabecera", "centros_poblados_rural_disperso"] = "total"
    clasificacion: Literal["informacion_campo", "oficial_otra_entidad", "pendiente_verificacion"] = "informacion_campo"
    entidad: str = "Usuario"
    producto: str = "Información de campo"
    soporte: str
    metodologia: str = ""
    usuario: str = "anonimo"


@app.post("/api/unidades/{uid}/datos")
def agregar_dato(uid: int, d: DatoUsuario):
    """Información de campo u oficial de otra entidad. Nunca se acepta como 'censo_oficial_dane' ni 'estimacion_tecnica'."""
    if not d.soporte.strip():
        raise HTTPException(422, "Toda información incorporada por el usuario requiere soporte documental.")
    with conexion(d.usuario) as con:
        from .importers.comun import registrar_fuente
        fid = registrar_fuente(con, d.entidad, d.producto, None)
        serie = "CAMPO" if d.clasificacion == "informacion_campo" else "OTRA_ENTIDAD"
        con.execute("""UPDATE dato_demografico SET vigente=false WHERE unidad_id=%s AND anio=%s AND area=%s AND variable=%s
                       AND serie=%s AND vigente""", (uid, d.anio, d.area, d.variable, serie))
        r = con.execute("""INSERT INTO dato_demografico (unidad_id, anio, area, variable, valor, clasificacion, fuente_id,
                              serie, metodologia, soporte, usuario) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (uid, d.anio, d.area, d.variable, d.valor, d.clasificacion, fid, serie, d.metodologia, d.soporte,
                         d.usuario)).fetchone()
        return r


class NuevaUnidad(BaseModel):
    tipo: Literal["vereda", "corregimiento", "centro_poblado", "caserio", "sector_rural", "otro"]
    nombre: str
    codigo: str | None = None
    padre_id: int
    geometria: dict | None = None
    altitud_msnm: float | None = None
    fuente: str = "Delimitación del usuario"


@app.post("/api/unidades")
def crear_unidad(u: NuevaUnidad):
    with conexion() as con:
        p = con.execute("SELECT cod_departamento, cod_municipio, es_demostracion FROM unidad_territorial WHERE id=%s",
                        (u.padre_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Unidad padre no existe")
        from .importers.comun import registrar_fuente
        fid = registrar_fuente(con, "Usuario", u.fuente, None)
        g = json.dumps(u.geometria) if u.geometria else None
        return con.execute(
            """INSERT INTO unidad_territorial (tipo, codigo, sistema_codigo, nombre, padre_id, cod_departamento, cod_municipio,
                   clase, fuente_id, clasificacion, altitud_msnm, es_demostracion, geom, centroide)
               VALUES (%s, coalesce(%s, 'LOC-' || nextval('unidad_territorial_id_seq')), 'LOCAL', %s, %s, %s, %s, 'rural', %s,
                       'informacion_campo', %s, %s,
                       CASE WHEN %s::text IS NOT NULL THEN ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)),3)) END,
                       CASE WHEN %s::text IS NOT NULL THEN ST_PointOnSurface(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) END)
               RETURNING id""",
            (u.tipo, u.codigo, u.nombre, u.padre_id, p["cod_departamento"], p["cod_municipio"], fid, u.altitud_msnm,
             p["es_demostracion"], g, g, g, g)).fetchone()


# ------------------------------------------------------------------ cálculo

@app.get("/api/normativa")
def get_normativa():
    with conexion() as con:
        return servicio.parametros(con)


@app.post("/api/calcular")
def calcular(req: servicio.CalculoReq):
    try:
        with conexion() as con:
            return servicio.calcular(con, req)
    except ValueError as e:
        _err(e)


class EstimacionReq(BaseModel):
    metodo: Literal["prorrateo", "viviendas", "hogares"]
    partes: list[dict] = []
    viviendas_ocupadas: float | None = None
    ppv: float | None = None
    fuente_viviendas: str = ""
    fuente_ppv: str = ""
    hogares: float | None = None
    pph: float | None = None
    fuente_hogares: str = ""
    fuente_pph: str = ""
    unidad_id: int | None = None
    anio: int | None = None
    guardar: bool = False


@app.post("/api/estimacion")
def estimar(r: EstimacionReq):
    try:
        if r.metodo == "prorrateo":
            res = estimacion.prorrateo_unidades_censales(r.partes)
        elif r.metodo == "viviendas":
            res = estimacion.viviendas_por_ppv(r.viviendas_ocupadas, r.ppv, r.fuente_viviendas, r.fuente_ppv)
        else:
            res = estimacion.hogares_por_pph(r.hogares, r.pph, r.fuente_hogares, r.fuente_pph)
    except (ValueError, TypeError) as e:
        _err(e)
    if r.guardar and r.unidad_id and r.anio:
        with conexion() as con:
            from .importers.comun import registrar_fuente
            fid = registrar_fuente(con, "Aplicación", "Estimación técnica de población", servicio.VERSION_MOTOR)
            con.execute("""INSERT INTO dato_demografico (unidad_id, anio, variable, valor, clasificacion, fuente_id, serie, metodologia)
                           VALUES (%s,%s,'poblacion',%s,'estimacion_tecnica',%s,%s,%s)
                           ON CONFLICT (unidad_id, anio, area, variable, serie) WHERE vigente DO UPDATE SET valor=EXCLUDED.valor,
                               metodologia=EXCLUDED.metodologia""",
                        (r.unidad_id, r.anio, res["poblacion"], fid, f"EST_{r.metodo.upper()}", res["metodologia"]))
        res["guardado"] = True
    return res


@app.get("/api/validaciones")
def get_validaciones(municipio: str | None = None):
    with conexion() as con:
        return validaciones.ejecutar(con, municipio)


@app.get("/api/fuentes")
def fuentes():
    with conexion() as con:
        return con.execute("""SELECT f.*, (SELECT count(*) FROM carga_datos c WHERE c.fuente_id=f.id) cargas
                              FROM fuente f ORDER BY id""").fetchall()


@app.get("/api/fuentes_oficiales")
def fuentes_oficiales():
    return json.loads((RAIZ / "data" / "seed" / "fuentes_oficiales.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ memoria

@app.post("/api/memoria/{formato}")
def exportar(formato: Literal["xlsx", "docx", "pdf"], req: servicio.CalculoReq):
    try:
        with conexion() as con:
            res = servicio.calcular(con, req)
            servicio.guardar_calculo(con, req, res)
    except ValueError as e:
        _err(e)
    contenido, mime = memoria.generar(formato, res)
    nombre = "".join(c if c.isalnum() else "_" for c in req.proyecto.nombre)[:60] or "memoria"
    return Response(contenido, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="memoria_{nombre}.{formato}"'})


# ------------------------------------------------------------------ proyectos

class ProyectoGuardar(BaseModel):
    calculo: servicio.CalculoReq
    id: int | None = None


@app.post("/api/proyectos")
def guardar_proyecto(p: ProyectoGuardar):
    c = p.calculo
    with conexion() as con:
        if p.id:
            con.execute("""UPDATE proyecto SET nombre=%s, entidad=%s, tipo=%s, descripcion=%s, anio_base=%s, periodo_diseno=%s,
                           parametros=%s, actualizado_en=now() WHERE id=%s""",
                        (c.proyecto.nombre, c.proyecto.entidad, c.proyecto.tipo, c.proyecto.descripcion, c.anio_inicial,
                         c.periodo_diseno, c.model_dump_json(), p.id))
            pid = p.id
        else:
            pid = con.execute("""INSERT INTO proyecto (nombre, entidad, tipo, descripcion, anio_base, periodo_diseno, parametros)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (c.proyecto.nombre, c.proyecto.entidad, c.proyecto.tipo, c.proyecto.descripcion,
                               c.anio_inicial, c.periodo_diseno, c.model_dump_json())).fetchone()["id"]
        con.execute("DELETE FROM proyecto_localidad WHERE proyecto_id=%s", (pid,))
        for l in c.localidades:
            if l.unidad_id:
                con.execute("""INSERT INTO proyecto_localidad (proyecto_id, unidad_id, area, cobertura_pct, anio_ingreso,
                                  metodo_proyeccion, poblacion_base, poblacion_base_soporte, tasa_manual, justificacion)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (pid, l.unidad_id, l.area, l.cobertura_pct, l.anio_ingreso, l.metodo, l.poblacion_base,
                             l.poblacion_base_soporte, l.tasa_manual, l.justificacion))
        return {"id": pid}


@app.get("/api/proyectos")
def listar_proyectos():
    with conexion() as con:
        return con.execute("SELECT id, nombre, entidad, tipo, anio_base, periodo_diseno, actualizado_en FROM proyecto "
                           "ORDER BY actualizado_en DESC").fetchall()


@app.get("/api/proyectos/{pid}")
def get_proyecto(pid: int):
    with conexion() as con:
        p = con.execute("SELECT * FROM proyecto WHERE id=%s", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "No existe")
        p["historial"] = con.execute("SELECT accion, usuario, ts FROM auditoria WHERE tabla='proyecto' AND registro_id=%s "
                                     "ORDER BY ts DESC LIMIT 50", (str(pid),)).fetchall()
        return p


# ------------------------------------------------------------------ geografía

@app.get("/api/geo/unidades")
def geo_unidades(municipio: str | None = None, departamento: str | None = None, tipos: str = "municipio",
                 simplificar: float = 0.0005):
    tl = tipos.split(",")
    with conexion() as con:
        filas = con.execute(
            """SELECT u.id, u.tipo, u.codigo, u.nombre, u.es_demostracion, u.clasificacion,
                      ST_AsGeoJSON(ST_SimplifyPreserveTopology(coalesce(u.geom, u.centroide), %s), 6) g,
                      (SELECT valor::float FROM dato_demografico d WHERE d.unidad_id=u.id AND d.variable='poblacion'
                         AND d.area='total' AND d.vigente ORDER BY d.anio DESC, (d.clasificacion='censo_oficial_dane') DESC LIMIT 1) pob,
                      (SELECT anio FROM dato_demografico d WHERE d.unidad_id=u.id AND d.variable='poblacion'
                         AND d.area='total' AND d.vigente ORDER BY d.anio DESC LIMIT 1) pob_anio,
                      (SELECT clasificacion FROM dato_demografico d WHERE d.unidad_id=u.id AND d.variable='poblacion'
                         AND d.area='total' AND d.vigente ORDER BY d.anio DESC LIMIT 1) pob_clasif
               FROM unidad_territorial u
               WHERE u.tipo::text = ANY(%s) AND (u.geom IS NOT NULL OR u.centroide IS NOT NULL)
                 AND (%s::text IS NULL OR u.cod_municipio=%s) AND (%s::text IS NULL OR u.cod_departamento=%s)
               LIMIT 5000""", (simplificar, tl, municipio, municipio, departamento, departamento)).fetchall()
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(f.pop("g")), "properties": f} for f in filas]}


@app.post("/api/geo/leer")
async def geo_leer(archivo: UploadFile = File(...)):
    """Convierte GeoJSON / KML / KMZ / Shapefile (.zip) a GeoJSON (no persiste)."""
    try:
        gj = geo.a_geojson(await archivo.read(), archivo.filename)
    except (ValueError, KeyError, StopIteration) as e:
        _err(e)
    if gj.pop("_proyectado", False):
        gj["advertencia"] = "Shapefile proyectado: la visualización requiere reproyectar a EPSG:4326 al cargarlo como capa."
    gj.pop("_prj", None)
    return gj


class AreaReq(BaseModel):
    geometria: dict
    tipos: list[str] = ["cabecera_municipal", "centro_poblado", "corregimiento", "inspeccion_policia", "caserio", "vereda",
                        "sector_rural", "seccion_rural"]


@app.post("/api/geo/localidades_en_area")
def localidades_en_area(a: AreaReq):
    """Unidades que intersectan el área de influencia dibujada, con la fracción de su área incluida."""
    g = json.dumps(a.geometria)
    with conexion() as con:
        return con.execute(
            """WITH a AS (SELECT ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) g)
               SELECT u.id, u.tipo, u.codigo, u.nombre, u.es_demostracion,
                      CASE WHEN u.geom IS NULL THEN NULL ELSE
                        round((ST_Area(ST_Intersection(u.geom, a.g)::geography)/nullif(ST_Area(u.geom::geography),0))::numeric, 4)::float END fraccion
               FROM unidad_territorial u, a
               WHERE u.tipo::text = ANY(%s) AND ST_Intersects(coalesce(u.geom, u.centroide), a.g)
               ORDER BY fraccion DESC NULLS LAST""", (g, a.tipos)).fetchall()


@app.post("/api/proyectos/{pid}/area")
async def subir_area(pid: int, nombre: str = Form("Área de influencia"), archivo: UploadFile | None = File(None),
                     geometria: str | None = Form(None)):
    if archivo:
        gj = geo.a_geojson(await archivo.read(), archivo.filename)
        origen = {"kml": "kml", "kmz": "kml", "zip": "shapefile"}.get(archivo.filename.rsplit(".", 1)[-1].lower(), "geojson")
        geoms = [f["geometry"] for f in gj["features"]]
    elif geometria:
        geoms, origen = [json.loads(geometria)], "dibujo"
    else:
        raise HTTPException(422, "Envíe un archivo o una geometría")
    with conexion() as con:
        r = con.execute("""INSERT INTO area_influencia (proyecto_id, nombre, origen, geom)
                           SELECT %s, %s, %s, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Union(ST_SetSRID(ST_GeomFromGeoJSON(x),4326))),3))
                           FROM unnest(%s::text[]) x RETURNING id""",
                        (pid, nombre, origen, [json.dumps(g) for g in geoms])).fetchone()
        return r


# ------------------------------------------------------------------ importación (administración)

@app.post("/api/importar/divipola")
async def imp_divipola(archivo: UploadFile = File(...), version: str = Form(...), url: str = Form(""),
                       token: str | None = Form(None)):
    _admin(token)
    data = await archivo.read()
    with conexion() as con:
        return divipola.importar_archivo(con, data, archivo.filename, version, url or None)


@app.post("/api/importar/proyecciones")
async def imp_proy(archivo: UploadFile = File(...), version: str = Form(...), url: str = Form(""), hoja: str = Form(""),
                   token: str | None = Form(None)):
    _admin(token)
    data = await archivo.read()
    try:
        with conexion() as con:
            return poblacion.importar_proyecciones(con, data, archivo.filename, version, url or None, hoja=hoja or None)
    except ValueError as e:
        _err(e)


@app.post("/api/importar/censo")
async def imp_censo(archivo: UploadFile = File(...), anio: int = Form(...), serie: str = Form(...),
                    producto: str = Form(...), clasificacion: str = Form("censo_oficial_dane"), entidad: str = Form("DANE"),
                    version: str = Form(""), url: str = Form(""), metodologia: str = Form(""), hoja: str = Form(""),
                    token: str | None = Form(None)):
    _admin(token)
    data = await archivo.read()
    try:
        with conexion() as con:
            return poblacion.importar_tabla_poblacion(con, data, archivo.filename, anio, serie, clasificacion, entidad,
                                                      producto, version or None, url or None, metodologia, hoja=hoja or None)
    except ValueError as e:
        _err(e)


@app.post("/api/importar/capa")
async def imp_capa(archivo: UploadFile = File(...), tipo: str = Form(...), campo_codigo: str = Form(...),
                   campo_nombre: str = Form(...), campo_padre: str = Form(""), entidad: str = Form("DANE"),
                   producto: str = Form(...), version: str = Form(""), srid: int | None = Form(None),
                   token: str | None = Form(None)):
    _admin(token)
    gj = geo.a_geojson(await archivo.read(), archivo.filename)
    try:
        with conexion() as con:
            return geo.cargar_capa(con, gj, tipo, campo_codigo, campo_nombre, campo_padre or None,
                                   {"entidad": entidad, "producto": producto, "version": version or None}, srid,
                                   "MGN" if entidad == "DANE" else "LOCAL")
    except ValueError as e:
        _err(e)


# ------------------------------------------------------------------ frontend

FRONT = RAIZ / "frontend"
app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")
