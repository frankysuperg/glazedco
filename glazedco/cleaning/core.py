"""Particiones trazables y resúmenes; las funciones no mutan el DataFrame recibido."""

from dataclasses import dataclass, field
import pandas as pd

from .config import Config


@dataclass
class Rule:
    name: str
    action: str
    source_rows: list[int]

    @property
    def affected_rows(self) -> int:
        return len(self.source_rows)


@dataclass
class Summary:
    table: str
    rows_read: int
    rows_clean: int
    rows_quarantine: int
    rules: list[Rule]
    notes: dict[str, object] = field(default_factory=dict)


Result = tuple[pd.DataFrame, pd.DataFrame, Summary]


class Batch:
    def __init__(self, frame: pd.DataFrame, table: str) -> None:
        if "_source_row" in frame or "motivo" in frame:
            raise ValueError("La entrada debe ser cruda, sin _source_row ni motivo")
        self.original = frame.reset_index(drop=True).copy(deep=True)
        self.original["_source_row"] = self.original.index
        self.data = self.original.copy(deep=True)
        self.table = table
        self.reasons = pd.Series("", index=self.data.index, dtype=object)
        self.rules: list[Rule] = []

    @property
    def rejected(self) -> pd.Series:
        return self.reasons.ne("")

    def rule(self, name: str, mask: pd.Series, *, quarantine: bool = False) -> None:
        mask = mask.reindex(self.data.index).fillna(False).astype(bool)
        self.rules.append(Rule(name, "cuarentena" if quarantine else "marca", self.data.index[mask].tolist()))
        if quarantine:
            self.reasons.loc[mask] = self.reasons.loc[mask].map(lambda old: f"{old}|{name}" if old else name)

    def numeric(self, columns: list[str]) -> None:
        for column in columns:
            self.data[column] = pd.to_numeric(self.data[column], errors="coerce")

    def dates(self, columns: list[str]) -> None:
        for column in columns:
            self.data[column] = pd.to_datetime(self.data[column], errors="coerce")

    def deduplicate(self, config: Config, *, rank_products: bool = False) -> None:
        keys = config.keys[self.table]
        ranked = self.data
        if rank_products:
            ranked = self.data.assign(
                _cost=self.data.unit_cost.notna(),
                _complete=self.original.drop(columns="_source_row").notna().sum(axis=1),
            ).sort_values(["_cost", "_complete", "_source_row"], ascending=[False, False, True], kind="stable")
        losers = ranked.index[ranked.duplicated(keys, keep="first")]
        self.rule("duplicado", pd.Series(self.data.index.isin(losers), index=self.data.index), quarantine=True)
        if config.quarantine_null_keys:
            self.rule("llave_nula", self.data[keys].isna().any(axis=1), quarantine=True)

    def finish(self, **notes: object) -> Result:
        clean = self.data.loc[~self.rejected].copy()
        quarantine = self.original.loc[self.rejected].copy()
        quarantine["motivo"] = self.reasons.loc[self.rejected]
        assert len(clean) + len(quarantine) == len(self.original)
        return clean, quarantine, Summary(self.table, len(self.original), len(clean), len(quarantine), self.rules, notes)
