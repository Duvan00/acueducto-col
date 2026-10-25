-- =====================================================================
-- Plataforma de información demográfica y caudales de acueducto (Colombia)
-- Esquema relacional PostgreSQL 14+ / PostGIS 3+
--
-- Principios:
--  * Ningún valor demográfico existe sin fuente, año, unidad territorial,
--    metodología y clasificación (oficial / estimado / campo / pendiente).
--  * Las series oficiales se versionan: una nueva publicación del DANE
--    no borra la anterior; la marca como no vigente.
--  * Las veredas NO se asumen equivalentes a unidades censales: la
--    relación entre unidades se modela explícitamente (relacion_territorial).
--  * Toda modificación queda en la tabla auditoria (triggers).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent() no es IMMUTABLE; este envoltorio permite indexar nombres sin tildes.
CREATE OR REPLACE FUNCTION public.f_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
$$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

-- ---------------------------------------------------------------------
-- Catálogos
-- ---------------------------------------------------------------------

-- Clasificación de confiabilidad exigida por el requisito 9.
CREATE TYPE clasificacion_dato AS ENUM (
    'censo_oficial_dane',          -- Dato censal oficial del DANE
    'proyeccion_oficial_dane',     -- Proyección / retroproyección oficial del DANE
    'oficial_otra_entidad',        -- IGAC, gobernación, SISBEN, SUI, etc.
    'estimacion_tecnica',          -- Calculado por la aplicación (NUNCA oficial)
    'informacion_campo',           -- Censo local, encuesta, conteo de viviendas del usuario
    'pendiente_verificacion'       -- Transcrito o sin soporte documental verificado
);

CREATE TYPE tipo_unidad AS ENUM (
    'pais', 'departamento', 'municipio',
    'cabecera_municipal',
    'corregimiento', 'inspeccion_policia', 'centro_poblado', 'caserio',
    'vereda',
    'area_rural_dispersa',          -- resto rural del municipio
    'seccion_rural', 'sector_rural', 'seccion_urbana', 'sector_urbano', 'manzana',
    'otro'
);

-- Área geográfica tal como la publica el DANE.
CREATE TYPE area_geografica AS ENUM (
    'total',
    'cabecera',                     -- Cabecera municipal
    'centros_poblados_rural_disperso',
    'no_aplica'
);

CREATE TABLE fuente (
    id               serial PRIMARY KEY,
    entidad          text NOT NULL,                -- DANE, IGAC, MinVivienda, usuario...
    producto         text NOT NULL,                -- p. ej. 'CNPV 2018', 'Proyecciones municipales por área 2018-2042'
    version          text,                         -- versión / fecha de actualización publicada
    url              text,
    fecha_publicacion date,
    fecha_consulta   date,
    licencia         text,
    nivel_desagregacion text,                      -- departamento, municipio, área, sector, manzana...
    notas            text,
    creado_en        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entidad, producto, version)
);

-- Cada importación es un lote trazable (archivo, hash, quién, cuándo).
CREATE TABLE carga_datos (
    id               serial PRIMARY KEY,
    fuente_id        int NOT NULL REFERENCES fuente(id),
    archivo          text,
    sha256           char(64),
    importador       text NOT NULL,                -- nombre/versión del procedimiento
    registros        int DEFAULT 0,
    advertencias     jsonb NOT NULL DEFAULT '[]',
    estado           text NOT NULL DEFAULT 'completada' CHECK (estado IN ('en_proceso','completada','fallida','revertida')),
    usuario          text NOT NULL DEFAULT current_user,
    creado_en        timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Territorio
-- ---------------------------------------------------------------------

CREATE TABLE unidad_territorial (
    id               bigserial PRIMARY KEY,
    tipo             tipo_unidad NOT NULL,
    codigo           text,                         -- DIVIPOLA (dpto 2, mpio 5, CP 8) o código MGN / vereda
    sistema_codigo   text NOT NULL DEFAULT 'DIVIPOLA', -- DIVIPOLA, MGN, VEREDAS_DANE, LOCAL
    nombre           text NOT NULL,
    nombre_normalizado text GENERATED ALWAYS AS (lower(public.f_unaccent(nombre))) STORED,
    padre_id         bigint REFERENCES unidad_territorial(id),
    cod_departamento char(2),
    cod_municipio    char(5),
    clase            text CHECK (clase IN ('urbano','rural','mixto')),
    categoria_divipola text,                       -- CM, C, IP, IPM, CAS, CP... tal como viene en DIVIPOLA
    vigente_desde    date,
    vigente_hasta    date,                         -- p. ej. municipios segregados / supresiones
    es_demostracion  boolean NOT NULL DEFAULT false,
    fuente_id        int REFERENCES fuente(id),
    carga_id         int REFERENCES carga_datos(id),
    clasificacion    clasificacion_dato NOT NULL DEFAULT 'pendiente_verificacion',
    altitud_msnm     numeric,                      -- altitud representativa (para dotación, art. 43)
    altitud_fuente   text,
    centroide        geometry(Point, 4326),
    geom             geometry(MultiPolygon, 4326),
    geom_fuente_id   int REFERENCES fuente(id),
    area_km2         numeric GENERATED ALWAYS AS (ST_Area(geom::geography) / 1e6) STORED,
    atributos        jsonb NOT NULL DEFAULT '{}',
    creado_en        timestamptz NOT NULL DEFAULT now(),
    actualizado_en   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (sistema_codigo, codigo, tipo)
);
CREATE INDEX ix_ut_padre ON unidad_territorial(padre_id);
CREATE INDEX ix_ut_mpio ON unidad_territorial(cod_municipio);
CREATE INDEX ix_ut_tipo ON unidad_territorial(tipo);
CREATE INDEX ix_ut_nombre_trgm ON unidad_territorial USING gin (nombre_normalizado gin_trgm_ops);
CREATE INDEX ix_ut_geom ON unidad_territorial USING gist (geom);
CREATE INDEX ix_ut_centroide ON unidad_territorial USING gist (centroide);

-- Relaciones no jerárquicas: vereda ↔ sector rural, vereda ↔ centro poblado,
-- municipio segregado de otro, etc. Las fracciones permiten prorratear sin
-- suponer coincidencia de límites.
CREATE TABLE relacion_territorial (
    id               bigserial PRIMARY KEY,
    unidad_origen    bigint NOT NULL REFERENCES unidad_territorial(id) ON DELETE CASCADE,
    unidad_destino   bigint NOT NULL REFERENCES unidad_territorial(id) ON DELETE CASCADE,
    tipo_relacion    text NOT NULL CHECK (tipo_relacion IN
                     ('contiene','intersecta','equivalente','segregado_de','agregado_a','superpuesto')),
    fraccion_area    numeric CHECK (fraccion_area BETWEEN 0 AND 1),      -- área(origen∩destino)/área(destino)
    fraccion_viviendas numeric CHECK (fraccion_viviendas BETWEEN 0 AND 1),
    anio_evento      int,
    metodo           text,
    fuente_id        int REFERENCES fuente(id),
    clasificacion    clasificacion_dato NOT NULL,
    notas            text,
    UNIQUE (unidad_origen, unidad_destino, tipo_relacion)
);

-- ---------------------------------------------------------------------
-- Datos demográficos (series versionadas)
-- ---------------------------------------------------------------------

CREATE TABLE dato_demografico (
    id               bigserial PRIMARY KEY,
    unidad_id        bigint NOT NULL REFERENCES unidad_territorial(id) ON DELETE CASCADE,
    anio             int NOT NULL CHECK (anio BETWEEN 1900 AND 2150),
    area             area_geografica NOT NULL DEFAULT 'total',
    variable         text NOT NULL CHECK (variable IN (
                        'poblacion','hombres','mujeres','viviendas','viviendas_ocupadas',
                        'hogares','personas_por_vivienda','personas_por_hogar')),
    valor            numeric NOT NULL CHECK (valor >= 0),
    clasificacion    clasificacion_dato NOT NULL,
    fuente_id        int NOT NULL REFERENCES fuente(id),
    carga_id         int REFERENCES carga_datos(id),
    serie            text NOT NULL,                -- 'CNPV2018', 'CENSO2005_CONCILIADO', 'PROY_MUN_AREA_2018_2042_v2025-08'...
    metodologia      text,                         -- descripción (o referencia) de cómo se obtuvo
    vigente          boolean NOT NULL DEFAULT true,
    reemplaza_id     bigint REFERENCES dato_demografico(id),
    usuario          text NOT NULL DEFAULT current_user,
    soporte          text,                         -- documento / acta / archivo de campo
    creado_en        timestamptz NOT NULL DEFAULT now()
);
-- Solo un valor vigente por unidad/año/área/variable/serie.
CREATE UNIQUE INDEX ux_dato_vigente ON dato_demografico (unidad_id, anio, area, variable, serie) WHERE vigente;
CREATE INDEX ix_dato_unidad ON dato_demografico (unidad_id, variable, anio);

-- Una estimación técnica jamás puede quedar marcada como oficial: la serie
-- de estimaciones de la aplicación debe empezar por 'EST_' y las oficiales no.
ALTER TABLE dato_demografico ADD CONSTRAINT ck_estimacion_no_oficial CHECK (
    (clasificacion = 'estimacion_tecnica') = (serie LIKE 'EST\_%')
);

-- ---------------------------------------------------------------------
-- Normativa parametrizada (sin valores "quemados" en el código)
-- ---------------------------------------------------------------------

CREATE TABLE parametro_normativo (
    id               serial PRIMARY KEY,
    clave            text NOT NULL,                -- 'dotacion_neta_max', 'perdidas_max', 'k1_max', 'k2_max', 'periodo_diseno'
    valor            numeric NOT NULL,
    unidad           text,
    condicion        jsonb NOT NULL DEFAULT '{}',  -- {"altitud_min":2000} {"poblacion_max":12500} ...
    norma            text NOT NULL,                -- 'Resolución 0330 de 2017 (MVCT)'
    articulo         text,
    texto_referencia text,
    vigente_desde    date,
    vigente_hasta    date,
    -- verificado_texto_oficial: cotejado con el texto consolidado publicado por MinVivienda/CRA.
    -- verificado_fuente_secundaria: confirmado en fuentes que citan el texto oficial; requiere cotejo.
    estado_verificacion text NOT NULL DEFAULT 'pendiente'
                     CHECK (estado_verificacion IN ('verificado_texto_oficial','verificado_fuente_secundaria','pendiente','derogado')),
    referencia_verificacion text,
    nota             text
);

-- ---------------------------------------------------------------------
-- Proyectos, cálculos y memorias
-- ---------------------------------------------------------------------

CREATE TABLE proyecto (
    id               bigserial PRIMARY KEY,
    nombre           text NOT NULL,
    codigo           text,
    entidad          text,
    tipo             text NOT NULL DEFAULT 'urbano' CHECK (tipo IN ('urbano','rural','regional')),
    descripcion      text,
    anio_base        int NOT NULL,
    periodo_diseno   int NOT NULL,
    altitud_msnm     numeric,
    parametros       jsonb NOT NULL DEFAULT '{}',  -- dotación, pérdidas, K1, K2, justificaciones
    usuario          text NOT NULL DEFAULT current_user,
    creado_en        timestamptz NOT NULL DEFAULT now(),
    actualizado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proyecto_localidad (
    id               bigserial PRIMARY KEY,
    proyecto_id      bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    unidad_id        bigint NOT NULL REFERENCES unidad_territorial(id),
    area             area_geografica NOT NULL DEFAULT 'total',
    cobertura_pct    numeric NOT NULL DEFAULT 100 CHECK (cobertura_pct BETWEEN 0 AND 100),
    anio_ingreso     int,                          -- etapa de expansión en que se incorpora
    metodo_proyeccion text,
    poblacion_base   numeric,                      -- si se ajusta con información local
    poblacion_base_clasificacion clasificacion_dato,
    poblacion_base_soporte text,
    tasa_manual      numeric,
    justificacion    text,
    UNIQUE (proyecto_id, unidad_id, area)
);

CREATE TABLE proyecto_etapa (
    id               bigserial PRIMARY KEY,
    proyecto_id      bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    nombre           text NOT NULL,
    anio_inicio      int NOT NULL,
    anio_fin         int NOT NULL,
    descripcion      text,
    CHECK (anio_fin >= anio_inicio)
);

CREATE TABLE demanda_adicional (
    id               bigserial PRIMARY KEY,
    proyecto_id      bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    tipo             text NOT NULL,                -- institucional, comercial, industrial, turística, pecuaria...
    descripcion      text,
    caudal_lps       numeric NOT NULL CHECK (caudal_lps >= 0),
    afecta_k1        boolean NOT NULL DEFAULT true,
    afecta_k2        boolean NOT NULL DEFAULT true,
    incluye_perdidas boolean NOT NULL DEFAULT false,
    incluida_en_dotacion boolean NOT NULL DEFAULT false, -- si true se excluye para evitar duplicidad
    justificacion    text NOT NULL
);

CREATE TABLE area_influencia (
    id               bigserial PRIMARY KEY,
    proyecto_id      bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    nombre           text NOT NULL,
    origen           text NOT NULL CHECK (origen IN ('dibujo','geojson','kml','shapefile')),
    geom             geometry(MultiPolygon, 4326) NOT NULL,
    creado_en        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ai_geom ON area_influencia USING gist (geom);

-- Cada cálculo emitido se congela (entrada + resultado + hash) para que la
-- memoria pueda reproducirse aunque cambien las series posteriormente.
CREATE TABLE calculo (
    id               bigserial PRIMARY KEY,
    proyecto_id      bigint REFERENCES proyecto(id) ON DELETE CASCADE,
    version_motor    text NOT NULL,
    entrada          jsonb NOT NULL,
    resultado        jsonb NOT NULL,
    sha256           char(64) NOT NULL,
    usuario          text NOT NULL DEFAULT current_user,
    creado_en        timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Auditoría
-- ---------------------------------------------------------------------

CREATE TABLE auditoria (
    id               bigserial PRIMARY KEY,
    tabla            text NOT NULL,
    registro_id      text,
    accion           text NOT NULL,
    antes            jsonb,
    despues          jsonb,
    usuario          text NOT NULL DEFAULT coalesce(current_setting('app.usuario', true), current_user),
    ts               timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION f_auditoria() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO auditoria (tabla, registro_id, accion, antes, despues)
    VALUES (TG_TABLE_NAME,
            coalesce((to_jsonb(NEW)->>'id'), (to_jsonb(OLD)->>'id')),
            TG_OP,
            CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) - 'geom' END,
            CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) - 'geom' END);
    RETURN coalesce(NEW, OLD);
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['dato_demografico','parametro_normativo','proyecto',
                             'proyecto_localidad','demanda_adicional','relacion_territorial']
    LOOP
        EXECUTE format('CREATE TRIGGER tg_aud_%1$s AFTER INSERT OR UPDATE OR DELETE ON %1$s
                        FOR EACH ROW EXECUTE FUNCTION f_auditoria()', t);
    END LOOP;
END $$;
-- (unidad_territorial se audita solo en UPDATE/DELETE para no inflar la bitácora en cargas masivas)
CREATE TRIGGER tg_aud_unidad AFTER UPDATE OR DELETE ON unidad_territorial
    FOR EACH ROW EXECUTE FUNCTION f_auditoria();

-- ---------------------------------------------------------------------
-- Vistas de consulta
-- ---------------------------------------------------------------------

-- Serie demográfica vigente con su procedencia completa.
CREATE VIEW v_serie_vigente AS
SELECT d.id, d.unidad_id, u.codigo, u.nombre, u.tipo, d.anio, d.area, d.variable, d.valor,
       d.clasificacion, d.serie, d.metodologia,
       f.entidad || ' — ' || f.producto || coalesce(' (' || f.version || ')', '') AS fuente,
       f.url AS fuente_url, f.fecha_consulta, u.es_demostracion
FROM dato_demografico d
JOIN unidad_territorial u ON u.id = d.unidad_id
JOIN fuente f ON f.id = d.fuente_id
WHERE d.vigente;

-- Superposiciones geométricas entre unidades (para evitar doble conteo).
CREATE OR REPLACE FUNCTION f_superposiciones(ids bigint[])
RETURNS TABLE (a bigint, b bigint, fraccion_a numeric, fraccion_b numeric) LANGUAGE sql STABLE AS $$
    SELECT x.id, y.id,
           round((ST_Area(ST_Intersection(x.geom, y.geom)::geography) / nullif(ST_Area(x.geom::geography),0))::numeric, 4),
           round((ST_Area(ST_Intersection(x.geom, y.geom)::geography) / nullif(ST_Area(y.geom::geography),0))::numeric, 4)
    FROM unidad_territorial x JOIN unidad_territorial y ON x.id < y.id
    WHERE x.id = ANY(ids) AND y.id = ANY(ids)
      AND x.geom IS NOT NULL AND y.geom IS NOT NULL
      AND ST_Intersects(x.geom, y.geom)
      AND NOT ST_Touches(x.geom, y.geom);
$$;
