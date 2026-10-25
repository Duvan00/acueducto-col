import math

import pytest

from app.calc import caudales, estimacion, normativa, proyeccion, sistema
from app.calc.proyeccion import PuntoCensal

CENSOS = [PuntoCensal(2005, 10_000), PuntoCensal(2018, 13_000)]


def test_tasas_intercensales():
    t = proyeccion.tasas_intercensales(CENSOS)[0]
    assert t["aritmetica"] == pytest.approx(3000 / 13)
    assert t["geometrica"] == pytest.approx(1.3 ** (1 / 13) - 1)
    assert t["exponencial"] == pytest.approx(math.log(1.3) / 13)


@pytest.mark.parametrize("metodo,esperado", [
    ("aritmetico", 13_000 + 3000 / 13 * 25),
    ("geometrico", 13_000 * 1.3 ** (25 / 13)),
    ("exponencial", 13_000 * 1.3 ** (25 / 13)),   # un solo período: equivale al geométrico
])
def test_proyeccion_metodos(metodo, esperado):
    r = proyeccion.proyectar(metodo, CENSOS, anio_final=2043)
    assert r.poblacion_en(2043) == pytest.approx(esperado)
    assert all(p["clasificacion"] == "estimacion_tecnica" for p in r.serie)


def test_poblacion_base_ajustada():
    r = proyeccion.proyectar("geometrico", CENSOS, anio_base=2025, poblacion_base=15_000, anio_final=2050)
    assert r.poblacion_en(2025) == 15_000
    assert r.poblacion_en(2050) == pytest.approx(15_000 * 1.3 ** (25 / 13))


def test_tasa_negativa_advierte():
    r = proyeccion.proyectar("geometrico", [PuntoCensal(2005, 5000), PuntoCensal(2018, 4000)], anio_final=2043)
    assert any(a.codigo == "TASA_NEGATIVA" for a in r.advertencias)


def test_no_comparable_se_excluye():
    c = [PuntoCensal(1993, 3000, comparable=False, nota="municipio segregado en 1996"),
         PuntoCensal(2005, 10_000), PuntoCensal(2018, 13_000)]
    r = proyeccion.proyectar("geometrico", c, anio_final=2043)
    assert r.tasa == pytest.approx(1.3 ** (1 / 13) - 1)
    assert any(a.codigo == "NO_COMPARABLE" for a in r.advertencias)


def test_dane_extrapolacion_no_es_oficial():
    dane = {y: 13_000 * 1.01 ** (y - 2018) for y in range(2018, 2043)}
    r = proyeccion.proyectar("dane_oficial", CENSOS, anio_base=2025, anio_final=2050, serie_dane=dane)
    assert {p["clasificacion"] for p in r.serie if p["anio"] <= 2042} == {"proyeccion_oficial_dane"}
    assert {p["clasificacion"] for p in r.serie if p["anio"] > 2042} == {"estimacion_tecnica"}
    assert r.poblacion_en(2050) == pytest.approx(13_000 * 1.01 ** 32, rel=1e-9)


def test_recomendacion_usa_dane():
    dane = {y: 13_000 + 3000 / 13 * (y - 2018) for y in range(2018, 2043)}
    comp = proyeccion.comparar_metodos(CENSOS, anio_final=2043, serie_dane=dane)
    assert comp["recomendacion"]["metodo"] == "aritmetico"


def test_dotacion_por_altitud():
    p = normativa.cargar()
    assert normativa.buscar(p, "dotacion_neta_max", altitud=2500)["valor"] == 120
    assert normativa.buscar(p, "dotacion_neta_max", altitud=2000)["valor"] == 130
    assert normativa.buscar(p, "dotacion_neta_max", altitud=1000)["valor"] == 130
    assert normativa.buscar(p, "dotacion_neta_max", altitud=999)["valor"] == 140


def test_caudales_basicos():
    r = caudales.calcular(caudales.EntradaCaudales(poblacion=5000, altitud_msnm=2500))
    assert r["dotacion_neta"] == 120
    assert r["dotacion_bruta"] == pytest.approx(160)
    assert r["qmd_lps"] == pytest.approx(5000 * 160 / 86400)
    assert r["qmd_m3d"] == pytest.approx(800)
    assert r["k1"] == 1.3 and r["k2"] == 1.6
    assert r["QMD_lps"] == pytest.approx(1.3 * 5000 * 160 / 86400)
    assert r["QMH_lps"] == pytest.approx(1.6 * 1.3 * 5000 * 160 / 86400)
    assert r["cumple_normativa"]


def test_coeficientes_poblacion_mayor():
    r = caudales.calcular(caudales.EntradaCaudales(poblacion=20_000, altitud_msnm=500))
    assert (r["k1"], r["k2"], r["dotacion_neta"]) == (1.2, 1.5, 140)


def test_incumplimientos():
    r = caudales.calcular(caudales.EntradaCaudales(poblacion=20_000, altitud_msnm=2600, dotacion_neta=150,
                                                   perdidas_pct=30, k1=1.3))
    codigos = {a["codigo"] for a in r["advertencias"]}
    assert {"DOTACION_EXCEDE", "PERDIDAS_EXCEDE", "K1_EXCEDE"} <= codigos
    assert not r["cumple_normativa"]


def test_demandas_adicionales_sin_duplicar():
    d = [caudales.DemandaAdicional("institucional", 0.75, "Colegio 500 estudiantes"),
         caudales.DemandaAdicional("comercial", 1.0, "Ya en dotación", incluida_en_dotacion=True),
         caudales.DemandaAdicional("industrial", 1.0, "Planta láctea", incluye_perdidas=True, afecta_k2=False)]
    r = caudales.calcular(caudales.EntradaCaudales(poblacion=5000, altitud_msnm=2500, demandas=d))
    q_res = 5000 * 160 / 86400
    q_inst = 0.75 / 0.75
    assert r["demandas_adicionales_lps"] == pytest.approx(q_inst + 1.0)
    assert r["QMD_lps"] == pytest.approx(1.3 * (q_res + q_inst + 1.0))
    assert r["QMH_lps"] == pytest.approx(1.6 * 1.3 * (q_res + q_inst) + 1.3 * 1.0)
    assert r["demandas_excluidas"] == ["comercial"]


def test_sistema_detecta_jerarquia_y_cobertura():
    a = sistema.LocalidadSistema(1, "Municipio X — rural", CENSOS, "geometrico")
    b = sistema.LocalidadSistema(2, "Vereda Y", CENSOS, "aritmetico", cobertura_pct=50, ancestros=[1])
    res = sistema.consolidar([a, b], 2018, 2043)
    assert any(x["codigo"] == "JERARQUIA_SUPERPUESTA" for x in res["advertencias"])
    pa = 13_000 * 1.3 ** (25 / 13)
    pb = 0.5 * (13_000 + 3000 / 13 * 25)
    assert res["poblacion_diseno"] == pytest.approx(pa + pb)


def test_sistema_etapa_ingreso():
    a = sistema.LocalidadSistema(1, "A", CENSOS, "aritmetico", anio_ingreso=2030)
    res = sistema.consolidar([a], 2018, 2043)
    s = {x["anio"]: x["poblacion_servida"] for x in res["serie_total"]}
    assert s[2029] == 0 and s[2030] > 0


def test_estimacion_vereda_prorrateo():
    r = estimacion.prorrateo_unidades_censales([
        {"codigo": "S1", "poblacion": 800, "viviendas_total": 200, "viviendas_en_vereda": 50},
        {"codigo": "S2", "poblacion": 400, "fraccion_area": 0.25}])
    assert r["poblacion"] == pytest.approx(200 + 100)
    assert r["clasificacion"] == "estimacion_tecnica"
    assert r["advertencias"]
