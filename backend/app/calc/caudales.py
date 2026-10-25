"""Cálculo de dotaciones y caudales de diseño (Res. 0330/2017, arts. 43, 44 y 47).

    dbruta = dneta / (1 − %p)                         (art. 44)
    Qmd    = P · dbruta / 86 400            [L/s]     (art. 47)
    QMD    = K1 · Qmd                                  (art. 47)
    QMH    = K2 · QMD                                  (art. 47)

Las demandas adicionales (no residenciales) se suman en L/s. Cada una declara
si ya incluye pérdidas, si se mayora con K1/K2 y si ya está contenida en la
dotación residencial (en cuyo caso se excluye para no duplicarla).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import normativa

SEG_DIA = 86_400


@dataclass
class DemandaAdicional:
    tipo: str
    caudal_lps: float
    justificacion: str
    descripcion: str = ""
    afecta_k1: bool = True
    afecta_k2: bool = True
    incluye_perdidas: bool = False
    incluida_en_dotacion: bool = False


@dataclass
class EntradaCaudales:
    poblacion: float
    altitud_msnm: float | None
    dotacion_neta: float | None = None          # None → se adopta la máxima normativa
    perdidas_pct: float = 25.0
    k1: float | None = None                     # None → máximo normativo (debe justificarse)
    k2: float | None = None
    k1_por_macromedicion: bool = False
    k2_por_macromedicion: bool = False
    regimen: str = "general"                    # 'general' | 'rural_esquema_diferencial'
    demandas: list[DemandaAdicional] = field(default_factory=list)
    justificaciones: dict = field(default_factory=dict)


def _paso(nombre, simbolo, valor, unidad, formula="", sustitucion="", fundamento="", origen=""):
    return dict(nombre=nombre, simbolo=simbolo, valor=valor, unidad=unidad, formula=formula,
                sustitucion=sustitucion, fundamento=fundamento, origen=origen)


def calcular(e: EntradaCaudales, params: list[dict] | None = None) -> dict:
    params = params if params is not None else normativa.cargar()
    adv, pasos = [], []

    def aviso(nivel, codigo, msg):
        adv.append({"nivel": nivel, "codigo": codigo, "mensaje": msg})

    if e.poblacion <= 0:
        raise ValueError("La población de diseño debe ser positiva.")
    P = e.poblacion
    pasos.append(_paso("Población de diseño", "P", P, "hab",
                       fundamento="Resultado del módulo de proyección (horizonte de diseño).",
                       origen="estimacion_tecnica"))

    # --- Dotación neta (art. 43) ---------------------------------------
    p_max = normativa.buscar(params, "dotacion_neta_max", altitud=e.altitud_msnm)
    dmax = p_max["valor"] if p_max else None
    if e.altitud_msnm is None:
        aviso("critica", "SIN_ALTITUD", "No se indicó la altitud promedio de la zona atendida: no es posible "
              "verificar la dotación neta máxima del art. 43.")
    if e.dotacion_neta is None:
        if dmax is None:
            raise ValueError("Indique la dotación neta o la altitud de la zona atendida.")
        dneta, origen_d = dmax, "maximo_normativo"
        aviso("advertencia", "DOTACION_MAXIMA", "Se adoptó la dotación neta máxima normativa. La norma la fija "
              "como tope: sustente con consumos medidos (SUI, facturación, micromedición) si son menores.")
    else:
        dneta, origen_d = e.dotacion_neta, "usuario"
    if dmax is not None and dneta > dmax and e.regimen == "general":
        aviso("critica", "DOTACION_EXCEDE", f"La dotación neta ({dneta} L/hab·día) supera la máxima del art. 43 "
              f"para {e.altitud_msnm} m s.n.m. ({dmax} L/hab·día).")
    if e.regimen == "rural_esquema_diferencial":
        pmin = normativa.buscar(params, "dotacion_neta_rural_min", regimen=e.regimen)
        pmx = normativa.buscar(params, "dotacion_neta_rural_max", regimen=e.regimen)
        if pmin and pmx and not (pmin["valor"] <= dneta <= pmx["valor"]):
            aviso("critica", "DOTACION_RURAL_RANGO", f"Dotación fuera del rango {pmin['valor']}–{pmx['valor']} "
                  "L/hab·día de la Res. 0844/2018 (esquemas diferenciales rurales).")
        aviso("info", "REGIMEN_RURAL", "Régimen rural de esquema diferencial (Res. 0844/2018): parámetro pendiente "
              "de verificación del texto oficial.")
    pasos.append(_paso("Dotación neta", "dneta", dneta, "L/hab·día",
                       fundamento=normativa.referencia(p_max) + (f" — máximo {dmax} L/hab·día" if dmax else ""),
                       sustitucion=e.justificaciones.get("dotacion_neta", ""), origen=origen_d))

    # --- Dotación bruta (art. 44) --------------------------------------
    p_perd = normativa.buscar(params, "perdidas_max")
    pct = e.perdidas_pct
    if not 0 <= pct < 100:
        raise ValueError("El porcentaje de pérdidas debe estar entre 0 y 100.")
    if p_perd and pct > p_perd["valor"]:
        aviso("critica", "PERDIDAS_EXCEDE", f"Pérdidas de {pct} % superan el máximo de {p_perd['valor']} % (art. 44).")
    dbruta = dneta / (1 - pct / 100)
    pasos.append(_paso("Dotación bruta", "dbruta", dbruta, "L/hab·día", "dbruta = dneta / (1 − %p)",
                       f"{dneta:g} / (1 − {pct/100:g}) = {dbruta:.2f}", normativa.referencia(p_perd)))

    # --- Caudal medio diario --------------------------------------------
    qmd_res = P * dbruta / SEG_DIA
    pasos.append(_paso("Caudal medio diario residencial", "Qmd,res", qmd_res, "L/s", "Qmd = P · dbruta / 86400",
                       f"{P:,.0f} × {dbruta:.2f} / 86400 = {qmd_res:.3f}", "Res. 0330/2017, art. 47"))

    # Demandas adicionales
    incluidas, excluidas = [], []
    for d in e.demandas:
        if d.caudal_lps < 0:
            raise ValueError("Caudal de demanda adicional negativo")
        if not d.justificacion.strip():
            aviso("advertencia", "DEMANDA_SIN_JUSTIFICACION", f"La demanda '{d.tipo}' no tiene justificación.")
        if d.incluida_en_dotacion:
            excluidas.append(d)
            continue
        q_b = d.caudal_lps if d.incluye_perdidas else d.caudal_lps / (1 - pct / 100)
        incluidas.append((d, q_b))
    if excluidas:
        aviso("info", "DEMANDA_EXCLUIDA", "Se excluyeron por estar contenidas en la dotación residencial: "
              + ", ".join(d.tipo for d in excluidas) + ".")
    tipos = [d.tipo.strip().lower() for d, _ in incluidas]
    if len(tipos) != len(set(tipos)):
        aviso("advertencia", "DEMANDA_DUPLICADA", "Hay demandas adicionales con el mismo tipo; verifique duplicidad.")
    q_ad = sum(q for _, q in incluidas)
    qmd = qmd_res + q_ad
    pasos.append(_paso("Demandas adicionales (brutas)", "Qad", q_ad, "L/s", "Σ Qad,i / (1 − %p) si no incluyen pérdidas",
                       " + ".join(f"{q:.3f}" for _, q in incluidas) or "0"))
    pasos.append(_paso("Caudal medio diario", "Qmd", qmd, "L/s", "Qmd = Qmd,res + Qad", f"{qmd_res:.3f} + {q_ad:.3f}"))

    # --- Coeficientes (art. 47) -----------------------------------------
    p_k1 = normativa.buscar(params, "k1_max", poblacion=P)
    p_k2 = normativa.buscar(params, "k2_max", poblacion=P)
    k1 = e.k1 if e.k1 is not None else p_k1["valor"]
    k2 = e.k2 if e.k2 is not None else p_k2["valor"]
    for nombre, k, pk, medido in (("K1", k1, p_k1, e.k1_por_macromedicion), ("K2", k2, p_k2, e.k2_por_macromedicion)):
        if k < 1:
            aviso("critica", f"{nombre}_MENOR_1", f"{nombre} = {k} < 1 no es físicamente consistente.")
        if pk and k > pk["valor"]:
            aviso("critica", f"{nombre}_EXCEDE", f"{nombre} = {k} supera el máximo de {pk['valor']} (art. 47, "
                  f"población {'≤' if P <= 12500 else '>'} 12.500 hab).")
        if not medido:
            aviso("advertencia", f"{nombre}_SIN_MEDICION", f"{nombre} no proviene de registros de macromedición; "
                  "el art. 47 exige justificar su selección.")

    q_k1 = sum(q * (k1 if d.afecta_k1 else 1) for d, q in incluidas)
    QMD = k1 * qmd_res + q_k1
    q_k2 = sum(q * (k1 if d.afecta_k1 else 1) * (k2 if d.afecta_k2 else 1) for d, q in incluidas)
    QMH = k2 * k1 * qmd_res + q_k2
    pasos.append(_paso("Coeficiente de consumo máximo diario", "K1", k1, "-",
                       fundamento=normativa.referencia(p_k1) + f" — máximo {p_k1['valor']}",
                       sustitucion=e.justificaciones.get("k1", ""),
                       origen="macromedicion" if e.k1_por_macromedicion else ("usuario" if e.k1 else "maximo_normativo")))
    pasos.append(_paso("Caudal máximo diario", "QMD", QMD, "L/s", "QMD = K1 · Qmd",
                       f"{k1:g} × {qmd_res:.3f} + {q_k1:.3f} = {QMD:.3f}", "Res. 0330/2017, art. 47"))
    pasos.append(_paso("Coeficiente de consumo máximo horario", "K2", k2, "-",
                       fundamento=normativa.referencia(p_k2) + f" — máximo {p_k2['valor']}",
                       sustitucion=e.justificaciones.get("k2", ""),
                       origen="macromedicion" if e.k2_por_macromedicion else ("usuario" if e.k2 else "maximo_normativo")))
    pasos.append(_paso("Caudal máximo horario", "QMH", QMH, "L/s", "QMH = K2 · QMD",
                       f"{k2:g} × {k1 * qmd_res:.3f} + {q_k2:.3f} = {QMH:.3f}", "Res. 0330/2017, art. 47"))

    m3 = lambda q: q * SEG_DIA / 1000
    return {
        "poblacion": P, "altitud_msnm": e.altitud_msnm, "dotacion_neta": dneta, "dotacion_neta_max": dmax, "perdidas_pct": pct,
        "dotacion_bruta": dbruta, "k1": k1, "k2": k2,
        "qmd_residencial_lps": qmd_res, "demandas_adicionales_lps": q_ad,
        "qmd_lps": qmd, "qmd_m3d": m3(qmd),
        "QMD_lps": QMD, "QMD_m3d": m3(QMD),
        "QMH_lps": QMH, "QMH_m3d": m3(QMH),
        "demandas": [{"tipo": d.tipo, "descripcion": d.descripcion, "caudal_lps": d.caudal_lps, "caudal_bruto_lps": q,
                      "afecta_k1": d.afecta_k1, "afecta_k2": d.afecta_k2, "incluye_perdidas": d.incluye_perdidas,
                      "justificacion": d.justificacion} for d, q in incluidas],
        "demandas_excluidas": [d.tipo for d in excluidas],
        "pasos": pasos, "advertencias": adv,
        "cumple_normativa": not any(a["nivel"] == "critica" and a["codigo"].endswith(("EXCEDE", "RANGO", "MENOR_1"))
                                    for a in adv),
    }
