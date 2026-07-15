"""Hourly external statistics for Izypower Cloud."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    list_statistic_ids,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Active API fields (day + kWh only).
_STATISTIC_FIELDS: list[str] = [
    "meter_energy_p",
    "meter_energy_n",
    "energy",
    "storage_in",
    "storage_out",
]

_METRIC_LABELS: dict[str, str] = {
    "meter_energy_p": "Reseau Import",
    "meter_energy_n": "Reseau Export",
    "energy": "Production",
    "storage_in": "Batterie Charge",
    "storage_out": "Batterie Decharge",
}

_STAT_SUFFIXES: dict[str, str] = {
    "meter_energy_p": "reseau_import_stats",
    "meter_energy_n": "reseau_export_stats",
    "energy": "production_stats",
    "storage_in": "batterie_charge_stats",
    "storage_out": "batterie_decharge_stats",
}


def _integration_display_name() -> str:
    """Return a readable integration title from DOMAIN."""
    return DOMAIN.replace("_", " ").title()


def _normalize_stat_name(
    station_id: int,
    station_name: str,
    metric_fallback: str,
    source_name: str | None = None,
) -> str:
    """Build sensor-aligned statistic name."""
    integration_name = _integration_display_name()
    metric_part = (source_name or "").strip()

    prefixes_to_strip = [
        f"{integration_name} {station_name} ({station_id}) ",
        f"{integration_name} {station_name} - ",
        f"{station_name} ({station_id}) ",
        f"{station_name} - ",
        f"{station_name} ",
    ]
    for prefix in prefixes_to_strip:
        if metric_part.startswith(prefix):
            metric_part = metric_part[len(prefix) :].strip()
            break

    if not metric_part:
        metric_part = metric_fallback

    if not metric_part.lower().endswith("stats"):
        metric_part = f"{metric_part} Stats"

    return f"{integration_name} {station_name} ({station_id}) {metric_part}"


def _build_statistic_id(station_id: int, station_name: str, stat_suffix: str) -> str:
    """Build a HA-compliant external statistic id."""
    station_slug = slugify(station_name) or f"station_{station_id}"
    return f"{DOMAIN}:{station_id}_{station_slug}_{stat_suffix}"


def _resolve_existing_statistic_id(
    all_meta: dict[str, dict[str, Any]],
    station_id: int,
    station_name: str,
    stat_suffix: str,
) -> str:
    """Resolve statistic id for station and suffix, reusing existing ids when possible."""
    default_id = _build_statistic_id(station_id, station_name, stat_suffix)
    if default_id in all_meta:
        return default_id

    prefix = f"{DOMAIN}:{station_id}_"
    suffix = f"_{stat_suffix}"
    candidates = [sid for sid in all_meta if sid.startswith(prefix) and sid.endswith(suffix)]

    if len(candidates) == 1:
        return candidates[0]

    return default_id


def _period_has_reset(hass: HomeAssistant, last_start: Any, slot_utc: datetime) -> bool:
    """Return True if local day rolled over between last_start and slot_utc."""
    last_start_dt = _coerce_datetime(last_start)
    if last_start_dt is None:
        return False

    # Compare local dates to match the API's day-boundary behavior.
    if last_start_dt.tzinfo is None:
        last_start_dt = last_start_dt.replace(tzinfo=timezone.utc)

    if slot_utc.tzinfo is None:
        slot_utc = slot_utc.replace(tzinfo=timezone.utc)

    last_local = dt_util.as_local(last_start_dt)
    current_local = dt_util.as_local(slot_utc)
    return last_local.date() != current_local.date()


def _coerce_datetime(raw: Any) -> datetime | None:
    """Normalize recorder datetime-like values to datetime."""
    if isinstance(raw, datetime):
        return raw

    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        try:
            # Accept both explicit offsets and trailing Z.
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return None


def _parse_value(raw: Any) -> float | None:
    """Parse a raw API value to float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.strip().rstrip("%").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _display_metric_name(base_label: str) -> str:
    """Build display metric label for statistic metadata name."""
    return base_label


def _insert_stats_slot(
    hass: HomeAssistant,
    station_id: int,
    station_name: str,
    report_data_by_period: dict[str, dict],
    slot_utc: datetime,
    running_sums: dict[str, float],
    previous_states: dict[str, float],
    resolved_statistic_ids: dict[str, str] | None = None,
    name_overrides: dict[str, str] | None = None,
) -> None:
    """Insert one time-slot of statistics into HA external statistics."""
    day_report_data = report_data_by_period.get("day", {})

    for api_field in _STATISTIC_FIELDS:
        stat_suffix = _STAT_SUFFIXES.get(api_field, f"{api_field}_stats")
        display_name = _METRIC_LABELS.get(api_field, api_field)
        value = _parse_value(day_report_data.get(api_field))
        if value is None:
            continue

        statistic_id = (
            resolved_statistic_ids.get(stat_suffix)
            if resolved_statistic_ids and stat_suffix in resolved_statistic_ids
            else _build_statistic_id(station_id, station_name, stat_suffix)
        )
        metadata_name = (
            name_overrides.get(statistic_id)
            if name_overrides and statistic_id in name_overrides
            else _normalize_stat_name(
                station_id,
                station_name,
                _display_metric_name(display_name),
            )
        )

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=metadata_name,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class="energy",
            unit_of_measurement="kWh",
        )

        prev_state = previous_states.get(statistic_id)
        if prev_state is None:
            # First observation is a baseline reference only.
            # Do not count it as an hourly increment.
            delta = 0.0
        elif value >= prev_state:
            delta = value - prev_state
        else:
            # kWh values must only increase within a period.
            # A lower value is a bad reading — skip and wait for the next valid one.
            _LOGGER.info(
                "Skipping stat %s: value %.3f < prev_state %.3f (bad reading)",
                statistic_id,
                value,
                prev_state,
            )
            continue

        previous_states[statistic_id] = value
        new_sum = running_sums.get(statistic_id, 0.0) + delta
        running_sums[statistic_id] = new_sum
        _LOGGER.info(
            "Stat %s: prev_state=%.3f, value=%.3f, delta=%.3f, new_sum=%.3f",
            statistic_id,
            prev_state if prev_state is not None else 0.0,
            value,
            delta,
            new_sum,
        )
        stat_data = StatisticData(start=slot_utc, state=value, sum=new_sum)

        async_add_external_statistics(hass, metadata, [stat_data])


async def async_insert_hourly_statistics_from_report(
    hass: HomeAssistant,
    station_id: int,
    station_name: str,
    report_data_by_period: dict[str, dict],
    slot_utc: datetime,
) -> bool:
    """Insert hourly statistics for one station using already fetched report data."""
    if not report_data_by_period:
        return False

    all_stat_ids = await get_instance(hass).async_add_executor_job(
        list_statistic_ids,
        hass,
        None,
        None,
    )
    if isinstance(all_stat_ids, dict):
        all_meta: dict[str, dict[str, Any]] = all_stat_ids
    else:
        all_meta = {
            item["statistic_id"]: item
            for item in all_stat_ids
            if isinstance(item, dict) and item.get("statistic_id")
        }

    resolved_statistic_ids: dict[str, str] = {}
    name_overrides: dict[str, str] = {}
    day_report_data = report_data_by_period.get("day", {})

    for api_field in _STATISTIC_FIELDS:
        stat_suffix = _STAT_SUFFIXES.get(api_field, f"{api_field}_stats")
        metric_label = _METRIC_LABELS.get(api_field, api_field)
        target_id = _resolve_existing_statistic_id(
            all_meta,
            station_id,
            station_name,
            stat_suffix,
        )
        resolved_statistic_ids[stat_suffix] = target_id
        name_overrides[target_id] = _normalize_stat_name(
            station_id,
            station_name,
            _display_metric_name(metric_label),
            # Force-refresh day names to drop old period suffixes from metadata.
            None,
        )

    inserted_values = 0
    for api_field in _STATISTIC_FIELDS:
        if _parse_value(day_report_data.get(api_field)) is not None:
            inserted_values += 1

    running_sums: dict[str, float] = {}
    previous_states: dict[str, float] = {}
    stats_with_existing_rows: set[str] = set()
    last_start_by_statistic_id: dict[str, datetime] = {}
    for api_field in _STATISTIC_FIELDS:
        stat_suffix = _STAT_SUFFIXES.get(api_field, f"{api_field}_stats")
        statistic_id = resolved_statistic_ids.get(
            stat_suffix,
            _build_statistic_id(station_id, station_name, stat_suffix),
        )
        last_stats = await get_instance(hass).async_add_executor_job(
            get_last_statistics,
            hass,
            1,
            statistic_id,
            True,
            {"sum", "state"},
        )
        if statistic_id in last_stats and last_stats[statistic_id]:
            stats_with_existing_rows.add(statistic_id)
            row = last_stats[statistic_id][0]
            raw_sum = row.get("sum")
            raw_state = row.get("state")
            last_start = row.get("start")
            last_start_dt = _coerce_datetime(last_start)
            if last_start_dt is not None:
                if last_start_dt.tzinfo is None:
                    last_start_dt = last_start_dt.replace(tzinfo=timezone.utc)
                else:
                    last_start_dt = dt_util.as_utc(last_start_dt)
                last_start_by_statistic_id[statistic_id] = last_start_dt
            if raw_sum is not None:
                running_sums[statistic_id] = float(raw_sum)
            else:
                running_sums[statistic_id] = 0.0
            # If the API day has rolled over since the last stat, treat
            # prev_state as 0 so delta = full new-day value rather than
            # being skipped as a "bad reading" or incorrectly subtracted.
            if last_start and _period_has_reset(hass, last_start, slot_utc):
                _LOGGER.info(
                    "Day rollover detected for stat %s: last_start=%s slot_utc=%s; resetting prev_state to 0",
                    statistic_id,
                    last_start,
                    slot_utc.isoformat(),
                )
                previous_states[statistic_id] = 0.0
            elif raw_state is not None:
                previous_states[statistic_id] = float(raw_state)
            else:
                # State missing but stat exists — treat last known state as 0
                # so the skip-on-decrease guard can still fire next cycle.
                previous_states[statistic_id] = 0.0
        else:
            running_sums[statistic_id] = 0.0

    _insert_stats_slot(
        hass,
        station_id,
        station_name,
        report_data_by_period,
        slot_utc,
        running_sums,
        previous_states,
        resolved_statistic_ids=resolved_statistic_ids,
        name_overrides=name_overrides,
    )

    _LOGGER.info(
        "Inserted hourly statistics for station=%s slot=%s values=%s",
        station_id,
        slot_utc.isoformat(),
        inserted_values,
    )
    return inserted_values > 0
