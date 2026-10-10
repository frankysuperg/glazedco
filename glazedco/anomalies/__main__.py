"""Detecta alertas sobre cleaning y compara WAPE sin sustituir pronósticos vigentes."""

import argparse
from dataclasses import replace
from pathlib import Path

from glazedco.db import DatabaseError
from glazedco.forecast.__main__ import NEEDED, load_clean
from glazedco.forecast.evaluate import evaluate_forecasts
from glazedco.forecast.prepare import weekly_demand

from .config import load_config
from .context import add_context
from .detect import detect_anomalies
from .report import render_impact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--adjustment", choices=["excluir", "suavizar"], default="suavizar")
    parser.add_argument("--horizon", type=int, default=4)
    parser.add_argument("--splits", type=int, default=4)
    args = parser.parse_args()
    if args.horizon < 1 or args.splits < 2:
        parser.error("--horizon >= 1 y --splits >= 2")
    config = replace(load_config(args.config), forecast_action=args.adjustment)
    try:
        tables, source = load_clean(needed=NEEDED + ("promotions", "calendar"), cache_dir=args.cache_dir)
    except DatabaseError as exc:
        print(str(exc))
        return 1
    print("Detectando anomalías diarias sobre datos limpios...", flush=True)
    alerts = add_context(detect_anomalies(tables["sales_daily"], config), tables)
    root = Path(__file__).resolve().parents[2]
    output = root / "reports"
    output.mkdir(parents=True, exist_ok=True)
    alerts.to_csv(output / "anomalies.csv", index=False)
    print(f"Alertas: {len(alerts)}. Evaluando pronóstico sin ajuste...", flush=True)
    panel = weekly_demand(tables["sales_daily"], tables["inventory_daily"], tables["products"], tables["stores"], tables["store_closures"])
    baseline = evaluate_forecasts(panel, horizon=args.horizon, n_splits=args.splits)
    print(f"Evaluando pronóstico con {args.adjustment}...", flush=True)
    adjusted = evaluate_forecasts(panel, horizon=args.horizon, n_splits=args.splits, anomaly_config=config)
    report = output / "anomaly_impact.md"
    report.write_text(render_impact(baseline, adjusted, alerts, panel, config, source), encoding="utf-8")
    print(f"Reporte: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
