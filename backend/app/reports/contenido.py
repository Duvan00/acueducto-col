"""Estructura de la memoria de cálculo como lista de bloques neutros
(h1/h2/p/tabla/imagen/formula/aviso), que luego se renderizan a Word y PDF."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ..calc.proyeccion import NOMBRE_METODO  # noqa: E402
from ..servicio import ETIQUETA_CLASIF  # noqa: E402

AREA = {"total": "Total", "cabecera": "Cabecera", "centros_poblados_rural_disperso": "C. poblados y rural disperso"}
COLORES = {"aritmetico": "#2a6f97", "geometrico": "#c9184a", "exponencial": "#6a994e", "dane_oficial": "#7b2cbf"}


def n0(x):
    return "—" if x is None else f"{x:,.0f}".replace(",", ".")


def n(x, d=2):
    if x is None:
        return "—"
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def grafica_localidad(loc: dict, anio_final: int) -> bytes:
    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=150)
    for m, r in loc["comparacion"].items():
        xs = [p["anio"] for p in r["serie"] if p["poblacion"] is not None]
        ys = [p["poblacion"] for p in r["serie"] if p["poblacion"] is not None]
        sel = m == loc["metodo"]
        ax.plot(xs, ys, color=COLORES[m], lw=2.4 if sel else 1.2, ls="-" if sel else "--",
                label=NOMBRE_METODO[m] + (" (adoptado)" if sel else ""))
    if loc["metodo"] not in loc["comparacion"]:
        s = loc["seleccion"]
        pts = [p for p in s["serie"] if p["poblacion"] is not None]
        ax.plot([p["anio"] for p in pts], [p["poblacion"] for p in pts], color=COLORES[loc["metodo"]], lw=2.4,
                label=NOMBRE_METODO[loc["metodo"]] + " (tasa adoptada)")
        ax.scatter([s["anio_base"]], [s["poblacion_base"]], color="#2d6a2d", zorder=5, s=26, label="Población base")
    if loc.get("serie_dane"):
        ys = sorted(loc["serie_dane"].items())
        ax.plot([int(a) for a, _ in ys], [v for _, v in ys], ".", color=COLORES["dane_oficial"], ms=3,
                label="Serie oficial DANE (publicada)")
    c = loc["censos"]
    ax.scatter([x["anio"] for x in c], [x["poblacion"] for x in c], color="black", zorder=5, label="Censos", s=22)
    ax.set_xlabel("Año")
    ax.set_ylabel("Habitantes")
    ax.grid(alpha=.3)
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title(loc["nombre"], fontsize=9)
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    return b.getvalue()


def grafica_sistema(res: dict) -> bytes:
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=150)
    anios = [p["anio"] for p in res["sistema"]["serie_total"]]
    base = [0.0] * len(anios)
    for loc in res["localidades"]:
        ys = [p["servida"] for p in loc["serie_servida"]]
        ax.fill_between(anios, base, [a + b for a, b in zip(base, ys)], alpha=.55, label=loc["nombre"][:40])
        base = [a + b for a, b in zip(base, ys)]
    ax.plot(anios, base, color="black", lw=1.5, label="Total servido")
    ax.set_xlabel("Año")
    ax.set_ylabel("Población servida (hab)")
    ax.grid(alpha=.3)
    ax.legend(fontsize=7, loc="upper left")
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    return b.getvalue()


def construir(res: dict) -> list[tuple]:
    B = []
    h1 = lambda t: B.append(("h1", t))
    h2 = lambda t: B.append(("h2", t))
    p = lambda t: B.append(("p", t))
    tabla = lambda enc, filas, anchos=None: B.append(("tabla", enc, filas, anchos))
    f = lambda t: B.append(("formula", t))
    pr = res["proyecto"]
    q = res["caudales"]

    if res["es_demostracion"]:
        B.append(("aviso", "DOCUMENTO DE DEMOSTRACIÓN — CONTIENE DATOS FICTICIOS. NO VÁLIDO PARA DISEÑO."))

    h1("1. Identificación del proyecto")
    tabla(["Campo", "Valor"], [["Proyecto", pr["nombre"]], ["Código", pr["codigo"] or "—"], ["Entidad", pr["entidad"] or "—"],
                               ["Elaboró", pr["elaboro"] or "—"], ["Tipo de sistema", pr["tipo"]],
                               ["Descripción", pr["descripcion"] or "—"], ["Fecha de cálculo", res["fecha"]],
                               ["Versión del motor de cálculo", res["version_motor"]],
                               ["Huella de los datos de entrada (SHA-256)", res["sha256"][:32] + "…"]], [5, 11])

    h1("2. Localización geográfica")
    filas = []
    for l in res["localidades"]:
        u = l["unidad"] or {}
        anc = " / ".join(a["nombre"] for a in u.get("ancestros", []) if a["tipo"] != "pais")
        filas.append([l["nombre"], u.get("tipo", "manual"), u.get("codigo", "—"), anc or "—",
                      n(u.get("lat"), 5) + ", " + n(u.get("lon"), 5) if u.get("lat") is not None else "—",
                      n0(u.get("altitud_msnm")) if u.get("altitud_msnm") is not None else "—"])
    tabla(["Localidad", "Tipo", "Código", "Jerarquía", "Lat, Lon (WGS84)", "Altitud (m s.n.m.)"], filas)

    h1("3. Fuentes consultadas")
    for s in res["fuentes"]:
        p("• " + s)
    p("Clasificación de los datos utilizada en esta memoria: " + "; ".join(ETIQUETA_CLASIF.values()) + ".")

    h1("4. Información demográfica histórica")
    for l in res["localidades"]:
        h2(l["nombre"] + f" — área: {AREA[l['area']]}")
        if l["censos"]:
            tabla(["Año", "Población", "Fuente", "Clasificación", "Comparable"],
                  [[c["anio"], n0(c["poblacion"]), c["fuente"], ETIQUETA_CLASIF.get(c["clasificacion"], c["clasificacion"]),
                    "Sí" if c["comparable"] else "No — " + c["nota"]] for c in l["censos"]], [2, 3, 7, 4, 3])
        else:
            p("No hay censos disponibles para esta unidad y área.")
        if l["serie_dane"]:
            ys = sorted(l["serie_dane"].items())
            muestra = [x for x in ys if int(x[0]) % 5 == 0 or x == ys[-1]]
            p("Proyección oficial DANE (años seleccionados): " +
              "; ".join(f"{a}: {n0(v)}" for a, v in muestra) + ".")

    h1("5. Análisis de crecimiento poblacional")
    f("ka = (P2 − P1) / (T2 − T1)     r = (P2 / P1)^(1/(T2 − T1)) − 1     k = (ln P2 − ln P1) / (T2 − T1)")
    for l in res["localidades"]:
        if not l["tasas"]:
            continue
        h2(l["nombre"])
        tabla(["Período", "P inicial", "P final", "ka (hab/año)", "r geométrica (%/año)", "k exponencial (%/año)", "Comparable"],
              [[t["periodo"], n0(t["p_inicial"]), n0(t["p_final"]), n(t["aritmetica"], 1), n(t["geometrica"] * 100, 3),
                n(t["exponencial"] * 100, 3), "Sí" if t["comparable"] else "No"] for t in l["tasas"]])
        p(f"Criterio de tasa adoptado: {'promedio de períodos comparables' if res['criterio_tasa']=='promedio' else 'último período intercensal'}.")

    h1("6. Selección y justificación del método de proyección")
    for l in res["localidades"]:
        h2(l["nombre"])
        p(l["justificacion"])
        for a in l["seleccion"]["advertencias"]:
            B.append(("aviso_suave", f"[{a['nivel'].upper()}] {a['mensaje']}"))

    h1("7. Horizonte de diseño")
    pn = res.get("periodo_norma") or {}
    p(f"Año inicial: {res['anio_inicial']}. Período de diseño: {res['periodo_diseno']} años. Año horizonte: {res['anio_final']}. "
      f"Fundamento: {pn.get('norma', '')}, {pn.get('articulo', '')}. {pn.get('texto_referencia', '')}")

    h1("8. Población proyectada")
    f("Aritmético: Pf = Pb + ka·(Tf − Tb)    Geométrico: Pf = Pb·(1 + r)^(Tf − Tb)    Exponencial: Pf = Pb·e^(k·(Tf − Tb))")
    for l in res["localidades"]:
        h2(l["nombre"])
        s = l["seleccion"]
        p(f"Método adoptado: {NOMBRE_METODO[s['metodo']]}. Año base Tb = {s['anio_base']}; Pb = {n0(s['poblacion_base'])} hab; "
          f"tasa = {n(s['tasa'] * (100 if s['unidad_tasa'] != 'hab/año' else 1), 4) if s['tasa'] is not None else '—'} "
          f"{'%/año' if s['unidad_tasa'] != 'hab/año' else 'hab/año'}. Cobertura: {n(l['cobertura_pct'], 0)} %"
          + (f"; ingresa al sistema en {l['anio_ingreso']}" if l["anio_ingreso"] else "") + ".")
        B.append(("imagen", grafica_localidad(l, res["anio_final"]), 16))
    h2("Consolidado del sistema")
    if len(res["localidades"]) > 1:
        B.append(("imagen", grafica_sistema(res), 16))
    serie = res["sistema"]["serie_total"]
    paso = 5
    filas = []
    for i, pto in enumerate(serie):
        if i % paso == 0 or i == len(serie) - 1:
            filas.append([pto["anio"]] + [n0(next(x["servida"] for x in l["serie_servida"] if x["anio"] == pto["anio"]))
                                          for l in res["localidades"]] + [n0(pto["poblacion_servida"])])
    tabla(["Año"] + [l["nombre"][:28] for l in res["localidades"]] + ["Total servido"], filas)
    p(f"Población de diseño (año {res['anio_final']}): {n0(res['sistema']['poblacion_diseno'])} habitantes "
      "(estimación técnica; incluye coberturas y etapas de ingreso).")

    h1("9. Parámetros normativos utilizados")
    tabla(["Parámetro", "Valor", "Norma / artículo", "Verificación"],
          [[x["clave"], f"{x['valor']:g} {x.get('unidad') or ''}", f"{x['norma']} — {x.get('articulo') or ''}",
            x.get("estado_verificacion", "")] for x in res["parametros_normativos"]], [3, 2, 8, 3])

    h1("10. Cálculo de dotaciones")
    for s in q["pasos"]:
        if s["simbolo"] in ("dneta", "dbruta"):
            p(f"{s['nombre']} ({s['simbolo']}) = {n(s['valor'])} {s['unidad']}. {s['fundamento']}")
            if s["formula"]:
                f(f"{s['formula']}  →  {s['sustitucion']}")
            elif s["sustitucion"]:
                p("Justificación: " + s["sustitucion"])

    h1("11. Cálculo de caudales de diseño")
    for s in q["pasos"]:
        if s["simbolo"] not in ("dneta", "dbruta", "P"):
            p(f"{s['nombre']} ({s['simbolo']}) = {n(s['valor'], 3)} {s['unidad']}" + (f". {s['fundamento']}" if s["fundamento"] else ""))
            if s["formula"]:
                f(f"{s['formula']}  →  {s['sustitucion']}")
    if q["demandas"]:
        h2("Demandas adicionales")
        tabla(["Tipo", "Caudal declarado (L/s)", "Caudal bruto (L/s)", "K1", "K2", "Justificación"],
              [[d["tipo"], n(d["caudal_lps"], 3), n(d["caudal_bruto_lps"], 3), "Sí" if d["afecta_k1"] else "No",
                "Sí" if d["afecta_k2"] else "No", d["justificacion"]] for d in q["demandas"]])
    if q["demandas_excluidas"]:
        p("Demandas excluidas por estar incluidas en la dotación residencial: " + ", ".join(q["demandas_excluidas"]) + ".")

    h1("12. Tablas de resultados")
    tabla(["Caudal", "L/s", "m³/día"], [["Caudal medio diario (Qmd)", n(q["qmd_lps"], 3), n(q["qmd_m3d"], 1)],
                                        ["Caudal máximo diario (QMD)", n(q["QMD_lps"], 3), n(q["QMD_m3d"], 1)],
                                        ["Caudal máximo horario (QMH)", n(q["QMH_lps"], 3), n(q["QMH_m3d"], 1)]])
    if res["caudales_etapas"]:
        h2("Caudales por etapa")
        tabla(["Etapa", "Años", "Población servida", "Qmd (L/s)", "QMD (L/s)", "QMH (L/s)"],
              [[e["nombre"], f"{e['anio_inicio']}–{e['anio_fin']}", n0(e["poblacion_servida"]), n(e["qmd_lps"], 3),
                n(e["QMD_lps"], 3), n(e["QMH_lps"], 3)] for e in res["caudales_etapas"]])
    p("Cumplimiento normativo de los parámetros: " + ("CUMPLE" if q["cumple_normativa"] else "NO CUMPLE — ver advertencias") + ".")

    h1("13. Gráficas de proyección")
    p("Las gráficas de proyección por localidad y del sistema se incluyen en el numeral 8.")

    h1("14. Fórmulas y desarrollo de cálculos")
    f("dbruta = dneta / (1 − %p)        (Res. 0330/2017, art. 44)")
    f("Qmd = P · dbruta / 86 400 + Σ Qad        QMD = K1 · Qmd        QMH = K2 · QMD        (art. 47)")
    p("El libro Excel adjunto conserva estas fórmulas en celdas para su revisión.")

    h1("15. Referencias técnicas y normativas")
    for r in ["Ministerio de Vivienda, Ciudad y Territorio. Resolución 0330 de 2017 — Reglamento Técnico del Sector de Agua "
              "Potable y Saneamiento Básico (RAS), y sus modificaciones (Resolución 0799 de 2021).",
              "Ministerio de Vivienda, Ciudad y Territorio. Resolución 0844 de 2018 — Requisitos técnicos para proyectos de agua y "
              "saneamiento básico de zonas rurales (esquemas diferenciales).",
              "DANE. Censo Nacional de Población y Vivienda 2018; Censo General 2005; Censo 1993.",
              "DANE. Proyecciones y retroproyecciones de población municipal por área, base CNPV 2018.",
              "DANE. Marco Geoestadístico Nacional (MGN) y DIVIPOLA.",
              "MinDesarrollo / MAVDT. RAS-2000, Título B (guía técnica de métodos de proyección; no vinculante tras la Res. 0330/2017)."]:
        p("• " + r)

    h1("16. Limitaciones y supuestos")
    for x in res["limitaciones"]:
        p("• " + x)
    h2("Advertencias del cálculo")
    for a in res["advertencias"]:
        B.append(("aviso_suave", f"[{a['nivel'].upper()}] {a['mensaje']}"))
    return B
