# Diseño técnico — Plataforma demográfica y de caudales para acueductos en Colombia

Versión 0.1 · 25 de octubre de 2026

Este documento responde a las siete condiciones previas al desarrollo (numeral 11 del requerimiento) y describe la primera versión operativa.

> **Estado de verificación.** Los portales del DANE, de MinVivienda y de la CRA no fueron accesibles desde el entorno de desarrollo (el proxy bloqueó la conexión). Las fuentes y los valores normativos se confirmaron con buscadores y fuentes secundarias que citan el texto oficial. En el sistema quedan como `verificado_fuente_secundaria` o `pendiente`. Antes de emitir diseños definitivos hay que cotejarlos con el texto consolidado de la Res. 0330/2017 (MinVivienda o Gestor Normativo de la CRA).

---

## 1. Fuentes oficiales del DANE disponibles

| Fuente | Qué contiene | Mecanismo de integración |
|---|---|---|
| **DIVIPOLA** | Códigos de departamento (2 dígitos), municipio (5) y centro poblado (8, con tipo CM/C/IP/CAS…) | Descarga XLSX/CSV del geoportal, o API SODA de datos.gov.co (JSON) → `importers/divipola.py` |
| **CNPV 2018** | Población censada, viviendas, hogares y sexo por municipio y área | Cuadros XLSX, REDATAM, microdatos (ANDA) → `importers/poblacion.importar_tabla_poblacion` |
| **MGN integrado con CNPV 2018** | Geometría de departamentos, municipios, sectores, secciones y manzanas, con atributos censales (personas, viviendas, hogares) | Shapefile/GDB → `importers/geo.cargar_capa` |
| **Censo General 2005** | Población por municipio y área (cabecera / resto) | Cuadros XLSX / REDATAM → importador de censo |
| **Censo 1993** | Población por municipio y área | Cuadros históricos / REDATAM → importador de censo |
| **Proyecciones y retroproyecciones municipales por área** (base CNPV 2018) | Serie anual 2018–2042 y retroproyección 1985–2017 por municipio × área; versión con sexo y edad | XLSX (formato largo o ancho) → `importers/poblacion.importar_proyecciones`. La página oficial registra una actualización del **8 de agosto de 2025**. |
| **Capa de veredas** (geoportal DANE / IGAC / entes territoriales) | Límites veredales de referencia, sin población | Shapefile → importador de capas |

**Sobre las API.** El DANE no ofrece un API REST oficial de series de población. El único servicio de datos estructurado es el API SODA de datos.gov.co, y sirve sobre todo para la DIVIPOLA. Todo lo demás entra por **importación verificable de archivos**: cada carga registra fuente, versión, URL, fecha de consulta, nombre del archivo y SHA-256 (tabla `carga_datos`).

## 2. Nivel de desagregación geográfica

| Nivel | Censo 2018 | Censos 2005/1993 | Proyecciones DANE | Cartografía |
|---|---|---|---|---|
| Departamento / municipio | Sí | Sí | Sí | MGN |
| Área (cabecera / centros poblados y rural disperso) | Sí | Sí (cabecera / resto) | Sí | MGN (clase) |
| Centro poblado | Sí (CNPV 2018) | Parcial | **No** | MGN |
| Sector / sección rural | Sí (MGN integrado) | No comparable | **No** | MGN |
| **Vereda** | **No** | **No** | **No** | Capa de referencia |

Por eso la aplicación **nunca asigna población oficial a una vereda**, y en particular nunca le asigna el total rural del municipio. Las proyecciones oficiales sólo se ofrecen para municipio × área.

## 3. Población censada frente a proyecciones oficiales

- **Censada**: el conteo efectivo del operativo censal, sin ajuste por omisión. Se clasifica `censo_oficial_dane`.
- **Proyección o retroproyección oficial**: la población conciliada (corregida por omisión) proyectada por el DANE con el método de componentes. Se clasifica `proyeccion_oficial_dane`.
- Las dos cifras **no son iguales para el mismo año** (2018): la conciliada es mayor. La aplicación las guarda como series distintas, las muestra por separado y nunca las mezcla al calcular tasas.
- Si la serie DANE no cubre el horizonte (termina en 2042 y un proyecto que arranca en 2026 llega a 2051), los años posteriores se extrapolan y se marcan `estimacion_tecnica`.

Clasificaciones implementadas, según el requisito 9: `censo_oficial_dane`, `proyeccion_oficial_dane`, `oficial_otra_entidad`, `estimacion_tecnica`, `informacion_campo` y `pendiente_verificacion`. Una restricción `CHECK` de la base impide guardar una estimación técnica con nombre de serie oficial, y viceversa.

## 4. Normativa aplicada

| Parámetro | Valor | Fundamento | Verificación |
|---|---|---|---|
| Período de diseño | 25 años (más con análisis técnico-económico en proyectos especiales) | Res. 0330/2017 art. 40, modificado por el art. 1 de la Res. 0799/2021 | fuente secundaria |
| Dotación neta máxima | 120 L/hab·día (> 2000 m s.n.m.); 130 (1000–2000); 140 (< 1000) | Res. 0330/2017 art. 43 | fuente secundaria |
| Pérdidas técnicas máximas | 25 %; dbruta = dneta / (1 − %p) | art. 44 | fuente secundaria |
| K1 máximo | 1,3 (P ≤ 12.500 hab); 1,2 (P > 12.500) | art. 47 | fuente secundaria |
| K2 máximo | 1,6 (P ≤ 12.500 hab); 1,5 (P > 12.500) | art. 47 | fuente secundaria |
| Caudales | Qmd = P·dbruta/86 400; QMD = K1·Qmd; QMH = K2·QMD | art. 47 | fuente secundaria |
| Dotación rural (esquema diferencial) | 20–200 L/hab·día con volúmenes de subsistencia | Res. 0844/2018 (número de artículo por confirmar) | **pendiente** |

- Los K deben salir de registros de macromedición. Si no se marcan como medidos, la aplicación usa el máximo normativo y advierte que la selección debe justificarse.
- La Res. 0330/2017 exige proyectar la población dentro del período de diseño, pero no impone un método. Los métodos aritmético, geométrico y exponencial siguen la formulación del RAS‑2000 Título B, hoy guía técnica no vinculante.
- Los parámetros viven en `data/seed/parametros_normativos.json` y en la tabla `parametro_normativo`, con vigencia y estado de verificación. Ningún valor normativo está escrito directamente en el código.

## 5. Modelo de base de datos (PostgreSQL + PostGIS)

El esquema completo está en `db/001_esquema.sql`.

```
fuente ─┬─< carga_datos
        │
unidad_territorial (jerarquía padre_id; geom MultiPolygon 4326; centroide; altitud; clasificación)
   │  ├─< relacion_territorial (contiene / intersecta / equivalente / segregado_de, fracción de área y de viviendas)
   │  └─< dato_demografico (año, área, variable, valor, clasificación, serie, fuente, carga, vigente, reemplaza_id, soporte)
   │
parametro_normativo (clave, valor, condición JSON, norma, artículo, vigencia, verificación)
proyecto ─┬─< proyecto_localidad (unidad, área, cobertura, año de ingreso, método, base ajustada + soporte)
          ├─< proyecto_etapa
          ├─< demanda_adicional (afecta K1/K2, incluye pérdidas, incluida en dotación)
          ├─< area_influencia (geom; origen: dibujo / GeoJSON / KML / Shapefile)
          └─< calculo (entrada + resultado congelados + SHA-256 + versión del motor)
auditoria (tabla, registro, acción, antes/después JSONB, usuario, ts) ← triggers
```

Decisiones clave:

- **Versionado de series.** Un índice único parcial garantiza un solo valor vigente por (unidad, año, área, variable, serie). Una nueva versión de las proyecciones del DANE marca la anterior `vigente = false` sin borrarla.
- **Veredas y unidades censales.** Las veredas no se modelan como hijas de sectores censales. La relación vive en `relacion_territorial` con fracciones explícitas, calculadas por intersección PostGIS o por conteo de viviendas.
- **Búsqueda.** Índice trigram sobre el nombre sin tildes, más búsqueda por prefijo de código DIVIPOLA.
- **Doble conteo.** `f_superposiciones()` calcula las fracciones de superposición geométrica entre las unidades seleccionadas.
- **Historial.** Triggers de auditoría en datos, parámetros, proyectos y relaciones. Cada memoria emitida se congela en `calculo`.

## 6. Metodología para veredas sin dato censal individual

Por orden de preferencia (`backend/app/calc/estimacion.py`):

1. **Censo local o encuesta de campo** (JAC, operador, conteo casa a casa) → `informacion_campo`. Exige soporte documental.
2. **Prorrateo de unidades censales del MGN/CNPV 2018.** P_vereda = Σ_s P_s · f_s, donde f_s es la fracción de **viviendas** de la sección o sector s que queda dentro de la vereda. Si sólo se conoce la fracción de área, el cálculo se hace igual pero con una advertencia explícita: supone densidad uniforme.
3. **Viviendas ocupadas × habitantes por vivienda.** Las viviendas se cuentan en campo o sobre ortofoto; el habitantes por vivienda sale del área de centros poblados y rural disperso del municipio en el CNPV 2018.
4. **Hogares × personas por hogar**, con la misma lógica.

Todo resultado se guarda como `estimacion_tecnica` (serie `EST_*`), con su metodología. Una validación avisa si la suma de las veredas supera la población rural oficial del municipio.

Para **proyectar** una vereda sin serie propia, la aplicación ofrece la *tasa de referencia del área del municipio*: la tasa intercensal del área de centros poblados y rural disperso, aplicada sobre la población base de la vereda. Esta elección queda escrita en la justificación.

## 7. Arquitectura funcional

```
┌───────────────────────── Navegador ─────────────────────────┐
│ HTML + JS (Leaflet, Leaflet.draw, Chart.js, servidos sin CDN)│
│ Selección territorial · Demografía · Proyección · Caudales   │
│ Mapa · Validaciones · Fuentes y normativa · Exportación      │
└───────────────▲───────────────────────────────┬─────────────┘
                │ JSON (recalcula en cada cambio)│
┌───────────────┴────────── FastAPI ────────────▼─────────────┐
│ main.py (REST)  →  servicio.py (orquestación única)          │
│   calc/proyeccion · calc/caudales · calc/sistema ·           │
│   calc/estimacion · calc/normativa   (Python puro, probado)  │
│   importers/ divipola · poblacion · geo (GeoJSON/KML/SHP)    │
│   reports/ contenido → Word (python-docx) · PDF (ReportLab)  │
│            Excel con fórmulas vivas (openpyxl)               │
│   validaciones.py (consistencia, duplicidad, faltantes)      │
└───────────────▲─────────────────────────────────────────────┘
                │ psycopg 3
┌───────────────┴─────────── PostgreSQL 16 + PostGIS 3 ───────┐
│ Esquema versionado y auditado (db/001_esquema.sql)           │
└─────────────────────────────────────────────────────────────┘
        ▲
scripts/actualizar_fuentes.py  (DIVIPOLA vía SODA o archivo, proyecciones, censos, capas MGN/veredas)
```

- **Una sola ruta de cálculo.** La pantalla y las tres memorias salen de `servicio.calcular()`. El Excel reconstruye el cálculo con fórmulas y una prueba automática lo recalcula en LibreOffice para comprobar que coincide con el motor.
- **Seguridad.** Las rutas de importación se protegen con `TOKEN_ADMIN`. Todo el SQL va parametrizado. La auditoría toma el usuario de `app.usuario`. El siguiente paso es autenticación con roles (consulta / diseñador / administrador de datos).
- **Escalabilidad.** El esquema carga los 1.122 municipios y admite veredas y sectores de forma gradual. Las consultas espaciales usan índices GiST y las geometrías se simplifican al servirlas.

## 8. Alcance de la versión 0.1 y pendientes

**Incluido:** consulta de departamentos y municipios; búsqueda por nombre o código; ficha demográfica con trazabilidad; proyección aritmética, geométrica, exponencial y DANE con comparación, gráfica, sugerencia razonada y advertencias; ajustes con soporte; caudales con validación normativa; demandas adicionales sin duplicidad; sistemas de varias localidades con cobertura, etapas y detección de doble conteo; mapa con dibujo de área de influencia y carga de GeoJSON, KML/KMZ y Shapefile; validaciones de calidad; memorias en Excel, Word y PDF; proyectos guardados con historial.

**Datos incluidos:**
- `data/seed/divipola_municipios_npm2021.csv`: 1.121 municipios y áreas no municipalizadas, tomados del paquete npm `divipola` 1.0.3 (MIT, 2021). Es una copia **no oficial**, marcada `pendiente_verificacion`. Puede no incluir cambios posteriores a 2021.
- `data/demo/demo.json`: un municipio **ficticio**, ubicado a propósito en el océano Pacífico, para probar la herramienta. La interfaz y las memorias lo marcan como tal en cada página.
- **No se incluye ninguna cifra poblacional real.** Hay que importarlas con `scripts/actualizar_fuentes.py` desde una máquina con acceso a los portales oficiales.

**Pendiente:**
- Cotejar la normativa con el texto oficial consolidado (Res. 0799/2021 y numeración de la Res. 0844/2018).
- Confirmar los nombres de archivo y de campo de cada versión del DANE.
- Autenticación y roles.
- Prorrateo veredal automático desde el MGN integrado: requiere cargar el shapefile y su tabla de atributos censales.
- Mapa coroplético de demanda.
- Plantillas institucionales de memoria.
