# Acueducto CO — Población de diseño y caudales de acueducto (Colombia)

Aplicación web para consultar información demográfica oficial (DANE), proyectar población y calcular caudales de diseño según la Res. 0330/2017 (RAS), con trazabilidad completa de cada cifra.

- Diseño técnico (fuentes, normativa, modelo de datos, metodología veredal, arquitectura): [`docs/DISENO_TECNICO.md`](docs/DISENO_TECNICO.md)
- Esquema PostGIS: [`db/001_esquema.sql`](db/001_esquema.sql)
- Parámetros normativos con su fundamento: [`data/seed/parametros_normativos.json`](data/seed/parametros_normativos.json)

## Puesta en marcha

Con Docker:

```bash
docker compose up --build
# http://localhost:8000
```

Sin Docker (PostgreSQL 14+ con PostGIS):

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://acueducto:acueducto@localhost:5432/acueducto
cd backend
python -m app.semillas            # esquema + normativa + municipios semilla + demo ficticio
uvicorn app.main:app --port 8000
```

`python -m app.semillas --sin-demo` omite el conjunto ficticio. `--reiniciar` borra y recrea el esquema.

## Cargar datos oficiales

El repositorio **no trae cifras de población reales**. Impórtelas desde los archivos publicados por el DANE (ver ejemplos en `scripts/actualizar_fuentes.py`):

```bash
python scripts/actualizar_fuentes.py divipola --archivo DIVIPOLA.xlsx --version 2026-10
python scripts/actualizar_fuentes.py censo --archivo cnpv2018_mpios.xlsx --anio 2018 --serie CNPV2018 --producto "CNPV 2018"
python scripts/actualizar_fuentes.py proyecciones --archivo proy_mun_area_2018_2042.xlsx --version 2025-08-08
python scripts/actualizar_fuentes.py capa --archivo MGN_MPIO.zip --tipo municipio --campo-codigo MPIO_CDPMP --campo-nombre MPIO_CNMBR --producto "MGN"
```

Las mismas importaciones están disponibles en la API (`POST /api/importar/*`), protegidas con `TOKEN_ADMIN`. Cada carga conserva las versiones anteriores de las series.

## Pruebas

```bash
cd backend
createdb acueducto_test            # base PostGIS de pruebas
python -m pytest -q
```

Hay 31 pruebas: el motor de cálculo con valores verificados a mano, los importadores (formatos DANE largo y ancho, CSV con separador «;», versionado), la integridad (una estimación no puede marcarse oficial; los datos de campo exigen soporte), la detección de doble conteo, las consultas espaciales, KML y las tres memorias. Si LibreOffice está instalado, una prueba adicional recalcula el Excel exportado y comprueba que sus fórmulas reproducen el motor.

## Advertencias

- Los valores normativos están marcados `verificado_fuente_secundaria`: hay que cotejarlos con el texto consolidado oficial antes de un diseño definitivo.
- El DANE no publica población por vereda. Las cifras veredales son siempre información de campo o estimación técnica.
- El listado semilla de municipios es una copia no oficial de 2021 (`pendiente_verificacion`). Reemplácelo con la DIVIPOLA vigente.
