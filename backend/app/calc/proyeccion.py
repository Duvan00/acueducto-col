"""Proyección de población.

Métodos implementados (formulación clásica empleada en la práctica colombiana
y descrita en el RAS-2000 Título B, que hoy es guía técnica no vinculante; la
Res. 0330/2017 exige proyectar la población dentro del período de diseño pero
no impone un método único):

    Aritmético   Pf = Pb + ka·(Tf − Tb)          ka = (P2 − P1)/(T2 − T1)
    Geométrico   Pf = Pb·(1 + r)^(Tf − Tb)       r  = (P2/P1)^(1/(T2 − T1)) − 1
    Exponencial  Pf = Pb·e^(k·(Tf − Tb))         k  = (ln P2 − ln P1)/(T2 − T1)
    DANE         valor oficial publicado para el año; fuera del rango publicado
                 se extrapola geométricamente y el valor se marca como
                 estimación técnica (nunca como oficial).

Pb y Tb son la población y el año base elegidos por el usuario (por defecto
el último censo). Las tasas se calculan entre pares consecutivos de censos
comparables.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

METODOS = ("aritmetico", "geometrico", "exponencial", "dane_oficial")
NOMBRE_METODO = {
    "aritmetico": "Aritmético",
    "geometrico": "Geométrico",
    "exponencial": "Exponencial",
    "dane_oficial": "Proyección oficial DANE",
}


@dataclass
class PuntoCensal:
    anio: int
    poblacion: float
    fuente: str = ""
    clasificacion: str = "censo_oficial_dane"
    comparable: bool = True
    nota: str = ""


@dataclass
class Advertencia:
    nivel: str      # 'info' | 'advertencia' | 'critica'
    codigo: str
    mensaje: str


@dataclass
class ResultadoProyeccion:
    metodo: str
    anio_base: int
    poblacion_base: float
    tasa: float | None
    unidad_tasa: str
    serie: list[dict] = field(default_factory=list)   # {anio, poblacion, clasificacion}
    advertencias: list[Advertencia] = field(default_factory=list)

    def poblacion_en(self, anio: int) -> float | None:
        for p in self.serie:
            if p["anio"] == anio:
                return p["poblacion"]
        return None

    def as_dict(self):
        d = asdict(self)
        d["nombre"] = NOMBRE_METODO[self.metodo]
        return d


# ---------------------------------------------------------------------------
# Tasas intercensales
# ---------------------------------------------------------------------------

def tasas_intercensales(censos: list[PuntoCensal]) -> list[dict]:
    """Tasas entre pares consecutivos de censos ordenados por año."""
    pts = sorted(censos, key=lambda c: c.anio)
    out = []
    for a, b in zip(pts, pts[1:]):
        dt = b.anio - a.anio
        if dt <= 0 or a.poblacion <= 0 or b.poblacion <= 0:
            continue
        out.append({
            "periodo": f"{a.anio}–{b.anio}",
            "anio_inicial": a.anio, "anio_final": b.anio,
            "p_inicial": a.poblacion, "p_final": b.poblacion,
            "aritmetica": (b.poblacion - a.poblacion) / dt,
            "geometrica": (b.poblacion / a.poblacion) ** (1 / dt) - 1,
            "exponencial": (math.log(b.poblacion) - math.log(a.poblacion)) / dt,
            "comparable": a.comparable and b.comparable,
            "nota": "; ".join(n for n in (a.nota, b.nota) if n),
        })
    return out


_CLAVE_TASA = {"aritmetico": "aritmetica", "geometrico": "geometrica", "exponencial": "exponencial"}
UNIDAD_TASA = {"aritmetico": "hab/año", "geometrico": "1/año", "exponencial": "1/año"}


def tasa_adoptada(tasas: list[dict], metodo: str, criterio: str = "promedio") -> float | None:
    clave = _CLAVE_TASA[metodo]
    validas = [t for t in tasas if t["comparable"]]
    if not validas:
        return None
    if criterio == "ultimo_periodo":
        return validas[-1][clave]
    return sum(t[clave] for t in validas) / len(validas)


# ---------------------------------------------------------------------------
# Proyección
# ---------------------------------------------------------------------------

def _formula(metodo: str, pb: float, tasa: float, dt: float) -> float:
    if metodo == "aritmetico":
        return pb + tasa * dt
    if metodo == "geometrico":
        return pb * (1 + tasa) ** dt
    if metodo == "exponencial":
        return pb * math.exp(tasa * dt)
    raise ValueError(metodo)


def proyectar(
    metodo: str,
    censos: list[PuntoCensal],
    anio_base: int | None = None,
    poblacion_base: float | None = None,
    anio_final: int | None = None,
    anio_inicial: int | None = None,
    tasa_manual: float | None = None,
    criterio_tasa: str = "promedio",
    serie_dane: dict[int, float] | None = None,
) -> ResultadoProyeccion:
    if metodo not in METODOS:
        raise ValueError(f"Método desconocido: {metodo}")
    censos = sorted(censos, key=lambda c: c.anio)
    adv: list[Advertencia] = []

    if anio_base is None:
        if not censos:
            raise ValueError("Se requiere población base o al menos un censo")
        anio_base = censos[-1].anio
    if poblacion_base is None:
        match = [c for c in censos if c.anio == anio_base]
        if match:
            poblacion_base = match[0].poblacion
        elif metodo == "dane_oficial" and serie_dane and anio_base in serie_dane:
            poblacion_base = serie_dane[anio_base]
        else:
            raise ValueError(f"No hay población para el año base {anio_base}; ingrésela con su soporte.")
    anio_final = anio_final or anio_base + 25
    anio_inicial = anio_inicial or anio_base
    anios = range(min(anio_inicial, anio_base), anio_final + 1)

    if metodo == "dane_oficial":
        return _proyeccion_dane(serie_dane or {}, anio_base, poblacion_base, anios, adv)

    tasas = tasas_intercensales(censos)
    if any(not t["comparable"] for t in tasas):
        adv.append(Advertencia("advertencia", "NO_COMPARABLE",
            "Hay períodos intercensales marcados como no comparables (cambios territoriales, "
            "segregación de municipios o diferencias de cobertura); se excluyeron del cálculo de la tasa."))
    if tasa_manual is not None:
        tasa = tasa_manual
        adv.append(Advertencia("info", "TASA_MANUAL",
            "Se adoptó una tasa definida por el usuario; debe quedar sustentada en la memoria."))
    else:
        tasa = tasa_adoptada(tasas, metodo, criterio_tasa)
        if tasa is None:
            raise ValueError("Se requieren al menos dos censos comparables o una tasa manual.")
    if metodo == "exponencial" and len([t for t in tasas if t["comparable"]]) < 2 and tasa_manual is None:
        adv.append(Advertencia("advertencia", "POCOS_CENSOS_EXP",
            "El método exponencial con un solo período intercensal equivale al geométrico continuo; "
            "su uso suele requerir al menos tres censos."))
    if any(t[_CLAVE_TASA[metodo]] < 0 for t in tasas):
        adv.append(Advertencia("advertencia", "TASA_NEGATIVA_PERIODO",
            "Al menos un período intercensal presenta decrecimiento poblacional."))
    if tasa < 0:
        adv.append(Advertencia("critica", "TASA_NEGATIVA",
            "La tasa adoptada es negativa: la población proyectada decrece. No adopte una población de "
            "diseño inferior a la actual sin justificar (migración, información local, serie DANE)."))
    if metodo != "aritmetico" and tasa > 0.05:
        adv.append(Advertencia("advertencia", "TASA_ALTA",
            f"Tasa de {tasa*100:.2f} %/año: inusualmente alta; verifique cambios de cobertura censal o territoriales."))

    serie = []
    for t in anios:
        p = _formula(metodo, poblacion_base, tasa, t - anio_base)
        serie.append({"anio": t, "poblacion": max(p, 0.0), "clasificacion": "estimacion_tecnica"})
    return ResultadoProyeccion(metodo, anio_base, poblacion_base, tasa, UNIDAD_TASA[metodo], serie, adv)


def _proyeccion_dane(serie_dane, anio_base, poblacion_base, anios, adv) -> ResultadoProyeccion:
    if not serie_dane:
        raise ValueError("No hay proyección oficial del DANE cargada para esta unidad territorial.")
    ys = sorted(serie_dane)
    y0, y1 = ys[0], ys[-1]
    # Tasa geométrica de los últimos 5 años publicados, para extrapolar.
    ya = max(y0, y1 - 5)
    r = (serie_dane[y1] / serie_dane[ya]) ** (1 / (y1 - ya)) - 1 if y1 > ya and serie_dane[ya] > 0 else 0.0
    serie, extrapolado = [], False
    for t in anios:
        if t in serie_dane:
            serie.append({"anio": t, "poblacion": serie_dane[t], "clasificacion": "proyeccion_oficial_dane"})
        elif t > y1:
            extrapolado = True
            serie.append({"anio": t, "poblacion": serie_dane[y1] * (1 + r) ** (t - y1),
                          "clasificacion": "estimacion_tecnica"})
        else:
            serie.append({"anio": t, "poblacion": None, "clasificacion": "sin_dato"})
    if extrapolado:
        adv.append(Advertencia("advertencia", "DANE_EXTRAPOLADO",
            f"La serie oficial del DANE llega a {y1}. Los años posteriores se extrapolaron con la tasa geométrica "
            f"{ya}–{y1} ({r*100:.3f} %/año) y se clasifican como estimación técnica, no como dato oficial."))
    if any(p["poblacion"] is None for p in serie):
        adv.append(Advertencia("advertencia", "DANE_INCOMPLETO", "Hay años sin valor oficial en el rango solicitado."))
    if r < 0:
        adv.append(Advertencia("critica", "TASA_NEGATIVA",
            "La proyección oficial del DANE es decreciente al final de la serie."))
    return ResultadoProyeccion("dane_oficial", anio_base, poblacion_base, r, "1/año (extrapolación)", serie, adv)


# ---------------------------------------------------------------------------
# Comparación y justificación
# ---------------------------------------------------------------------------

def comparar_metodos(censos, anio_base=None, poblacion_base=None, anio_final=None,
                     criterio_tasa="promedio", serie_dane=None, anio_inicial=None) -> dict:
    resultados: dict[str, ResultadoProyeccion] = {}
    errores: dict[str, str] = {}
    for m in METODOS:
        try:
            resultados[m] = proyectar(m, censos, anio_base, poblacion_base, anio_final, anio_inicial,
                                      criterio_tasa=criterio_tasa, serie_dane=serie_dane)
        except ValueError as e:
            errores[m] = str(e)
    return {"resultados": resultados, "errores": errores,
            "recomendacion": recomendar_metodo(resultados, censos, serie_dane)}


def recomendar_metodo(resultados: dict[str, ResultadoProyeccion], censos, serie_dane) -> dict:
    """Sugerencia razonada. La decisión final es del diseñador y debe quedar justificada."""
    criterios = []
    calc = {m: r for m, r in resultados.items() if m != "dane_oficial"}
    if not calc:
        return {"metodo": "dane_oficial" if "dane_oficial" in resultados else None,
                "criterios": ["Sin censos comparables suficientes para métodos matemáticos."]}

    finales = {m: r.serie[-1]["poblacion"] for m, r in calc.items()}
    dispersion = (max(finales.values()) - min(finales.values())) / max(min(finales.values()), 1)
    criterios.append(f"Dispersión entre métodos al horizonte: {dispersion*100:.1f} %.")

    # 1) Contraste con la serie oficial del DANE en los años en que coexisten.
    rmse = {}
    if serie_dane:
        for m, r in calc.items():
            pares = [(p["poblacion"], serie_dane[p["anio"]]) for p in r.serie
                     if p["anio"] in serie_dane and p["anio"] > r.anio_base]
            if pares:
                rmse[m] = math.sqrt(sum((a - b) ** 2 for a, b in pares) / len(pares))
    if rmse:
        mejor = min(rmse, key=rmse.get)
        criterios.append("Error cuadrático medio frente a la proyección oficial DANE: " +
                         ", ".join(f"{NOMBRE_METODO[m]} {v:,.0f} hab" for m, v in rmse.items()) + ".")
        criterios.append(f"Se sugiere {NOMBRE_METODO[mejor]} por ser el más consistente con la serie oficial; "
                         "si la serie DANE cubre el horizonte, puede adoptarse directamente.")
        return {"metodo": mejor, "criterios": criterios, "rmse_dane": rmse}

    # 2) Sin serie DANE: criterio conservador según comportamiento histórico.
    tasas = tasas_intercensales(censos)
    g = [t["geometrica"] for t in tasas if t["comparable"]]
    if g and all(x > 0 for x in g):
        mejor = "geometrico"
        criterios.append("Crecimiento positivo en todos los períodos: el método geométrico representa "
                         "crecimiento a tasa constante, usual en localidades con dinámica activa.")
    elif g and all(x <= 0 for x in g):
        mejor = "aritmetico"
        criterios.append("Decrecimiento sostenido: se sugiere el aritmético (variación lineal) y no adoptar "
                         "una población de diseño menor a la actual sin sustento local.")
    else:
        mejor = "aritmetico"
        criterios.append("Comportamiento irregular entre censos: se sugiere el aritmético por ser menos "
                         "sensible a tasas extremas; complemente con información local.")
    if mejor not in calc:
        mejor = next(iter(calc))
    return {"metodo": mejor, "criterios": criterios}


def justificacion_texto(metodo: str, resultado: ResultadoProyeccion, recomendacion: dict,
                        justificacion_usuario: str = "") -> str:
    partes = [f"Método adoptado: {NOMBRE_METODO[metodo]}."]
    if recomendacion.get("metodo") == metodo:
        partes.append("Coincide con el método sugerido por la herramienta.")
    elif recomendacion.get("metodo"):
        partes.append(f"La herramienta sugería {NOMBRE_METODO[recomendacion['metodo']]}; "
                      "el diseñador adoptó otro método (ver justificación).")
    partes += recomendacion.get("criterios", [])
    if justificacion_usuario:
        partes.append("Justificación del diseñador: " + justificacion_usuario)
    return " ".join(partes)
