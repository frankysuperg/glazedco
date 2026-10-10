"""Caché Parquet local con vencimiento; nunca cambia reglas de limpieza."""

from dataclasses import asdict
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time

import pandas as pd

from glazedco.cleaning import clean_all, load_config
from glazedco.cleaning.io import read_tables
from glazedco.cleaning.pipeline import TABLES
from glazedco.db import Database, load_environment


def max_cache_age() -> float:
    load_environment()
    value = float(os.getenv("API_CLEAN_CACHE_MAX_AGE_SECONDS", "86400"))
    if not math.isfinite(value) or value < 0:
        raise ValueError("Antigüedad máxima de caché inválida")
    return value


def _signatures(paths: dict[str, Path]) -> dict:
    return {name: [path.stat().st_size, path.stat().st_mtime_ns] for name, path in paths.items()}


def load_tables(directory: Path, max_age_seconds: float) -> dict[str, pd.DataFrame]:
    """Reutiliza también Parquet de cleaning --cache sin manifiesto de la API.

    El manifiesto se publica al final; una escritura interrumpida o una mezcla
    de archivos invalida la caché completa, sin devolver tablas parciales.
    """
    if not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("Antigüedad máxima de caché inválida")
    config = load_config()
    paths = {table: directory / f"{table}.parquet" for table in TABLES}
    manifest = directory / ".api_cache.json"
    try:
        signatures = _signatures(paths)
        now = time()
        fresh = max_age_seconds > 0 and all(
            0 <= now - path.stat().st_mtime <= max_age_seconds for path in paths.values())
        has_manifest = manifest.exists()
        metadata = json.loads(manifest.read_text(encoding="utf-8")) if has_manifest else None
        valid = not has_manifest or metadata == {"config": asdict(config), "files": signatures}
        if fresh and valid:
            cached = {table: pd.read_parquet(path) for table, path in paths.items()}
            if _signatures(paths) == signatures and (
                not manifest.exists() if metadata is None else
                json.loads(manifest.read_text(encoding="utf-8")) == metadata
            ):
                return cached
    except (OSError, ValueError, TypeError):
        pass  # Caché incompleta, vencida o ilegible: ejecutar la carga habitual.

    with Database(assistant=True) as database:
        raw = read_tables(database, config)
    cleaned = clean_all(raw, config)
    tables = {table: cleaned[table][0] for table in TABLES}
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".writing-", dir=directory) as temporary:
        stage = Path(temporary)
        for table, frame in tables.items():
            frame.to_parquet(stage / f"{table}.parquet")
        expected = _signatures({table: stage / f"{table}.parquet" for table in TABLES})
        # Invalidar primero; publicar cada archivo atómicamente y el manifiesto al final.
        marker = stage / "manifest.json"
        marker.write_text('{"incompleta": true}', encoding="utf-8")
        marker.replace(manifest)
        for table, path in paths.items():
            (stage / f"{table}.parquet").replace(path)
        marker.write_text(json.dumps({"config": asdict(config), "files": expected}), encoding="utf-8")
        marker.replace(manifest)
    return tables
