"""Estimaciones monetarias anexas; no modifica riesgos, pedidos ni presupuestos."""

import math

import pandas as pd

from glazedco.replenishment.config import ReplenishmentConfig
from glazedco.replenishment.demand import daily_rates_from_forecast, demand_during_days
from glazedco.replenishment.lead_time import estimate_supplier_lead_times
from .enrichment import EnrichmentConfig, finite
from .models import EstimatedImpact

STOCKOUT = "Demanda esperada sin cobertura hasta la reposición × (Σ net_sales / Σ units_sold − unit_cost)"
EXCESS = "Capital inmovilizado: max(existencias + en tránsito − demanda de cobertura objetivo, 0) × unit_cost"


class ImpactEstimator:
    def __init__(self, tables: dict[str, pd.DataFrame], forecast: pd.DataFrame,
                 policy: ReplenishmentConfig, config: EnrichmentConfig):
        self.tables, self.policy, self.config = tables, policy, config
        self.cut = pd.Timestamp(policy.as_of_date)
        self.products = {key: group for key, group in tables["products"].groupby("sku")}
        inventory = tables["inventory_daily"]
        inventory = inventory.loc[pd.to_datetime(inventory.date).eq(self.cut)]
        self.inventory = dict(tuple(inventory.groupby(["store_id", "sku"])))
        sales = tables["sales_daily"]
        sales = sales.loc[pd.to_datetime(sales.date).between(self.cut - pd.Timedelta(days=config.price_days - 1), self.cut)]
        self.sales = dict(tuple(sales.groupby(["store_id", "sku"])))
        self.forecasts = dict(tuple(forecast.groupby(["store_id", "sku"])))
        self.leads: dict[str, pd.DataFrame] = {}

    def lead(self, store: str, supplier: str) -> float | None:
        if store not in self.leads:
            orders = self.tables["purchase_orders"]
            orders = orders.loc[orders.store_id.eq(store)
                                & pd.to_datetime(orders.order_date).le(self.cut)
                                & pd.to_datetime(orders.received_date).le(self.cut)]
            self.leads[store] = estimate_supplier_lead_times(orders, self.tables["suppliers"], self.policy)
        selected = self.leads[store].loc[self.leads[store].supplier_id.eq(supplier)]
        if len(selected) != 1 or selected.iloc[0].lead_time_source == "default_config":
            return None
        value = finite(selected.iloc[0].lead_time_days)
        return value if value is not None and value > 0 else None

    def estimate(self, store: str, sku: str, kind: str) -> EstimatedImpact:
        method = STOCKOUT if kind == "quiebre_stock" else EXCESS
        def missing(reason: str) -> EstimatedImpact:
            return EstimatedImpact(impacto_usd_estimado=None, metodo=method, motivo=reason)
        product = self.products.get(sku)
        if product is None or len(product) != 1:
            return missing("Producto ausente o ambiguo.")
        product = product.iloc[0]
        cost = finite(product.get("unit_cost"))
        if cost is None or cost < 0:
            return missing("Falta unit_cost válido; no se sustituye por un costo estimado.")
        stock = self.inventory.get((store, sku))
        if stock is None or len(stock) != 1:
            return missing("Falta inventario único al corte.")
        amounts = [finite(stock.iloc[0].get(name)) for name in ("closing_qty", "on_order_qty")]
        if any(value is None or value < 0 for value in amounts):
            return missing("Existencias o unidades en tránsito ausentes o inválidas.")
        position = sum(amounts)
        piece = self.forecasts.get((store, sku))
        if piece is None or piece.empty:
            return missing("Falta pronóstico para SKU y sucursal.")
        units = pd.to_numeric(piece.yhat, errors="coerce")
        if (not units.map(lambda v: finite(v) is not None and v >= 0).all()
                or piece.horizonte.duplicated().any() or piece.semana.duplicated().any()
                or not pd.to_numeric(piece.horizonte, errors="coerce").eq(1).any()):
            return missing("Pronóstico ausente, inválido o ambiguo; no se imputan ceros.")
        daily = daily_rates_from_forecast(piece, as_of=self.cut.date(),
                                         forecast_start=pd.Timestamp(self.policy.forecast_start_date).date())
        if kind == "quiebre_stock":
            days = self.lead(store, product.get("supplier_id"))
            if days is None:
                return missing("Falta plazo de reposición observado o estándar del proveedor; no se usa el valor por defecto.")
        else:
            days = float(self.policy.cobertura_objetivo_dias)
            perishable = finite(product.get("is_perishable"))
            if perishable not in (0, 1):
                return missing("Falta indicador de producto perecedero.")
            if perishable == 1:
                life = finite(product.get("shelf_life_days"))
                if life is None or life <= 0:
                    return missing("Falta vida útil para limitar la cobertura objetivo.")
                days = min(days, life)
        expected_dates = pd.date_range(self.cut + pd.Timedelta(days=1), periods=math.ceil(days), freq="D")
        rates = daily.set_index(pd.to_datetime(daily.date)).daily_demand.reindex(expected_dates)
        if rates.isna().any():
            return missing("El pronóstico no cubre todos los días necesarios para la estimación.")
        expected_demand = demand_during_days(rates, days)
        if kind == "sobreinventario":
            excess = max(position - expected_demand, 0.0)
            return EstimatedImpact(impacto_usd_estimado=excess * cost, metodo=method, unidades_exceso=excess)
        sales = self.sales.get((store, sku))
        if sales is None or sales.empty:
            return missing("Faltan ventas para calcular el precio medio en la ventana al corte.")
        sold = pd.to_numeric(sales.units_sold, errors="coerce")
        revenue = pd.to_numeric(sales.net_sales, errors="coerce")
        valid = lambda values: values.map(lambda value: finite(value) is not None and value >= 0).all()
        if not valid(sold) or not valid(revenue) or sold.sum() <= 0:
            return missing("Precio medio no calculable: ventas inválidas o unidades vendidas iguales a cero.")
        margin = float(revenue.sum() / sold.sum()) - cost
        remaining = position
        at_risk_days = 0.0
        for index, rate in enumerate(rates):
            duration = min(1.0, days - index)
            demand = float(rate) * duration
            shortage = max(demand - remaining, 0.0)
            at_risk_days += shortage / rate if rate > 0 else 0.0
            remaining = max(remaining - demand, 0.0)
        at_risk_units = max(expected_demand - position, 0.0)
        return EstimatedImpact(impacto_usd_estimado=at_risk_units * margin, metodo=method,
                               dias_en_riesgo=at_risk_days, unidades_en_riesgo=at_risk_units,
                               margen_unitario=margin)
