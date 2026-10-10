"""Adaptación de resultados existentes; no entrena modelos ni cambia sus reglas."""

import json
from pathlib import Path
from threading import RLock
from typing import Protocol

import pandas as pd

from glazedco.cleaning.config import load_config as cleaning_config
from glazedco.forecast.prepare import weekly_demand
from glazedco.metrics import aggregate_margin
from glazedco.replenishment.config import ReplenishmentConfig, load_config
from glazedco.replenishment.demand import observed_weekly_means
from glazedco.replenishment.recommend import OUTPUT_COLUMNS, recommend_replenishment
from .errors import APIError
from .cache import load_tables, max_cache_age
from .models import Alert, ForecastResponse, Margin, Scope, Week


class DataSource(Protocol):
    def tables(self) -> dict[str, pd.DataFrame]: ...
    def report(self, name: str) -> pd.DataFrame: ...


class LocalSource:
    """Caché limpia existente o lectura/limpieza existente; CSV de modelos publicados."""
    def __init__(self, root: Path | None = None, *, cache_max_age_seconds: float | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[2]
        self.cache_max_age_seconds = cache_max_age_seconds

    def tables(self) -> dict[str, pd.DataFrame]:
        age = max_cache_age() if self.cache_max_age_seconds is None else self.cache_max_age_seconds
        return load_tables(self.root / "data" / "clean", age)

    def report(self, name: str) -> pd.DataFrame:
        # name es interno, no una ruta suministrada por HTTP.
        paths = {"forecast": "forecast_next_weeks.csv", "errors": "forecast_errors.csv",
                 "anomalies": "anomalies.csv"}
        return pd.read_csv(self.root / "reports" / paths[name], dtype={"store_id": str, "sku": str})


def records(frame: pd.DataFrame) -> list[dict]:
    # pandas convierte NA/NaN/inf en null y fechas en ISO, sin strings 'nan'.
    return json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))


class Dashboard:
    def __init__(self, source: DataSource | None = None, config: ReplenishmentConfig | None = None) -> None:
        self.source = source or LocalSource()
        self.config = config or load_config()
        self._tables: dict[str, pd.DataFrame] | None = None
        self._reports: dict[str, pd.DataFrame] = {}
        self._recommendations: dict[str | None, pd.DataFrame] = {}
        self._margins: dict[str | None, Margin] = {}
        self._forecasts: dict[str | None, ForecastResponse] = {}
        self._alerts: dict[str | None, list[Alert]] = {}
        self._lock = RLock()

    def warmup(self) -> None:
        """Mismos cálculos por alcance; ningún proveedor de IA se invoca al arrancar."""
        tables = self.tables()
        for name in ("forecast", "errors", "anomalies"):
            self.report(name)
        for store in [None, *tables["stores"].store_id.dropna().unique().tolist()]:
            self._margins[store] = self.margin(store)
            self._forecasts[store] = self.forecast(store)
            self._alerts[store] = self.alerts(store)

    def tables(self) -> dict[str, pd.DataFrame]:
        with self._lock:
            if self._tables is None:
                try:
                    self._tables = self.source.tables()
                except Exception:
                    raise APIError(503, "DATOS_NO_DISPONIBLES", "No se pudieron cargar los datos limpios.") from None
            return self._tables

    def report(self, name: str) -> pd.DataFrame:
        with self._lock:
            if name not in self._reports:
                try:
                    self._reports[name] = self.source.report(name)
                except Exception:
                    raise APIError(503, "REPORTE_NO_DISPONIBLE", "Falta un resultado publicado del módulo solicitado.") from None
            return self._reports[name]

    def scope(self, store: str | None) -> Scope:
        if store is None:
            return Scope()
        stores = self.tables()["stores"]
        selected = stores.loc[stores.store_id.eq(store)]
        if selected.empty:
            raise APIError(404, "SUCURSAL_NO_ENCONTRADA", "La sucursal solicitada no existe en los datos limpios.")
        return Scope(sucursal=store, vista=str(selected.store_name.iloc[0]))

    @staticmethod
    def select(frame: pd.DataFrame, store: str | None) -> pd.DataFrame:
        return frame.loc[frame.store_id.eq(store)].copy() if store is not None and "store_id" in frame else frame.copy()

    def margin(self, store: str | None) -> Margin:
        if store in self._margins:
            return self._margins[store].model_copy(deep=True)
        self.scope(store)
        sales = self.select(self.tables()["sales_daily"], store)
        return Margin.model_validate(records(aggregate_margin(sales))[0])

    def forecast(self, store: str | None) -> ForecastResponse:
        if store in self._forecasts:
            return self._forecasts[store].model_copy(deep=True)
        scope = self.scope(store)
        frame = self.select(self.report("forecast"), store)
        weeks: list[Week] = []
        for week, group in frame.groupby("semana", sort=True):
            values = pd.to_numeric(group.yhat, errors="coerce")
            weeks.append(Week(semana=pd.Timestamp(week).date(),
                              unidades=float(values.sum()) if values.notna().all() else None,
                              series=len(group), series_con_pronostico=int(values.notna().sum())))
        total = (sum(w.unidades for w in weeks) if weeks and all(w.unidades is not None for w in weeks) else None)
        return ForecastResponse(**scope.model_dump(), semanas=weeks, unidades_agregadas=total)

    def recommendations(self, store: str | None) -> pd.DataFrame:
        self.scope(store)
        with self._lock:
            if store not in self._recommendations:
                tables = {name: self.select(frame, store) for name, frame in self.tables().items()}
                forecast = self.select(self.report("forecast"), store)
                if forecast.empty:
                    self._recommendations[store] = pd.DataFrame(columns=OUTPUT_COLUMNS)
                    return self._recommendations[store].copy()
                history = {name: tables[name].loc[pd.to_datetime(tables[name].date).le(self.config.as_of_date)]
                           for name in ("sales_daily", "inventory_daily")}
                panel = weekly_demand(history["sales_daily"], history["inventory_daily"],
                                      tables["products"], tables["stores"], tables["store_closures"],
                                      closure_end_inclusive=cleaning_config().closure_end_inclusive)
                observed = observed_weekly_means(panel, self.config.as_of_date)
                self._recommendations[store] = recommend_replenishment(
                    forecast=forecast, errors=self.select(self.report("errors"), store),
                    inventory=tables["inventory_daily"], products=tables["products"],
                    purchase_orders=tables["purchase_orders"], suppliers=tables["suppliers"],
                    config=self.config, prices=tables.get("prices"), observed_demand=observed)
            return self._recommendations[store].copy()

    def alerts(self, store: str | None) -> list[Alert]:
        if store in self._alerts:
            return [alert.model_copy(deep=True) for alert in self._alerts[store]]
        result: list[Alert] = []
        severity = {"alto": "alta", "medio": "media", "desconocido": "desconocida"}
        for row in self.recommendations(store).itertuples(index=False):
            for field, kind in (("riesgo_quiebre", "quiebre_stock"), ("riesgo_exceso", "sobreinventario")):
                risk = getattr(row, field)
                if risk == "bajo":
                    continue
                if risk not in severity:
                    raise APIError(503, "RIESGO_INVALIDO", "El módulo devolvió un riesgo no reconocido.")
                result.append(Alert(store_id=row.store_id, sku=row.sku, tipo=kind,
                                    severidad=severity[risk], fecha_corte=self.config.as_of_date, motivo=row.motivo))
        return result
