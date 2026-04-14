"""Hourly external statistics for Izypower Cloud."""
from __future__ import annotations

import logging
from datetime import datetime
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
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Tuples: (api_field, unit, has_sum, has_mean)
# stat_suffix and display_name are resolved via _LOCALIZED_STAT_SUFFIXES / _LOCALIZED_METRIC_LABELS.
_STATISTIC_FIELDS: list[tuple[str, str, bool, bool]] = [
    ("meter_energy_p", "kWh", True, False),
    ("meter_energy_n", "kWh", True, False),
    ("energy", "kWh", True, False),
    ("total_consumption", "kWh", True, False),
    ("consumption", "kWh", True, False),
    ("storage_in", "kWh", True, False),
    ("storage_out", "kWh", True, False),
    ("cover_rate", "%", False, True),
    ("storage_in_rate", "%", False, True),
    ("energy_self_rate", "%", False, True),
    ("meter_energy_p_rate", "%", False, True),
    ("storage_out_rate", "%", False, True),
    ("consumption_rate", "%", False, True),
    ("meter_energy_n_rate", "%", False, True),
]

_KWH_PERIODS: list[str] = ["day", "month", "year", "all"]
_PERCENT_PERIODS: list[str] = ["day", "month", "year", "all"]

_PERIOD_DISPLAY: dict[str, dict[str, str]] = {
    "en": {"day": "Day", "month": "Month", "year": "Year", "all": "Total"},
    "fr": {"day": "Jour", "month": "Mois", "year": "Annee", "all": "Total"},
}

_PERIOD_ID_TOKEN: dict[str, dict[str, str]] = {
    "en": {"day": "day", "month": "month", "year": "year", "all": "total"},
    "fr": {"day": "jour", "month": "mois", "year": "annee", "all": "total"},
}

_LOCALIZED_METRIC_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "meter_energy_p": "Grid Import",
        "meter_energy_n": "Grid Export",
        "energy": "Production",
        "total_consumption": "Consumption",
        "consumption": "Consumption from PV",
        "storage_in": "Battery Charge",
        "storage_out": "Battery Discharge",
        "cover_rate": "Cover Rate",
        "storage_in_rate": "Battery Rate (Charge)",
        "energy_self_rate": "Energy Self Rate",
        "meter_energy_p_rate": "Grid Rate (Import)",
        "storage_out_rate": "Battery Rate (Discharge)",
        "consumption_rate": "Consumption Rate from PV",
        "meter_energy_n_rate": "Grid Rate (Export)",
    },
    "fr": {
        "meter_energy_p": "Reseau Import",
        "meter_energy_n": "Reseau Export",
        "energy": "Production",
        "total_consumption": "Consommation",
        "consumption": "Consommation depuis PV",
        "storage_in": "Batterie Charge",
        "storage_out": "Batterie Decharge",
        "cover_rate": "Couverture",
        "storage_in_rate": "Taux Batterie (Charge)",
        "energy_self_rate": "Autoconsommation",
        "meter_energy_p_rate": "Taux Reseau (Import)",
        "storage_out_rate": "Taux Batterie (Decharge)",
        "consumption_rate": "Taux Consommation depuis PV",
        "meter_energy_n_rate": "Taux Reseau (Export)",
    },
}

_LOCALIZED_STAT_SUFFIXES: dict[str, dict[str, str]] = {
    "en": {
        "meter_energy_p": "grid_import_stats",
        "meter_energy_n": "grid_export_stats",
        "energy": "production_stats",
        "total_consumption": "total_consumption_stats",
        "consumption": "consumption_pv_stats",
        "storage_in": "battery_charge_stats",
        "storage_out": "battery_discharge_stats",
        "cover_rate": "cover_rate_stats",
        "storage_in_rate": "battery_charge_rate_stats",
        "energy_self_rate": "self_consumption_rate_stats",
        "meter_energy_p_rate": "grid_import_rate_stats",
        "storage_out_rate": "battery_discharge_rate_stats",
        "consumption_rate": "consumption_pv_rate_stats",
        "meter_energy_n_rate": "grid_export_rate_stats",
    },
    "fr": {
        "meter_energy_p": "reseau_import_stats",
        "meter_energy_n": "reseau_export_stats",
        "energy": "production_stats",
        "total_consumption": "consommation_totale_stats",
        "consumption": "consommation_depuis_pv_stats",
        "storage_in": "batterie_charge_stats",
        "storage_out": "batterie_decharge_stats",
        "cover_rate": "taux_couverture_stats",
        "storage_in_rate": "taux_charge_batterie_stats",
        "energy_self_rate": "taux_autoconsommation_stats",
        "meter_energy_p_rate": "taux_import_reseau_stats",
        "storage_out_rate": "taux_decharge_batterie_stats",
        "consumption_rate": "taux_consommation_pv_stats",
        "meter_energy_n_rate": "taux_export_reseau_stats",
    },
}


def _integration_display_name() -> str:
    """Return a readable integration title from DOMAIN."""
    return DOMAIN.replace("_", " ").title()


def _language_code(hass: HomeAssistant) -> str:
    """Return a normalized HA language code."""
    language = (hass.config.language or "en").lower()
    return "fr" if language.startswith("fr") else "en"


def _localized_metric_label(hass: HomeAssistant, api_field: str, fallback: str) -> str:
    """Return a localized metric label for display name fallback."""
    language = _language_code(hass)
    return _LOCALIZED_METRIC_LABELS.get(language, {}).get(api_field, fallback)


def _localized_stat_suffix(api_field: str, language: str) -> str:
    """Return the localized stat_suffix for an api_field."""
    return _LOCALIZED_STAT_SUFFIXES.get(language, _LOCALIZED_STAT_SUFFIXES["en"]).get(
        api_field, f"{api_field}_stats"
    )


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


def _build_period_stat_suffix(stat_suffix: str, period: str, language: str) -> str:
    """Build period-specific suffix for statistic ids."""
    base = stat_suffix.removesuffix("_stats")
    period_token = _PERIOD_ID_TOKEN.get(language, _PERIOD_ID_TOKEN["en"]).get(period, period)
    return f"{base}_{period_token}_stats"


def _build_effective_stat_suffix(stat_suffix: str, unit: str, period: str, language: str) -> str:
    """Build effective suffix by unit and period.

    All period-based metrics use period-specific suffixes.
    The period token in the ID is localised (e.g. jour/mois/annee/total for FR).
    """
    return _build_period_stat_suffix(stat_suffix, period, language)


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


def _unit_class_for_unit(unit: str) -> str | None:
    """Return recorder unit_class for a statistics unit."""
    if unit == "kWh":
        return "energy"
    return None


def _periods_for_unit(unit: str) -> list[str]:
    """Return enabled periods for a metric unit."""
    if unit == "kWh":
        return _KWH_PERIODS
    if unit == "%":
        return _PERCENT_PERIODS
    return ["day"]


def _display_metric_name(base_label: str, unit: str, period: str, language: str) -> str:
    """Build display metric label for statistic metadata name."""
    period_display = _PERIOD_DISPLAY.get(language, {}).get(period, period.title())
    return f"{base_label} {period_display}"


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
    language = _language_code(hass)

    for api_field, unit, has_sum, has_mean in _STATISTIC_FIELDS:
        stat_suffix = _localized_stat_suffix(api_field, language)
        display_name = _LOCALIZED_METRIC_LABELS.get(language, _LOCALIZED_METRIC_LABELS["en"]).get(api_field, api_field)
        for period in _periods_for_unit(unit):
            report_data = report_data_by_period.get(period, {})
            value = _parse_value(report_data.get(api_field))
            if value is None:
                continue

            period_suffix = _build_effective_stat_suffix(stat_suffix, unit, period, language)
            statistic_id = (
                resolved_statistic_ids.get(period_suffix)
                if resolved_statistic_ids and period_suffix in resolved_statistic_ids
                else _build_statistic_id(station_id, station_name, period_suffix)
            )
            metadata_name = (
                name_overrides.get(statistic_id)
                if name_overrides and statistic_id in name_overrides
                else _normalize_stat_name(
                    station_id,
                    station_name,
                    _display_metric_name(display_name, unit, period, language),
                )
            )

            metadata = StatisticMetaData(
                has_mean=has_mean,
                has_sum=has_sum,
                mean_type=(
                    StatisticMeanType.ARITHMETIC
                    if has_mean
                    else StatisticMeanType.NONE
                ),
                name=metadata_name,
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=_unit_class_for_unit(unit),
                unit_of_measurement=unit,
            )

            if has_sum:
                prev_state = previous_states.get(statistic_id)
                if prev_state is None:
                    # First observation is a baseline reference only.
                    # Do not count it as an hourly increment.
                    delta = 0.0
                elif value >= prev_state:
                    delta = value - prev_state
                else:
                    # Source cumulative reset (period rollover): start from current value.
                    delta = value

                if delta < 0:
                    delta = 0.0

                previous_states[statistic_id] = value
                new_sum = running_sums.get(statistic_id, 0.0) + delta
                running_sums[statistic_id] = new_sum
                stat_data = StatisticData(start=slot_utc, state=value, sum=new_sum)
            else:
                stat_data = StatisticData(start=slot_utc, mean=value)

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

    language = _language_code(hass)
    resolved_statistic_ids: dict[str, str] = {}
    name_overrides: dict[str, str] = {}
    for api_field, unit, _, _ in _STATISTIC_FIELDS:
        stat_suffix = _localized_stat_suffix(api_field, language)
        metric_label = _localized_metric_label(hass, api_field, api_field)
        for period in _periods_for_unit(unit):
            period_suffix = _build_effective_stat_suffix(stat_suffix, unit, period, language)
            target_id = _resolve_existing_statistic_id(
                all_meta,
                station_id,
                station_name,
                period_suffix,
            )
            resolved_statistic_ids[period_suffix] = target_id
            existing_name = all_meta.get(target_id, {}).get("name")
            name_overrides[target_id] = _normalize_stat_name(
                station_id,
                station_name,
                _display_metric_name(metric_label, unit, period, language),
                # Force-refresh kWh day names to drop Day/Jour from old metadata.
                None if (unit == "kWh" and period == "day") else str(existing_name) if existing_name else None,
            )

    inserted_values = 0
    for api_field, unit, _, _ in _STATISTIC_FIELDS:
        for period in _periods_for_unit(unit):
            if _parse_value(report_data_by_period.get(period, {}).get(api_field)) is not None:
                inserted_values += 1

    running_sums: dict[str, float] = {}
    previous_states: dict[str, float] = {}
    for api_field, unit, has_sum, _ in _STATISTIC_FIELDS:
        if not has_sum:
            continue
        stat_suffix = _localized_stat_suffix(api_field, language)
        for period in _periods_for_unit(unit):
            period_suffix = _build_effective_stat_suffix(stat_suffix, unit, period, language)
            statistic_id = resolved_statistic_ids.get(
                period_suffix,
                _build_statistic_id(station_id, station_name, period_suffix),
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
                row = last_stats[statistic_id][0]
                raw_sum = row.get("sum")
                raw_state = row.get("state")
                if raw_sum is not None:
                    running_sums[statistic_id] = float(raw_sum)
                else:
                    running_sums[statistic_id] = 0.0
                if raw_state is not None:
                    previous_states[statistic_id] = float(raw_state)
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

    _LOGGER.debug(
        "Inserted hourly statistics for station=%s slot=%s values=%s",
        station_id,
        slot_utc.isoformat(),
        inserted_values,
    )
    return inserted_values > 0
