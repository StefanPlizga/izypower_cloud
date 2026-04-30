"""Hourly external statistics for Izypower Cloud."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
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
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
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

_FIRST_RUN_SENSOR_KEYS_BY_STAT_SUFFIX: dict[str, str] = {
    "reseau_import_stats": "grid_day_import",
    "reseau_export_stats": "grid_day_export",
    "production_stats": "production_day",
    "batterie_charge_stats": "battery_day_charge",
    "batterie_decharge_stats": "battery_day_discharge",
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


def _stat_point_get(point: Any, key: str) -> Any:
    """Get a value from a recorder statistic point (dict or object-like)."""
    if isinstance(point, dict):
        return point.get(key)
    return getattr(point, key, None)


def _resolve_station_sensor_entity_id(hass: HomeAssistant, station_id: int, translation_key: str) -> str | None:
    """Resolve station sensor entity id by its known unique_id pattern."""
    unique_id = f"{station_id}_{translation_key}"
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


def _build_seed_slot_utc(slot_utc: datetime, sensor_last_updated: datetime | None) -> datetime:
    """Build a backfilled seed slot shifted 1 hour earlier than sensor timestamp."""
    if sensor_last_updated is None:
        base_utc = slot_utc
    else:
        base_utc = sensor_last_updated
        if base_utc.tzinfo is None:
            base_utc = base_utc.replace(tzinfo=timezone.utc)
        else:
            base_utc = dt_util.as_utc(base_utc)

    shifted = base_utc - timedelta(hours=1)
    shifted = shifted.replace(minute=0, second=0, microsecond=0)

    # Keep seed rows strictly before the current slot.
    if shifted >= slot_utc:
        shifted = (slot_utc - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    return shifted


def _shift_timestamp_one_hour_earlier(raw_start: Any) -> datetime | None:
    """Shift a source statistics timestamp exactly one hour earlier in UTC."""
    start_dt = _coerce_datetime(raw_start)
    if start_dt is None:
        return None

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_dt = dt_util.as_utc(start_dt)

    return start_dt - timedelta(hours=1)


def _fetch_sensor_stat_rows(
    hass: HomeAssistant,
    sensor_statistic_id: str,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    """Fetch source sensor statistics rows with compatibility fallbacks."""
    start_utc = datetime(1970, 1, 1, tzinfo=timezone.utc)

    try:
        data = statistics_during_period(
            hass,
            start_utc,
            end_utc,
            [sensor_statistic_id],
            "hour",
            None,
            {"sum", "state"},
        )
    except TypeError:
        try:
            data = statistics_during_period(
                hass,
                start_utc,
                end_utc,
                [sensor_statistic_id],
                "hour",
                {"sum", "state"},
            )
        except TypeError:
            data = statistics_during_period(
                hass,
                start_utc,
                end_utc,
                [sensor_statistic_id],
                {"sum", "state"},
            )

    if not isinstance(data, dict):
        return []

    rows = data.get(sensor_statistic_id, [])
    return rows if isinstance(rows, list) else []


async def _seed_first_run_statistics_from_sensors(
    hass: HomeAssistant,
    station_id: int,
    station_name: str,
    slot_utc: datetime,
    resolved_statistic_ids: dict[str, str],
    name_overrides: dict[str, str],
    stats_with_existing_rows: set[str],
    last_start_by_statistic_id: dict[str, datetime],
    running_sums: dict[str, float],
    previous_states: dict[str, float],
) -> tuple[int, bool]:
    """Seed missing first-run statistics from station day sensors."""
    api_field_by_suffix = {suffix: field for field, suffix in _STAT_SUFFIXES.items()}
    seeded = 0
    should_retry = False

    for stat_suffix, translation_key in _FIRST_RUN_SENSOR_KEYS_BY_STAT_SUFFIX.items():
        statistic_id = resolved_statistic_ids.get(stat_suffix)
        _LOGGER.debug(
            "Seed check station=%s stat_suffix=%s translation_key=%s target_statistic_id=%s",
            station_id,
            stat_suffix,
            translation_key,
            statistic_id,
        )
        if not statistic_id:
            continue
        if statistic_id in stats_with_existing_rows:
            _LOGGER.debug(
                "Seed skip station=%s target_statistic_id=%s reason=already_has_rows",
                station_id,
                statistic_id,
            )
            continue

        try:
            entity_id = _resolve_station_sensor_entity_id(hass, station_id, translation_key)
            if not entity_id:
                should_retry = True
                _LOGGER.debug(
                    "First-run seed skipped for stat %s: no entity for unique_id %s_%s",
                    statistic_id,
                    station_id,
                    translation_key,
                )
                continue

            state_obj = hass.states.get(entity_id)
            if state_obj is None:
                should_retry = True
                _LOGGER.debug("First-run seed skipped for stat %s: entity %s unavailable", statistic_id, entity_id)
                continue

            value = _parse_value(state_obj.state)
            if value is None:
                state_text = str(state_obj.state).strip().lower()
                if state_text in {"unknown", "unavailable", "none", ""}:
                    should_retry = True
                _LOGGER.debug(
                    "First-run seed skipped for stat %s: non-numeric state from %s (state=%s)",
                    statistic_id,
                    entity_id,
                    state_obj.state,
                )
                continue

            source_statistic_id = entity_id
            _LOGGER.debug(
                "Seed source resolved station=%s target_statistic_id=%s source_statistic_id=%s",
                station_id,
                statistic_id,
                source_statistic_id,
            )
            source_rows = await get_instance(hass).async_add_executor_job(
                _fetch_sensor_stat_rows,
                hass,
                source_statistic_id,
                slot_utc,
            )
            _LOGGER.debug(
                "Seed source history fetched station=%s source_statistic_id=%s rows=%s end=%s",
                station_id,
                source_statistic_id,
                len(source_rows),
                slot_utc.isoformat(),
            )

            api_field = api_field_by_suffix.get(stat_suffix)
            metric_label = _METRIC_LABELS.get(api_field or "", api_field or stat_suffix)
            metadata_name = name_overrides.get(statistic_id) or _normalize_stat_name(
                station_id,
                station_name,
                _display_metric_name(metric_label),
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

            history_points: list[StatisticData] = []
            if source_rows:
                latest_by_start: dict[datetime, StatisticData] = {}
                for row in source_rows:
                    shifted_start = _shift_timestamp_one_hour_earlier(row.get("start"))
                    row_state = _parse_value(row.get("state"))
                    row_sum = _parse_value(row.get("sum"))
                    if shifted_start is None or row_state is None:
                        continue
                    if row_sum is None:
                        row_sum = row_state
                    latest_by_start[shifted_start] = StatisticData(
                        start=shifted_start,
                        state=row_state,
                        sum=row_sum,
                    )

                history_points = [latest_by_start[k] for k in sorted(latest_by_start)]

                target_last_start = last_start_by_statistic_id.get(statistic_id)
                if target_last_start is not None:
                    before_count = len(history_points)
                    history_points = [
                        p
                        for p in history_points
                        if isinstance(_stat_point_get(p, "start"), datetime)
                        and _stat_point_get(p, "start") > target_last_start
                    ]
                    _LOGGER.debug(
                        "Seed history filtered station=%s target_statistic_id=%s before=%s after=%s target_last_start=%s",
                        station_id,
                        statistic_id,
                        before_count,
                        len(history_points),
                        target_last_start.isoformat(),
                    )

            if history_points:
                async_add_external_statistics(hass, metadata, history_points)
                last_point = history_points[-1]
                running_sums[statistic_id] = float(_stat_point_get(last_point, "sum") or 0.0)
                previous_states[statistic_id] = float(_stat_point_get(last_point, "state") or 0.0)
                seeded += 1
                _LOGGER.debug(
                    "Seed history applied station=%s target_statistic_id=%s rows=%s",
                    station_id,
                    statistic_id,
                    len(history_points),
                )
                _LOGGER.info(
                    "Seeded first-run statistic %s from %s history: rows=%s first=%s last=%s",
                    statistic_id,
                    source_statistic_id,
                    len(history_points),
                    _stat_point_get(history_points[0], "start").isoformat(),
                    _stat_point_get(history_points[-1], "start").isoformat(),
                )
            else:
                # If target already has rows and there is no newer source history,
                # avoid writing a fallback point that could duplicate/overlap.
                if statistic_id in stats_with_existing_rows:
                    _LOGGER.debug(
                        "Seed skip fallback station=%s target_statistic_id=%s reason=no_newer_history_and_target_exists",
                        station_id,
                        statistic_id,
                    )
                    continue

                seed_slot_utc = _build_seed_slot_utc(slot_utc, state_obj.last_updated)
                _LOGGER.debug(
                    "Seed fallback current state station=%s target_statistic_id=%s source_statistic_id=%s state=%s seed_slot=%s",
                    station_id,
                    statistic_id,
                    source_statistic_id,
                    state_obj.state,
                    seed_slot_utc.isoformat(),
                )
                async_add_external_statistics(
                    hass,
                    metadata,
                    [StatisticData(start=seed_slot_utc, state=value, sum=value)],
                )

                running_sums[statistic_id] = value
                previous_states[statistic_id] = value
                seeded += 1
                _LOGGER.info(
                    "Seeded first-run statistic %s from %s current state only: value=%.3f start=%s",
                    statistic_id,
                    entity_id,
                    value,
                    seed_slot_utc.isoformat(),
                )
        except Exception:
            _LOGGER.exception(
                "Seed failed station=%s stat_suffix=%s target_statistic_id=%s",
                station_id,
                stat_suffix,
                statistic_id,
            )
            continue

    return seeded, should_retry


async def async_migrate_initial_statistics_from_sensors_if_missing(
    hass: HomeAssistant,
    station_id: int,
    station_name: str,
    slot_utc: datetime,
) -> tuple[int, bool]:
    """Migrate missing station statistics from day sensors at startup.

    Returns (seeded_count, should_retry) where should_retry=True means one or
    more source sensors were not ready yet.
    """
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
    stats_with_existing_rows: set[str] = set()
    last_start_by_statistic_id: dict[str, datetime] = {}

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
            None,
        )

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
            last_start = row.get("start")
            last_start_dt = _coerce_datetime(last_start)
            if last_start_dt is not None:
                if last_start_dt.tzinfo is None:
                    last_start_dt = last_start_dt.replace(tzinfo=timezone.utc)
                else:
                    last_start_dt = dt_util.as_utc(last_start_dt)
                last_start_by_statistic_id[statistic_id] = last_start_dt

    running_sums: dict[str, float] = {}
    previous_states: dict[str, float] = {}
    seeded, should_retry = await _seed_first_run_statistics_from_sensors(
        hass,
        station_id,
        station_name,
        slot_utc,
        resolved_statistic_ids,
        name_overrides,
        stats_with_existing_rows,
        last_start_by_statistic_id,
        running_sums,
        previous_states,
    )

    if seeded:
        _LOGGER.info(
            "Startup migration seeded %s statistics from sensors for station %s",
            seeded,
            station_id,
        )

    return seeded, should_retry


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
