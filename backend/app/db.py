import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://acueducto:acueducto@localhost:5432/acueducto")
RAIZ = Path(__file__).resolve().parents[2]


@contextmanager
def conexion(usuario: str | None = None):
    with psycopg.connect(DSN, row_factory=dict_row) as con:
        if usuario:
            con.execute("SELECT set_config('app.usuario', %s, false)", (usuario,))
        yield con


def migrar(reiniciar: bool = False):
    with psycopg.connect(DSN, autocommit=True) as con:
        if reiniciar:
            con.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        existe = con.execute("SELECT to_regclass('public.unidad_territorial')").fetchone()[0]
        if not existe:
            for f in sorted((RAIZ / "db").glob("*.sql")):
                con.execute(f.read_text(encoding="utf-8"))
