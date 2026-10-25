"""Utilidades comunes de importación: registro de fuente, lote de carga con
hash del archivo, lectura tolerante de CSV/XLSX publicados por el DANE."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from pathlib import Path

import openpyxl


def normalizar(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leer_tabla(data: bytes, nombre: str, alias: dict[str, list[str]], hoja: str | None = None,
               max_filas_encabezado: int = 30) -> list[dict]:
    """Devuelve filas como dict con claves canónicas (las de `alias`).

    Los anexos del DANE traen títulos y notas antes del encabezado: se busca la
    primera fila que contenga al menos dos de los alias esperados.
    """
    if nombre.lower().endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb[hoja] if hoja else wb.worksheets[0]
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        texto = data.decode("utf-8-sig", errors="replace")
        dialecto = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t|")
        filas = list(csv.reader(io.StringIO(texto), dialecto))

    alias_n = {canon: {normalizar(a) for a in [canon] + lista} for canon, lista in alias.items()}
    for i, fila in enumerate(filas[:max_filas_encabezado]):
        enc = [normalizar(c) for c in fila]
        mapa = {}
        for j, c in enumerate(enc):
            for canon, nombres in alias_n.items():
                if c in nombres and canon not in mapa:
                    mapa[canon] = j
        if len(mapa) >= 2:
            break
    else:
        raise ValueError(f"No se encontró fila de encabezado reconocible en {nombre}. Alias esperados: "
                         + json.dumps(alias, ensure_ascii=False))
    extra = {j: enc[j] for j in range(len(enc)) if j not in mapa.values() and enc[j]}
    out = []
    for fila in filas[i + 1:]:
        if not any(v not in (None, "") for v in fila):
            continue
        d = {canon: (fila[j] if j < len(fila) else None) for canon, j in mapa.items()}
        d["_extra"] = {k: fila[j] for j, k in extra.items() if j < len(fila)}
        out.append(d)
    return out


def numero(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):      # 1.234.567,8
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):    # 1,234,567.8
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def codigo(v, ancho: int) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, float):
        v = int(v)
    s = re.sub(r"\D", "", str(v))
    return s.zfill(ancho) if s else None


def registrar_fuente(con, entidad, producto, version=None, url=None, fecha_publicacion=None,
                     nivel_desagregacion=None, notas=None, fecha_consulta=None) -> int:
    r = con.execute(
        """INSERT INTO fuente (entidad, producto, version, url, fecha_publicacion, fecha_consulta, nivel_desagregacion, notas)
           VALUES (%s,%s,%s,%s,%s,coalesce(%s, current_date),%s,%s)
           ON CONFLICT (entidad, producto, version) DO UPDATE SET url = coalesce(EXCLUDED.url, fuente.url),
               notas = coalesce(EXCLUDED.notas, fuente.notas)
           RETURNING id""",
        (entidad, producto, version, url, fecha_publicacion, fecha_consulta, nivel_desagregacion, notas)).fetchone()
    return r["id"]


def iniciar_carga(con, fuente_id: int, archivo: str, data: bytes | None, importador: str) -> int:
    return con.execute(
        "INSERT INTO carga_datos (fuente_id, archivo, sha256, importador, estado) VALUES (%s,%s,%s,%s,'en_proceso') RETURNING id",
        (fuente_id, archivo, sha256(data) if data else None, importador)).fetchone()["id"]


def cerrar_carga(con, carga_id: int, registros: int, advertencias: list[str]):
    con.execute("UPDATE carga_datos SET registros=%s, advertencias=%s, estado='completada' WHERE id=%s",
                (registros, json.dumps(advertencias, ensure_ascii=False), carga_id))


def leer_archivo(ruta: str | Path) -> tuple[bytes, str]:
    p = Path(ruta)
    return p.read_bytes(), p.name
