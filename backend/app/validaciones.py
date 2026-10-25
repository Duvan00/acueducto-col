"""Validaciones de calidad de la información: inconsistencias, duplicidades,
faltantes y diferencias entre divisiones territoriales."""
from __future__ import annotations

CONSULTAS = {
    "suma_areas": ("critica", "Cabecera + centros poblados y rural disperso ≠ total",
        """SELECT u.codigo, u.nombre, t.anio, t.serie, t.valor::float total, (c.valor + r.valor)::float suma
           FROM dato_demografico t
           JOIN dato_demografico c ON c.unidad_id=t.unidad_id AND c.anio=t.anio AND c.serie=t.serie AND c.variable='poblacion'
                AND c.area='cabecera' AND c.vigente
           JOIN dato_demografico r ON r.unidad_id=t.unidad_id AND r.anio=t.anio AND r.serie=t.serie AND r.variable='poblacion'
                AND r.area='centros_poblados_rural_disperso' AND r.vigente
           JOIN unidad_territorial u ON u.id=t.unidad_id
           WHERE t.variable='poblacion' AND t.area='total' AND t.vigente AND abs(t.valor - c.valor - r.valor) > 1 {m}"""),
    "sexo": ("advertencia", "Hombres + mujeres ≠ población",
        """SELECT u.codigo, u.nombre, p.anio, p.area, p.serie, p.valor::float poblacion, (h.valor + w.valor)::float suma
           FROM dato_demografico p
           JOIN dato_demografico h ON h.unidad_id=p.unidad_id AND h.anio=p.anio AND h.area=p.area AND h.serie=p.serie AND h.variable='hombres' AND h.vigente
           JOIN dato_demografico w ON w.unidad_id=p.unidad_id AND w.anio=p.anio AND w.area=p.area AND w.serie=p.serie AND w.variable='mujeres' AND w.vigente
           JOIN unidad_territorial u ON u.id=p.unidad_id
           WHERE p.variable='poblacion' AND p.vigente AND abs(p.valor - h.valor - w.valor) > 1 {m}"""),
    "nombres_duplicados": ("advertencia", "Unidades con el mismo nombre y tipo en un municipio",
        """SELECT u.cod_municipio codigo, u.tipo::text, u.nombre_normalizado nombre, count(*) n
           FROM unidad_territorial u WHERE u.cod_municipio IS NOT NULL {m}
           GROUP BY 1,2,3 HAVING count(*) > 1"""),
    "salto_censal": ("advertencia", "Variación intercensal superior a ±50 % (posible cambio territorial o de cobertura)",
        """SELECT u.codigo, u.nombre, a.area, a.anio anio_a, b.anio anio_b, a.valor::float pa, b.valor::float pb
           FROM dato_demografico a JOIN dato_demografico b ON b.unidad_id=a.unidad_id AND b.area=a.area AND b.variable='poblacion'
                AND b.clasificacion='censo_oficial_dane' AND b.vigente AND b.anio > a.anio
                AND NOT EXISTS (SELECT 1 FROM dato_demografico x WHERE x.unidad_id=a.unidad_id AND x.area=a.area AND x.variable='poblacion'
                                AND x.clasificacion='censo_oficial_dane' AND x.vigente AND x.anio > a.anio AND x.anio < b.anio)
           JOIN unidad_territorial u ON u.id=a.unidad_id
           WHERE a.variable='poblacion' AND a.clasificacion='censo_oficial_dane' AND a.vigente AND a.valor > 0
             AND abs(b.valor / a.valor - 1) > 0.5 {m}"""),
    "veredas_exceden_rural": ("critica", "La suma de población de veredas supera la población rural del municipio",
        """WITH v AS (SELECT u.cod_municipio, d.anio, sum(d.valor) s FROM dato_demografico d JOIN unidad_territorial u ON u.id=d.unidad_id
                      WHERE u.tipo='vereda' AND d.variable='poblacion' AND d.vigente GROUP BY 1,2)
           SELECT m.codigo, m.nombre, v.anio, v.s::float suma_veredas, r.valor::float rural
           FROM v JOIN unidad_territorial m ON m.codigo=v.cod_municipio AND m.tipo='municipio'
           JOIN dato_demografico r ON r.unidad_id=m.id AND r.anio=v.anio AND r.area='centros_poblados_rural_disperso'
                AND r.variable='poblacion' AND r.vigente
           WHERE v.s > r.valor * 1.02 {mm}"""),
    "municipios_sin_poblacion": ("info", "Municipios sin ningún dato de población cargado",
        """SELECT u.codigo, u.nombre FROM unidad_territorial u WHERE u.tipo='municipio'
           AND NOT EXISTS (SELECT 1 FROM dato_demografico d WHERE d.unidad_id=u.id AND d.variable='poblacion' AND d.vigente) {m}
           ORDER BY u.codigo LIMIT 200"""),
    "pendientes_verificacion": ("info", "Unidades territoriales pendientes de verificación (no demostración)",
        """SELECT u.tipo::text, count(*) n FROM unidad_territorial u WHERE u.clasificacion='pendiente_verificacion'
           AND NOT u.es_demostracion {m} GROUP BY 1"""),
    "codigo_departamento": ("critica", "Código de municipio no coincide con su departamento",
        """SELECT u.codigo, u.nombre, u.cod_departamento FROM unidad_territorial u
           WHERE u.tipo='municipio' AND left(u.codigo, 2) <> u.cod_departamento {m}"""),
    "sin_geometria": ("info", "Unidades sin cartografía cargada (por tipo)",
        """SELECT u.tipo::text, count(*) n FROM unidad_territorial u WHERE u.geom IS NULL {m} GROUP BY 1"""),
}


def ejecutar(con, municipio: str | None = None) -> list[dict]:
    out = []
    for clave, (nivel, titulo, sql) in CONSULTAS.items():
        filtro = "AND u.cod_municipio = %(mun)s" if municipio else ""
        filtro_m = "AND m.codigo = %(mun)s" if municipio else ""
        filas = con.execute(sql.format(m=filtro, mm=filtro_m), {"mun": municipio}).fetchall()
        out.append({"clave": clave, "nivel": nivel, "titulo": titulo, "n": len(filas), "filas": filas[:100]})
    return out
