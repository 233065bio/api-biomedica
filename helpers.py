import math as _math


# ─────────────────────────────────────────────
# HELPER: convertir timedelta a string HH:MM:SS
# ─────────────────────────────────────────────
def timedelta_a_str(valor):
    if valor is None:
        return None
    if hasattr(valor, 'total_seconds'):
        total = int(valor.total_seconds())
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(valor)


# ─────────────────────────────────────────────
# SEÑALES: limpieza y construcción respiratoria
# ─────────────────────────────────────────────
def limpiar_outliers_ecg(timestamps, valores):
    """Clip outliers del ECG usando IQR x5, preservando longitud."""
    if len(valores) < 10:
        return timestamps, valores
    sorted_v = sorted(valores)
    n = len(sorted_v)
    q1 = sorted_v[n // 4]
    q3 = sorted_v[(3 * n) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return timestamps, valores
    mediana = sorted_v[n // 2]
    lim_inf = mediana - 5 * iqr
    lim_sup = mediana + 5 * iqr
    valores_limpios = [max(lim_inf, min(lim_sup, v)) for v in valores]
    return timestamps, valores_limpios


def construir_resp_desde_streaming(ts_flujo, vs_flujo, ts_accz, vs_accz):
    """
    Construye la señal respiratoria:
    - Si hay flujo: usa flujo como base con suavizado gaussiano (sin reinterpolación forzada)
    - Si solo acce_z: usa acce_z suavizado
    - Si ambas: combina 70/30 en la timeline del flujo
    - Fallback: senoidal
    """

    def media_movil_gauss(valores, ventana):
        if len(valores) <= ventana:
            return valores[:]
        result = valores[:]
        for _ in range(3):
            nuevo = []
            for i in range(len(result)):
                inicio = max(0, i - ventana // 2)
                fin = min(len(result), i + ventana // 2 + 1)
                nuevo.append(sum(result[inicio:fin]) / (fin - inicio))
            result = nuevo
        return result

    def normalizar_al_rango(valores, ref_min, ref_max):
        if not valores:
            return valores
        v_min = min(valores)
        v_max = max(valores)
        if v_max == v_min:
            return [(ref_min + ref_max) / 2] * len(valores)
        escala = (ref_max - ref_min) / (v_max - v_min)
        return [ref_min + (v - v_min) * escala for v in valores]

    def interp_en_ts(ts_src, vs_src, ts_dest):
        resultado = []
        j = 0
        for t in ts_dest:
            if t <= ts_src[0]:
                resultado.append(vs_src[0])
                continue
            if t >= ts_src[-1]:
                resultado.append(vs_src[-1])
                continue
            while j < len(ts_src) - 2 and ts_src[j + 1] < t:
                j += 1
            t_a, t_b = ts_src[j], ts_src[min(j + 1, len(ts_src) - 1)]
            v_a, v_b = vs_src[j], vs_src[min(j + 1, len(vs_src) - 1)]
            frac = (t - t_a) / (t_b - t_a) if t_b != t_a else 0.0
            resultado.append(v_a + frac * (v_b - v_a))
        return resultado

    tiene_flujo = len(ts_flujo) > 1
    tiene_accz  = len(ts_accz) > 1

    # ── Caso A: Solo flujo — suavizar SIN reinterpolación forzada ──
    if tiene_flujo and not tiene_accz:
        n = len(vs_flujo)
        ventana = max(5, n // 15)
        vs_s = media_movil_gauss(vs_flujo, ventana)
        t0 = ts_flujo[0]
        return [t - t0 for t in ts_flujo], [round(v, 3) for v in vs_s]

    # ── Caso B: Solo acce_z ──
    if tiene_accz and not tiene_flujo:
        n = len(vs_accz)
        ventana = max(5, n // 15)
        vs_s = media_movil_gauss(vs_accz, ventana)
        t0 = ts_accz[0]
        return [t - t0 for t in ts_accz], [round(v, 3) for v in vs_s]

    # ── Caso C: Ambas — combinar usando timestamps del flujo como base ──
    if tiene_flujo and tiene_accz:
        vs_accz_i = interp_en_ts(ts_accz, vs_accz, ts_flujo)
        f_min = min(vs_flujo)
        f_max = max(vs_flujo)
        vs_accz_norm = normalizar_al_rango(vs_accz_i, f_min, f_max)
        vs_comb = [0.70 * f + 0.30 * a for f, a in zip(vs_flujo, vs_accz_norm)]
        n = len(vs_comb)
        ventana = max(5, n // 15)
        vs_s = media_movil_gauss(vs_comb, ventana)
        t0 = ts_flujo[0]
        return [t - t0 for t in ts_flujo], [round(v, 3) for v in vs_s]

    # ── Fallback: senoidal ──
    duracion_ms = 20000
    N = 500
    ts_out = [int(i * duracion_ms / (N - 1)) for i in range(N)]
    vs_out = [round(155 + 30 * _math.sin(2 * _math.pi * 0.25 * t / 1000.0), 3) for t in ts_out]
    return ts_out, vs_out
