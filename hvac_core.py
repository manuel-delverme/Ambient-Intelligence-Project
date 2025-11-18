"""HVAC sizing utilities with whitespace-aware IT distribution.

This module replicates the reusable logic described in the prompt and adds a
whitespace distribution component that can sit between the IT and CRAH stages.
It is intentionally standalone so Grasshopper/IronPython scripts can import it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple


# ---------------------------------------------------------------------------
# Redundancy helpers
# ---------------------------------------------------------------------------


@dataclass
class RedundancyConfig:
    """Container describing required and total counts."""

    n_required: int
    n_extra: int

    def __post_init__(self) -> None:
        self.n_required = max(1, int(self.n_required))
        self.n_extra = max(0, int(self.n_extra))
        self.n_total = self.n_required + self.n_extra


def parse_redundancy(text) -> RedundancyConfig:
    """Parse strings like "3+1" or numeric values into a config."""

    if text is None:
        return RedundancyConfig(1, 0)
    if isinstance(text, (int, float)):
        return RedundancyConfig(int(text), 0)

    s = str(text).replace(" ", "")
    if "+" in s:
        req, extra = s.split("+", 1)
        return RedundancyConfig(int(req), int(extra))
    return RedundancyConfig(int(s), 0)


# ---------------------------------------------------------------------------
# Base consumer types
# ---------------------------------------------------------------------------


class PowerConsumer:
    """Base class shared by every element that consumes electrical power."""

    def __init__(self, idx: int, peak_kW: float, normal_kW: float, kind: str):
        self.id = idx
        self.peak_kW = float(max(0.0, peak_kW))
        self.normal_kW = float(max(0.0, normal_kW))
        self.kind = kind
        self.primary_string = None
        self.secondary_string = None


class CRAHUnit(PowerConsumer):
    def __init__(self, idx, cap_kW, peak_kW, normal_kW, whitespace_id=None):
        super().__init__(idx, peak_kW, normal_kW, "CRAH")
        self.design_capacity_kW = cap_kW
        self.whitespace_id = whitespace_id


class PumpUnit(PowerConsumer):
    def __init__(self, idx, hyd_kW, peak_kW, normal_kW):
        super().__init__(idx, peak_kW, normal_kW, "PUMP")
        self.design_hydraulic_kW = hyd_kW


class ChillerUnit(PowerConsumer):
    def __init__(self, idx, evap_cap_kW, peak_kW, normal_kW):
        super().__init__(idx, peak_kW, normal_kW, "CHLR")
        self.evap_capacity_kW = evap_cap_kW


class ITRowUnit(PowerConsumer):
    """Represents one IT row worth of load within a whitespace."""

    def __init__(self, idx: int, whitespace_id: int, load_kW: float):
        super().__init__(idx, peak_kW=load_kW, normal_kW=load_kW, kind="ITR")
        self.whitespace_id = whitespace_id


@dataclass
class ITWhiteSpace:
    id: int
    it_load_kW: float
    row_ids: List[int]


@dataclass
class StringSummary:
    id: int
    normal_load_kW: float
    design_capacity_kW: float
    unit_ids: List[str]
    units: List[PowerConsumer]


@dataclass
class UnitSummary:
    label: str
    kind: str
    unit_id: int
    normal_kW: float
    peak_kW: float
    capacity_kW: float | None
    whitespace_id: int | None


@dataclass
class StringReportRow:
    string_id: int
    normal_load_kW: float
    design_capacity_kW: float
    utilization: float
    components: List[UnitSummary]


@dataclass
class PowerStringAggregate:
    strings: List[StringSummary]
    total_peak_kW: float
    total_normal_kW: float
    string_normal_kW: List[float]
    string_design_cap_kW: List[float]
    config: RedundancyConfig
    total_strings: int
    active_strings: int
    units: List[PowerConsumer]


@dataclass
class PowerStringReport:
    table: List[StringReportRow]
    failure_case: Dict[str, object] | None


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def assign_dual_feeds(units: Sequence[PowerConsumer], N_strings: int, N_req: int):
    N_tot = len(units)
    if N_tot == 0 or N_strings == 0:
        return [False] * N_tot

    for i, unit in enumerate(units):
        unit.primary_string = (i % N_strings) + 1
        unit.secondary_string = None

    L_max = max(0, N_tot - N_req)
    buckets = [[] for _ in range(N_strings)]
    for unit in units:
        buckets[unit.primary_string - 1].append(unit)

    for s in range(N_strings):
        singles = [u for u in buckets[s] if u.secondary_string is None]
        excess = len(singles) - L_max
        if excess > 0:
            for i in range(excess):
                u = singles[i]
                sec = (s + 1) % N_strings
                u.secondary_string = sec + 1

    return [u.secondary_string is not None for u in units]


def summarize_strings(units: Sequence[PowerConsumer], strings_cfg: RedundancyConfig):
    N_req = strings_cfg.n_required
    N_tot = strings_cfg.n_total
    if N_tot < 1:
        N_tot = 1
        N_req = 1

    total_peak = sum(u.peak_kW for u in units)
    cap_per = total_peak / float(N_req) if N_req > 0 else 0.0

    loads = [0.0] * N_tot
    idlist = [[] for _ in range(N_tot)]
    unit_lists = [[] for _ in range(N_tot)]

    for u in units:
        p = u.primary_string or 1
        loads[p - 1] += u.normal_kW
        idlist[p - 1].append(f"{u.kind}{u.id}")
        unit_lists[p - 1].append(u)

    result = []
    for i in range(N_tot):
        result.append(StringSummary(i + 1, loads[i], cap_per, idlist[i], unit_lists[i]))
    return result


# ---------------------------------------------------------------------------
# IT whitespace distributor
# ---------------------------------------------------------------------------


def distribute_it_load(
    IT_kW: float,
    n_whitespaces: int,
    row_cfg: RedundancyConfig,
) -> Tuple[List[ITWhiteSpace], List[ITRowUnit]]:
    """Split the IT load per whitespace and per IT row.

    Returns a tuple ``(white_spaces, it_rows)``.
    """

    n_whitespaces = max(1, int(n_whitespaces))
    total_load = max(0.0, float(IT_kW))
    load_per_ws = total_load / float(n_whitespaces)

    load_per_row = (
        load_per_ws / float(row_cfg.n_required) if row_cfg.n_required > 0 else 0.0
    )

    white_spaces: List[ITWhiteSpace] = []
    rows: List[ITRowUnit] = []
    row_id = 1

    for ws_id in range(1, n_whitespaces + 1):
        ids_in_ws = []
        for _ in range(row_cfg.n_total):
            row_unit = ITRowUnit(idx=row_id, whitespace_id=ws_id, load_kW=load_per_row)
            rows.append(row_unit)
            ids_in_ws.append(row_id)
            row_id += 1
        white_spaces.append(ITWhiteSpace(ws_id, load_per_ws, ids_in_ws))

    return white_spaces, rows


# ---------------------------------------------------------------------------
# Sizing functions
# ---------------------------------------------------------------------------


def _size_crah_single(IT_kW, dT_air_K, SP_air_Pa, eta_fan, eta_motor, crah_cfg):
    crah_units = []
    Q_cwl_kW = 0.0
    P_fan_total_kW = 0.0

    if IT_kW <= 0:
        return crah_units, Q_cwl_kW, P_fan_total_kW

    rho_air = 1.2
    cp_air = 1005.0

    dT_air_K = max(1e-6, dT_air_K)
    eta_fan = max(1e-6, eta_fan)
    eta_motor = max(1e-6, eta_motor)

    mdot = (IT_kW * 1000.0) / (cp_air * dT_air_K)
    Vdot = mdot / rho_air

    eta_tot = max(1e-3, eta_fan * eta_motor)
    P_fan_W = Vdot * SP_air_Pa / eta_tot
    P_fan_total_kW = P_fan_W / 1000.0

    N_req = crah_cfg.n_required
    N_tot = crah_cfg.n_total

    fan_peak = P_fan_total_kW / float(N_req)
    fan_norm = P_fan_total_kW / float(N_tot)
    cap_per = IT_kW / float(N_req) + fan_peak

    for i in range(N_tot):
        crah_units.append(CRAHUnit(i + 1, cap_per, fan_peak, fan_norm))

    Q_cwl_kW = IT_kW + P_fan_total_kW
    return crah_units, Q_cwl_kW, P_fan_total_kW


def size_crah(
    IT_kW: float,
    dT_air_K: float,
    SP_air_Pa: float,
    eta_fan: float,
    eta_motor: float,
    crah_cfg: RedundancyConfig,
    white_spaces: Sequence[ITWhiteSpace] | None = None,
):
    """Size CRAH units.

    When ``white_spaces`` is provided each whitespace receives a copy of the CRAH
    configuration and the thermal load is divided automatically.
    """

    if white_spaces:
        crah_units: List[CRAHUnit] = []
        total_Q = 0.0
        total_P = 0.0
        idx = 1
        for ws in white_spaces:
            ws_units, ws_Q, ws_P = _size_crah_single(
                ws.it_load_kW, dT_air_K, SP_air_Pa, eta_fan, eta_motor, crah_cfg
            )
            for unit in ws_units:
                unit.id = idx
                unit.whitespace_id = ws.id
                crah_units.append(unit)
                idx += 1
            total_Q += ws_Q
            total_P += ws_P
        return crah_units, total_Q, total_P

    return _size_crah_single(IT_kW, dT_air_K, SP_air_Pa, eta_fan, eta_motor, crah_cfg)


def size_pumps(
    Q_cwl_kW,
    dT_water_K,
    head_m,
    eta_pump,
    eta_motor,
    pump_cfg: RedundancyConfig,
):
    pump_units = []
    Q_to_chiller_kW = Q_cwl_kW
    P_pump_total_kW = 0.0

    if Q_cwl_kW <= 0:
        return pump_units, Q_to_chiller_kW, P_pump_total_kW

    rho_w = 1000.0
    cp_w = 4180.0
    g = 9.81

    if dT_water_K <= 0:
        mdot_w = 0.0
    else:
        mdot_w = (Q_cwl_kW * 1000.0) / (cp_w * dT_water_K)
    Vdot_w = mdot_w / rho_w

    head_m = max(0.0, head_m)
    P_hyd_W = rho_w * g * head_m * Vdot_w
    P_hyd_kW = P_hyd_W / 1000.0

    eta_tot = max(1e-3, eta_pump * eta_motor)
    P_pump_total_kW = P_hyd_kW / eta_tot if head_m > 0 and mdot_w > 0 else 0.0

    N_req = pump_cfg.n_required
    N_tot = pump_cfg.n_total

    motor_peak = P_pump_total_kW / float(N_req)
    motor_norm = P_pump_total_kW / float(N_tot)
    hyd_per = P_hyd_kW / float(N_req)

    for i in range(N_tot):
        pump_units.append(PumpUnit(i + 1, hyd_per, motor_peak, motor_norm))

    Q_to_chiller_kW = Q_cwl_kW + P_pump_total_kW
    return pump_units, Q_to_chiller_kW, P_pump_total_kW


def size_chillers(Q_evap_kW, COP, ch_cfg: RedundancyConfig):
    ch_units = []
    Q_cond_kW = Q_evap_kW
    P_ch_total_kW = 0.0

    if Q_evap_kW <= 0 or COP <= 0:
        return ch_units, Q_cond_kW, P_ch_total_kW

    P_ch_total_kW = Q_evap_kW / float(COP)

    N_req = ch_cfg.n_required
    N_tot = ch_cfg.n_total

    comp_peak = P_ch_total_kW / float(N_req)
    comp_norm = P_ch_total_kW / float(N_tot)
    evap_cap = Q_evap_kW / float(N_req)

    for i in range(N_tot):
        ch_units.append(ChillerUnit(i + 1, evap_cap, comp_peak, comp_norm))

    Q_cond_kW = Q_evap_kW + P_ch_total_kW
    return ch_units, Q_cond_kW, P_ch_total_kW


# ---------------------------------------------------------------------------
# Global aggregator
# ---------------------------------------------------------------------------


def to_list(x: Iterable[PowerConsumer] | PowerConsumer | None):
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _balance_primary_strings(units: Sequence[PowerConsumer], active_strings: int) -> None:
    if active_strings <= 0:
        active_strings = 1

    loads = [0.0] * active_strings
    sorted_units = sorted(units, key=lambda u: u.normal_kW, reverse=True)
    for unit in sorted_units:
        idx = min(range(active_strings), key=lambda i: loads[i]) if loads else 0
        loads[idx] += unit.normal_kW
        unit.primary_string = idx + 1
        unit.secondary_string = None


def _clamp_primary_strings(units: Sequence[PowerConsumer], total_strings: int) -> None:
    total_strings = max(1, int(total_strings))
    for unit in units:
        p = getattr(unit, "primary_string", None)
        if p is None or p < 1:
            unit.primary_string = 1
        elif p > total_strings:
            unit.primary_string = total_strings


def _component_capacity(unit: PowerConsumer) -> float | None:
    for attr in ("design_capacity_kW", "design_hydraulic_kW", "evap_capacity_kW"):
        if hasattr(unit, attr):
            return getattr(unit, attr)
    if hasattr(unit, "peak_kW"):
        return unit.peak_kW
    return None


def aggregate_power_strings(
    crah_units=None,
    pump_units=None,
    chiller_units=None,
    it_rows=None,
    redundancy_strings: str | RedundancyConfig = "1+0",
    auto_balance: bool = True,
):
    if not isinstance(redundancy_strings, RedundancyConfig):
        cfg_strings = parse_redundancy(redundancy_strings)
    else:
        cfg_strings = redundancy_strings

    all_units = (
        to_list(it_rows)
        + to_list(crah_units)
        + to_list(pump_units)
        + to_list(chiller_units)
    )

    if len(all_units) == 0:
        empty = []
        return PowerStringAggregate(empty, 0.0, 0.0, empty, empty, cfg_strings, 0, 0, [])

    max_string_id = 0
    for u in all_units:
        if getattr(u, "primary_string", None) is not None:
            max_string_id = max(max_string_id, u.primary_string)
        if getattr(u, "secondary_string", None) is not None:
            max_string_id = max(max_string_id, u.secondary_string)

    N_req = cfg_strings.n_required
    N_tot = max(cfg_strings.n_total, max_string_id, 1)
    if N_req > N_tot:
        N_req = N_tot

    active_strings = max(1, N_req)

    if auto_balance:
        _balance_primary_strings(all_units, active_strings)
    else:
        _clamp_primary_strings(all_units, N_tot)

    total_peak_kW = sum(u.peak_kW for u in all_units)
    total_normal_kW = sum(u.normal_kW for u in all_units)
    cap_per = total_peak_kW / float(N_req) if N_req > 0 else 0.0

    loads = [0.0] * N_tot
    idlists = [[] for _ in range(N_tot)]
    unit_lists = [[] for _ in range(N_tot)]

    for u in all_units:
        p = getattr(u, "primary_string", 1) or 1
        idx = min(max(p - 1, 0), N_tot - 1)
        loads[idx] += u.normal_kW
        idlists[idx].append(f"{u.kind}{u.id}")
        unit_lists[idx].append(u)

    strings = []
    for i in range(N_tot):
        strings.append(StringSummary(i + 1, loads[i], cap_per, idlists[i], unit_lists[i]))

    return PowerStringAggregate(
        strings=strings,
        total_peak_kW=total_peak_kW,
        total_normal_kW=total_normal_kW,
        string_normal_kW=loads,
        string_design_cap_kW=[cap_per] * N_tot,
        config=cfg_strings,
        total_strings=N_tot,
        active_strings=active_strings,
        units=all_units,
    )


def _simulate_string_failure(aggregate: PowerStringAggregate, failed_string: int):
    if failed_string < 1 or failed_string > aggregate.total_strings:
        return {
            "failed_string": failed_string,
            "message": "Failed string outside configured range.",
        }

    survivors = [i for i in range(1, aggregate.total_strings + 1) if i != failed_string]
    loads = {sid: 0.0 for sid in survivors}
    lost_units: List[str] = []

    for unit in aggregate.units:
        primary = getattr(unit, "primary_string", 1) or 1
        target = primary
        if primary == failed_string:
            secondary = getattr(unit, "secondary_string", None)
            if secondary in survivors:
                target = secondary
            elif survivors:
                target = min(survivors, key=lambda sid: loads[sid])
            else:
                target = None
        if target in loads:
            loads[target] += unit.normal_kW
        else:
            lost_units.append(f"{unit.kind}{unit.id}")

    remaining_required = max(1, min(aggregate.active_strings, len(survivors)))
    new_cap = (
        aggregate.total_peak_kW / float(remaining_required)
        if remaining_required > 0
        else 0.0
    )

    return {
        "failed_string": failed_string,
        "redistributed_normal_kW": loads,
        "lost_units": lost_units,
        "design_capacity_per_string_kW": new_cap,
    }


def build_power_string_report(
    aggregate: PowerStringAggregate, failed_string: int | None = None
) -> PowerStringReport:
    rows: List[StringReportRow] = []

    for summary in aggregate.strings:
        units: List[UnitSummary] = []
        for unit in summary.units:
            units.append(
                UnitSummary(
                    label=f"{unit.kind}{unit.id}",
                    kind=unit.kind,
                    unit_id=unit.id,
                    normal_kW=unit.normal_kW,
                    peak_kW=unit.peak_kW,
                    capacity_kW=_component_capacity(unit),
                    whitespace_id=getattr(unit, "whitespace_id", None),
                )
            )

        utilization = (
            summary.normal_load_kW / summary.design_capacity_kW
            if summary.design_capacity_kW > 0
            else 0.0
        )

        rows.append(
            StringReportRow(
                string_id=summary.id,
                normal_load_kW=summary.normal_load_kW,
                design_capacity_kW=summary.design_capacity_kW,
                utilization=utilization,
                components=units,
            )
        )

    failure_case = (
        _simulate_string_failure(aggregate, failed_string)
        if failed_string is not None
        else None
    )

    return PowerStringReport(table=rows, failure_case=failure_case)


__all__ = [
    "RedundancyConfig",
    "parse_redundancy",
    "PowerConsumer",
    "CRAHUnit",
    "PumpUnit",
    "ChillerUnit",
    "ITRowUnit",
    "ITWhiteSpace",
    "UnitSummary",
    "StringReportRow",
    "StringSummary",
    "PowerStringAggregate",
    "PowerStringReport",
    "assign_dual_feeds",
    "summarize_strings",
    "distribute_it_load",
    "size_crah",
    "size_pumps",
    "size_chillers",
    "aggregate_power_strings",
    "build_power_string_report",
]
