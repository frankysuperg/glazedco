"""Referencia robusta causal: el punto actual nunca participa en su ventana."""

import numpy as np
import pandas as pd


def robust_scores(
    values: pd.Series, *, window_days: int, min_history: int,
    threshold: float, scale_floor: float,
) -> pd.DataFrame:
    """Serie ordenada con DatetimeIndex; puntaje firmado en escalas MAD.

    Escala=max(1.4826*MAD, piso). MAD se calcula alrededor de la mediana de
    cada ventana, no de medianas distintas. Nulos no cuentan como ceros.
    """
    values = pd.to_numeric(values, errors="coerce").astype(float).replace([np.inf, -np.inf], np.nan)
    window = values.rolling(f"{window_days}D", closed="left", min_periods=min_history)
    expected = window.median()
    mad = window.apply(lambda x: np.nanmedian(np.abs(x - np.nanmedian(x))), raw=True)
    scale = (1.4826 * mad).clip(lower=scale_floor)
    score = (values - expected) / scale
    return pd.DataFrame({"valor_observado": values, "valor_esperado": expected,
                         "puntaje": score, "anomalia": score.abs().gt(threshold)})
