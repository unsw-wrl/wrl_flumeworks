#!/usr/bin/env python3
"""Loopback-only SWAN/SWASH/XBeach job service for the wave-flume HTML viewer.

The service has no third-party Python dependencies.  It serves the generated
viewer, prepares one-dimensional SWAN or SWASH cases from the browser payload,
runs the selected local executable, and returns the toe wave conditions.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import queue
import re
import socket
import shutil
import struct
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


SERVICE_VERSION = "0.3.3"
API_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 2_000_000
MAX_LOG_CHARS = 40_000
ROOT = Path(__file__).resolve().parent
DEFAULT_VIEWER = ROOT / "wave_flume_bathymetry_viewer.html"
DEFAULT_RUNS = ROOT / "wave_model_runs"
DEFAULT_CONFIG = ROOT / "wave_model_config.json"


class CaseError(ValueError):
    """A case cannot be prepared or run safely."""


@dataclass(frozen=True)
class Engine:
    name: str
    executable: str | None
    run_supported: bool
    purpose: str
    runtime_paths: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "executable": self.executable,
            "runSupported": self.run_supported,
            "purpose": self.purpose,
        }

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self.runtime_paths:
            existing = environment.get("PATH", "")
            prefix = os.pathsep.join(self.runtime_paths)
            environment["PATH"] = prefix + (os.pathsep + existing if existing else "")
        return environment


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise CaseError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CaseError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise CaseError(f"{label} must be finite.")
    return number


def discover_executable(explicit: str | None, names: tuple[str, ...]) -> str | None:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise SystemExit(f"Configured executable was not found: {candidate}")
        return str(candidate)
    for name in names:
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())
    return None


def normalise_runtime_paths(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise SystemExit(f"{label} must be a path or a JSON array of paths.")
    paths: list[str] = []
    for value in values:
        candidate = Path(value).expanduser().resolve()
        if not candidate.is_dir():
            raise SystemExit(f"Configured runtime directory was not found: {candidate}")
        resolved = str(candidate)
        if resolved not in paths:
            paths.append(resolved)
    return tuple(paths)


def merge_runtime_paths(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for path in group:
            if path not in merged:
                merged.append(path)
    return tuple(merged)


def normalise_bathymetry(raw: Any) -> list[dict[str, float]]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise CaseError("At least two bathymetry points are required.")
    points: list[dict[str, float]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CaseError(f"Bathymetry point {index + 1} is invalid.")
        points.append(
            {
                "chainage": finite_number(item.get("chainage"), f"Bathymetry point {index + 1} chainage"),
                "elevation": finite_number(item.get("elevation"), f"Bathymetry point {index + 1} elevation"),
            }
        )
    points.sort(key=lambda point: point["chainage"])
    for first, second in zip(points, points[1:]):
        if abs(second["chainage"] - first["chainage"]) < 1e-9:
            raise CaseError("Bathymetry chainages must be unique.")
    return points


def interpolate_elevation(points: list[dict[str, float]], chainage: float) -> float:
    if chainage < points[0]["chainage"] - 1e-9 or chainage > points[-1]["chainage"] + 1e-9:
        raise CaseError(
            f"Chainage {chainage:g} m is outside the bathymetry range "
            f"{points[0]['chainage']:g} to {points[-1]['chainage']:g} m."
        )
    for point in points:
        if abs(point["chainage"] - chainage) < 1e-9:
            return point["elevation"]
    for first, second in zip(points, points[1:]):
        if first["chainage"] <= chainage <= second["chainage"]:
            fraction = (chainage - first["chainage"]) / (second["chainage"] - first["chainage"])
            return first["elevation"] + fraction * (second["elevation"] - first["elevation"])
    raise CaseError(f"Could not interpolate bathymetry at chainage {chainage:g} m.")


def chainages_at_elevation(points: list[dict[str, float]], elevation: float) -> list[float]:
    candidates: list[float] = []
    for point in points:
        if abs(point["elevation"] - elevation) < 1e-9:
            candidates.append(point["chainage"])
    for first, second in zip(points, points[1:]):
        delta = second["elevation"] - first["elevation"]
        low, high = sorted((first["elevation"], second["elevation"]))
        if abs(delta) < 1e-12 or not (low < elevation < high):
            continue
        fraction = (elevation - first["elevation"]) / delta
        candidates.append(first["chainage"] + fraction * (second["chainage"] - first["chainage"]))
    return sorted(set(round(value, 10) for value in candidates))


def select_boundary_chainage(points: list[dict[str, float]], elevation: float, toe_chainage: float) -> float:
    candidates = chainages_at_elevation(points, elevation)
    offshore = [value for value in candidates if value > toe_chainage + 1e-9]
    if not offshore:
        raise CaseError(
            f"Wave-stats elevation {elevation:g} m AHD does not intersect the bathymetry offshore "
            f"of toe chainage {toe_chainage:g} m."
        )
    return min(offshore, key=lambda value: value - toe_chainage)


def normalise_conditions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise CaseError("Select at least one wave condition.")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CaseError(f"Wave condition {index + 1} is invalid.")
        identifier = str(item.get("conditionId", "")).strip()
        if not identifier:
            raise CaseError(f"Wave condition {index + 1} has no Condition ID.")
        if identifier in seen:
            raise CaseError(f"Condition ID {identifier!r} is duplicated.")
        seen.add(identifier)
        condition = {
            "conditionId": identifier,
            "waterLevel": finite_number(item.get("waterLevel"), f"Condition {identifier} water level"),
            "statsDepth": finite_number(item.get("statsDepth"), f"Condition {identifier} wave-stats depth"),
            "waveHeight": finite_number(item.get("waveHeight"), f"Condition {identifier} Hm0"),
            "period": finite_number(item.get("period"), f"Condition {identifier} Tp"),
        }
        if condition["waveHeight"] <= 0 or condition["period"] <= 0:
            raise CaseError(f"Condition {identifier} Hm0 and Tp must be positive.")
        result.append(condition)
    return result


def validate_case(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CaseError("The request body must be a JSON object.")
    engine = str(payload.get("engine", "")).lower()
    if engine not in {"swan", "swash", "xbeach"}:
        raise CaseError("Engine must be 'swan', 'swash', or 'xbeach'.")
    bathymetry = normalise_bathymetry(payload.get("bathymetry"))
    conditions = normalise_conditions(payload.get("conditions"))
    structure = payload.get("structure")
    if not isinstance(structure, dict):
        raise CaseError("Structure settings are required.")
    toe_chainage = finite_number(structure.get("toeChainage"), "Seawall toe chainage")
    interpolate_elevation(bathymetry, toe_chainage)
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    spacing = finite_number(options.get("gridSpacing", 1.0), "Grid spacing")
    if not 0.05 <= spacing <= 20:
        raise CaseError("Grid spacing must be between 0.05 m and 20 m.")
    swan_breaking_coefficient = finite_number(
        options.get("swanBreakingCoefficient", 0.73), "SWAN breaking coefficient"
    )
    if not 0.1 <= swan_breaking_coefficient <= 1.5:
        raise CaseError("SWAN breaking coefficient must be between 0.10 and 1.50.")
    swash_periods = finite_number(options.get("swashPeriods", 100), "SWASH analysis length")
    if not 20 <= swash_periods <= 1000:
        raise CaseError("SWASH analysis length must be between 20 and 1000 peak periods.")
    swash_animation_periods = finite_number(
        options.get("swashAnimationPeriods", 20), "SWASH animation length"
    )
    if not 1 <= swash_animation_periods <= 50:
        raise CaseError("SWASH animation length must be between 1 and 50 peak periods.")
    xbeach_periods = finite_number(options.get("xbeachPeriods", 1000), "XBeach analysis length")
    if not 20 <= xbeach_periods <= 5000:
        raise CaseError("XBeach analysis length must be between 20 and 5000 peak periods.")
    return {
        "engine": engine,
        "bathymetry": bathymetry,
        "conditions": conditions,
        "structure": {"toeChainage": toe_chainage},
        "options": {
            "gridSpacing": spacing,
            "swanBreakingCoefficient": swan_breaking_coefficient,
            "swashPeriods": int(round(swash_periods)),
            "swashAnimationPeriods": int(round(swash_animation_periods)),
            "xbeachPeriods": int(round(xbeach_periods)),
        },
    }


def safe_condition_slug(identifier: str, index: int) -> str:
    clean = "".join(character if character.isalnum() or character in "-_" else "_" for character in identifier)
    return f"c{index + 1:02d}_{clean[:24] or 'condition'}"


def write_swan_case(case_dir: Path, model_case: dict[str, Any], condition: dict[str, Any], index: int) -> dict[str, Any]:
    points = model_case["bathymetry"]
    toe_chainage = model_case["structure"]["toeChainage"]
    boundary_chainage = select_boundary_chainage(points, condition["statsDepth"], toe_chainage)
    length = boundary_chainage - toe_chainage
    requested_spacing = model_case["options"]["gridSpacing"]
    cells = max(20, min(4000, math.ceil(length / requested_spacing)))
    spacing = length / cells
    samples: list[dict[str, float]] = []
    for sample in range(cells + 1):
        x = sample * spacing
        chainage = boundary_chainage - x
        elevation = interpolate_elevation(points, chainage)
        depth = condition["waterLevel"] - elevation
        if depth <= 0.05:
            raise CaseError(
                f"Condition {condition['conditionId']} is dry or too shallow at chainage {chainage:.3f} m "
                f"(depth {depth:.3f} m). Move the toe offshore or review the water level."
            )
        samples.append({"x": x, "chainage": chainage, "elevation": elevation, "depth": depth})

    case_dir.mkdir(parents=True, exist_ok=False)
    bottom_file = case_dir / "bottom.dep"
    bottom_file.write_text("\n".join(f"{sample['depth']:.8f}" for sample in samples) + "\n", encoding="ascii")

    peak_frequency = 1.0 / condition["period"]
    flow = max(0.02, min(0.08, peak_frequency / 3.0))
    fhigh = max(0.5, min(2.0, peak_frequency * 6.0))
    breaking_coefficient = model_case["options"]["swanBreakingCoefficient"]
    command = f"""$ Generated by Wave Flume local model service {SERVICE_VERSION}
PROJECT 'WRLFLUME' '{index + 1:03d}'
SET level=0.0 CARTESIAN
MODE STATIONARY ONEDIMENSIONAL
CGRID REGULAR 0.0 0.0 0.0 {length:.8f} 0.0 {cells} 0 SECTOR -10.0 10.0 40 {flow:.6f} {fhigh:.6f} 48
INPGRID BOTTOM REGULAR 0.0 0.0 0.0 {cells} 0 {spacing:.8f} 0.0
READINP BOTTOM 1.0 'bottom.dep' 1 0 FREE
BOUND SHAPESPEC JONSWAP 3.3 PEAK
BOUNDSPEC SIDE WEST CCW CONSTANT PAR {condition['waveHeight']:.8f} {condition['period']:.8f} 0.0 2.0
GEN3 WESTHUYSEN
OFF QUAD
SETUP
BREAKING CONSTANT 1.0 {breaking_coefficient:.6f}
FRICTION JONSWAP 0.038
TRIAD
CURVE 'PROFILE' 0.0 0.0 {cells} {length:.8f} 0.0
TABLE 'PROFILE' NOHEADER 'results.tab' XP DEPTH HSIGN RTP TMM10 TM01 SETUP
COMPUTE
STOP
"""
    (case_dir / "INPUT").write_text(command, encoding="ascii", newline="\n")
    metadata = {
        "condition": condition,
        "boundaryChainage": boundary_chainage,
        "toeChainage": toe_chainage,
        "domainLength": length,
        "cells": cells,
        "gridSpacing": spacing,
        "swanBreakingCoefficient": breaking_coefficient,
        "toeElevation": samples[-1]["elevation"],
        "toeStillWaterDepth": samples[-1]["depth"],
        "profile": samples,
    }
    (case_dir / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_swan_results(path: Path) -> dict[str, float]:
    if not path.is_file():
        raise CaseError("SWAN completed without creating results.tab.")
    rows: list[list[float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("$", "%", "*")):
            continue
        try:
            values = [float(value) for value in stripped.split()]
        except ValueError:
            continue
        if len(values) >= 7 and all(math.isfinite(value) and abs(value) < 1e20 for value in values[:7]):
            rows.append(values)
    if not rows:
        raise CaseError("SWAN results.tab did not contain valid profile output.")
    xp, depth, hm0, tp, tm_minus_10, tm01, setup = rows[-1][:7]
    return {
        "x": xp,
        "depth": depth,
        "hm0": hm0,
        "tp": tp,
        "tmMinus10": tm_minus_10,
        "tm01": tm01,
        "setup": setup,
    }


def format_swash_time(seconds: float) -> str:
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}{minutes:02d}{whole_seconds:02d}.{milliseconds:03d}"


def write_swash_case(case_dir: Path, model_case: dict[str, Any], condition: dict[str, Any], index: int) -> dict[str, Any]:
    points = model_case["bathymetry"]
    toe_chainage = model_case["structure"]["toeChainage"]
    boundary_chainage = select_boundary_chainage(points, condition["statsDepth"], toe_chainage)
    length = boundary_chainage - toe_chainage
    requested_spacing = model_case["options"]["gridSpacing"]
    cells = max(40, min(8000, math.ceil(length / requested_spacing)))
    spacing = length / cells
    samples: list[dict[str, float]] = []
    for sample in range(cells + 1):
        x = sample * spacing
        chainage = boundary_chainage - x
        elevation = interpolate_elevation(points, chainage)
        depth = condition["waterLevel"] - elevation
        if depth <= 0.05:
            raise CaseError(
                f"Condition {condition['conditionId']} is dry or too shallow at chainage {chainage:.3f} m "
                f"(depth {depth:.3f} m). Move the toe offshore or review the water level."
            )
        samples.append({"x": x, "chainage": chainage, "elevation": elevation, "depth": depth})

    analysis_periods = model_case["options"]["swashPeriods"]
    analysis_seconds = analysis_periods * condition["period"]
    spinup_periods = 20
    spinup_seconds = spinup_periods * condition["period"]
    total_seconds = spinup_seconds + analysis_seconds
    maximum_depth = max(sample["depth"] for sample in samples)
    time_step = max(0.002, min(0.05, 0.35 * spacing / math.sqrt(9.81 * maximum_depth)))
    output_step = min(0.25, condition["period"] / 40.0)
    animation_periods = min(model_case["options"]["swashAnimationPeriods"], analysis_periods)
    animation_seconds = animation_periods * condition["period"]
    animation_start_seconds = total_seconds - animation_seconds
    animation_intervals = max(2, min(cells, 400))
    toe_x = length

    case_dir.mkdir(parents=True, exist_ok=False)
    bottom_file = case_dir / "bottom.bot"
    # SWASH bottom levels are positive downward; input elevations are positive upward AHD.
    bottom_file.write_text("\n".join(f"{-sample['elevation']:.8f}" for sample in samples) + "\n", encoding="ascii")

    command = f"""$ Generated by Wave Flume local model service {SERVICE_VERSION}
PROJECT 'WRLFLUME' '{index + 1:03d}'
MODE NONSTATIONARY ONEDIMENSIONAL
SET LEVEL = {condition['waterLevel']:.8f}
CGRID REGULAR 0.0 0.0 0.0 {length:.8f} 0.0 {cells} 0
VERTICAL 2
INPGRID BOTTOM REGULAR 0.0 0.0 0.0 {cells} 0 {spacing:.8f} 0.0
READINP BOTTOM 1.0 'bottom.bot' 1 0 FREE
INITIAL ZERO
BOUND SHAPESPEC JONSWAP 3.3 SIG PEAK DSPR POWER
BOUNDCOND SIDE WEST CCW BTYPE WEAKREFL &
  SMOOTHING {2 * condition['period']:.3f} SEC ADDBOUNDWAVE &
  CONSTANT SPECTRUM {condition['waveHeight']:.8f} {condition['period']:.8f} 0.0 2.0 {total_seconds:.3f} SEC
BOUNDCOND SIDE EAST CCW BTYPE RADIATION
BREAKING
NONHYDROSTATIC BOX PREC ILU
DISCRETIZATION UPWIND MOMENTUM
TIMEINTEGRATION 0.1 0.5
POINTS 'TOE' {toe_x:.8f} 0.0
CURVE 'SURFACE' 0.0 0.0 {animation_intervals} {length:.8f} 0.0
QUANTITY HS SETUP DUR {analysis_seconds:.3f} SEC
TABLE 'TOE' NOHEADER 'toe.tbl' TSEC WATL OUTPUT {format_swash_time(spinup_seconds)} {output_step:.6f} SEC
TABLE 'TOE' NOHEADER 'stats.tab' XP HS SETUP
TABLE 'SURFACE' NOHEADER 'surface.tbl' TSEC XP WATL OUTPUT {format_swash_time(animation_start_seconds)} {output_step:.6f} SEC
TEST 1 0
COMPUTE 000000.000 {time_step:.6f} SEC {format_swash_time(total_seconds)}
STOP
"""
    (case_dir / "INPUT").write_text(command, encoding="ascii", newline="\n")
    metadata = {
        "condition": condition,
        "boundaryChainage": boundary_chainage,
        "toeChainage": toe_chainage,
        "domainLength": length,
        "cells": cells,
        "gridSpacing": spacing,
        "toeX": toe_x,
        "toeElevation": samples[-1]["elevation"],
        "toeStillWaterDepth": samples[-1]["depth"],
        "verticalLayers": 2,
        "spinupPeriods": spinup_periods,
        "analysisPeriods": analysis_periods,
        "spinupSeconds": spinup_seconds,
        "analysisSeconds": analysis_seconds,
        "animationPeriods": animation_periods,
        "animationSeconds": animation_seconds,
        "animationStartSeconds": animation_start_seconds,
        "animationIntervals": animation_intervals,
        "simulationSeconds": total_seconds,
        "timeStep": time_step,
        "outputStep": output_step,
        "profile": samples,
    }
    (case_dir / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def write_xbeach_case(case_dir: Path, model_case: dict[str, Any], condition: dict[str, Any], index: int) -> dict[str, Any]:
    """Prepare a fast-1D XBeach surfbeat case with a fixed bed."""
    points = model_case["bathymetry"]
    toe_chainage = model_case["structure"]["toeChainage"]
    boundary_chainage = select_boundary_chainage(points, condition["statsDepth"], toe_chainage)
    length = boundary_chainage - toe_chainage
    requested_spacing = model_case["options"]["gridSpacing"]
    cells = max(20, min(10_000, math.ceil(length / requested_spacing)))
    spacing = length / cells
    samples: list[dict[str, float]] = []
    for sample in range(cells + 1):
        x = sample * spacing
        chainage = boundary_chainage - x
        elevation = interpolate_elevation(points, chainage)
        depth = condition["waterLevel"] - elevation
        if depth <= 0.05:
            raise CaseError(
                f"Condition {condition['conditionId']} is dry or too shallow at chainage {chainage:.3f} m "
                f"(depth {depth:.3f} m). Move the toe offshore or review the water level."
            )
        samples.append({"x": x, "chainage": chainage, "elevation": elevation, "depth": depth})

    analysis_periods = model_case["options"]["xbeachPeriods"]
    spinup_periods = 20
    spinup_seconds = spinup_periods * condition["period"]
    analysis_seconds = analysis_periods * condition["period"]
    total_seconds = spinup_seconds + analysis_seconds
    output_step = max(0.25, min(1.0, condition["period"] / 20.0))
    expected_records = int(math.floor(analysis_seconds / output_step)) + 1
    boundary_step = max(0.1, min(1.0, condition["period"] / 20.0))
    toe_x = length

    case_dir.mkdir(parents=True, exist_ok=False)
    # XBeach fast-1D input is one row of nx+1 positive-up bed levels.
    (case_dir / "bed.dep").write_text(
        " ".join(f"{sample['elevation']:.8f}" for sample in samples) + "\n", encoding="ascii"
    )
    (case_dir / "jonswap.txt").write_text(
        (
            f"{condition['waveHeight']:.8f} {condition['period']:.8f} 270.0 3.3 1000 "
            f"{total_seconds:.8f} {boundary_step:.8f}\n"
        ),
        encoding="ascii",
        newline="\n",
    )
    command = f"""% Generated by Wave Flume local model service {SERVICE_VERSION}
wavemodel = surfbeat
wbctype = jonstable
bcfile = jonswap.txt
nx = {cells}
ny = 0
vardx = 0
dx = {spacing:.8f}
dy = 1
depfile = bed.dep
posdwn = 0
alfa = 0
zs0 = {condition['waterLevel']:.8f}
thetamin = 260
thetamax = 280
dtheta = 20
thetanaut = 1
tstop = {total_seconds:.8f}
taper = {min(100.0, spinup_seconds):.8f}
random = 0
sedtrans = 0
morphology = 0
outputformat = fortran
outputprecision = double
tstart = {spinup_seconds:.8f}
tintp = {output_step:.8f}
npoints = 1
{toe_x:.8f} 0
npointvar = 2
H
zs
timings = 1
"""
    (case_dir / "params.txt").write_text(command, encoding="ascii", newline="\n")
    metadata = {
        "condition": condition,
        "boundaryChainage": boundary_chainage,
        "toeChainage": toe_chainage,
        "domainLength": length,
        "cells": cells,
        "gridSpacing": spacing,
        "toeX": toe_x,
        "toeElevation": samples[-1]["elevation"],
        "toeStillWaterDepth": samples[-1]["depth"],
        "spinupPeriods": spinup_periods,
        "analysisPeriods": analysis_periods,
        "spinupSeconds": spinup_seconds,
        "analysisSeconds": analysis_seconds,
        "simulationSeconds": total_seconds,
        "outputStep": output_step,
        "expectedRecords": expected_records,
        "profile": samples,
    }
    (case_dir / "case.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def parse_xbeach_results(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Parse XBeach double-precision point output and convert Hrms to Hm0."""
    if not path.is_file():
        raise CaseError("XBeach completed without creating point001.dat.")
    raw = path.read_bytes()
    record_size = 3 * 8  # time, H (Hrms), zs
    if len(raw) < record_size or len(raw) % record_size:
        raise CaseError("XBeach point001.dat has an unexpected binary layout.")
    rows = [row for row in struct.iter_unpack("<ddd", raw) if all(math.isfinite(value) for value in row)]
    if not rows:
        raise CaseError("XBeach point001.dat did not contain valid toe output.")
    h_values = [max(0.0, row[1]) for row in rows]
    z_values = [row[2] for row in rows]
    hrms = math.sqrt(sum(value * value for value in h_values) / len(h_values))
    setup = sum(z_values) / len(z_values) - metadata["condition"]["waterLevel"]
    return {
        "x": metadata["toeX"],
        "depth": metadata["toeStillWaterDepth"] + setup,
        "hm0": math.sqrt(2.0) * hrms,
        "tp": metadata["condition"]["period"],
        "tmMinus10": None,
        "tm01": None,
        "setup": setup,
        "samples": len(rows),
        "sampleStep": metadata["outputStep"],
        "modelNote": "XBeach surfbeat: Hm0 = √2 × RMS(Hrms); Tp is the imposed peak period. Spectral mean periods are not resolved.",
    }


def _fft(values: list[complex]) -> list[complex]:
    size = len(values)
    if size == 0 or size & (size - 1):
        raise ValueError("FFT input length must be a non-zero power of two.")
    output = list(values)
    position = 0
    for index in range(1, size):
        bit = size >> 1
        while position & bit:
            position ^= bit
            bit >>= 1
        position ^= bit
        if index < position:
            output[index], output[position] = output[position], output[index]
    length = 2
    while length <= size:
        angle = -2.0 * math.pi / length
        root = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, size, length):
            factor = 1 + 0j
            for offset in range(half):
                even = output[start + offset]
                odd = output[start + offset + half] * factor
                output[start + offset] = even + odd
                output[start + offset + half] = even - odd
                factor *= root
        length *= 2
    return output


def parse_swash_stationary_results(path: Path) -> tuple[float, float] | None:
    """Return SWASH's native (Hs, setup) values from the final stationary row."""
    if not path.is_file():
        return None
    final: tuple[float, float] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values = [float(value) for value in line.split()]
        except ValueError:
            continue
        if len(values) >= 3 and all(math.isfinite(value) and abs(value) < 1e20 for value in values[:3]):
            final = (values[1], values[2])
    return final


def parse_swash_results(path: Path, metadata: dict[str, Any], stats_path: Path | None = None) -> dict[str, float]:
    if not path.is_file():
        raise CaseError("SWASH completed without creating toe.tbl.")
    rows: list[tuple[float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("$", "%", "*")):
            continue
        try:
            values = [float(value) for value in stripped.split()]
        except ValueError:
            continue
        if len(values) >= 2 and all(math.isfinite(value) and abs(value) < 1e20 for value in values[:2]):
            rows.append((values[0], values[1]))
    if len(rows) < 128:
        raise CaseError("SWASH toe.tbl did not contain enough valid water-level samples for spectral statistics.")

    differences = sorted(second[0] - first[0] for first, second in zip(rows, rows[1:]) if second[0] > first[0])
    if not differences:
        raise CaseError("SWASH toe.tbl has no increasing output times.")
    sample_step = differences[len(differences) // 2]
    if len(rows) > 32768:
        rows = rows[-32768:]
    count = len(rows)
    fft_size = 1 << (count - 1).bit_length()
    if count < 128:
        raise CaseError("SWASH toe.tbl contains too few samples for the FFT.")
    levels = [value for _, value in rows]
    mean_level = sum(levels) / count

    # Remove mean and linear drift, then apply a Hann window before spectral moments.
    centre = (count - 1) / 2
    denominator = sum((index - centre) ** 2 for index in range(count))
    slope = sum((index - centre) * (value - mean_level) for index, value in enumerate(levels)) / denominator
    detrended = [value - mean_level - slope * (index - centre) for index, value in enumerate(levels)]
    variance = sum(value * value for value in detrended) / count
    windowed: list[complex] = [
        complex(value * (0.5 - 0.5 * math.cos(2 * math.pi * index / (count - 1))), 0.0)
        for index, value in enumerate(detrended)
    ]
    windowed.extend([0j] * (fft_size - count))
    transform = _fft(windowed)
    bins: list[tuple[float, float]] = []
    for index in range(1, fft_size // 2 + 1):
        frequency = index / (fft_size * sample_step)
        energy = abs(transform[index]) ** 2
        if energy > 0 and math.isfinite(energy):
            bins.append((frequency, energy))
    if not bins:
        raise CaseError("SWASH toe water levels produced no usable spectral energy.")

    input_period = metadata["condition"]["period"]
    # Report sea-swell period statistics around the imposed peak. Surf-beat and
    # setup energy at very low frequencies is retained as a diagnostic, but it
    # must not dominate Tm-1,0 or move Tp to an infragravity peak.
    lower_frequency = 0.67 / input_period
    upper_frequency = 2.5 / input_period
    wave_bins = [item for item in bins if lower_frequency <= item[0] <= upper_frequency]
    if len(wave_bins) < 2:
        raise CaseError("SWASH toe water levels did not contain enough energy in the sea-swell frequency band.")
    peak_frequency = max(wave_bins, key=lambda item: item[1])[0]
    moment_zero = sum(energy for _, energy in wave_bins)
    moment_one = sum(frequency * energy for frequency, energy in wave_bins)
    moment_minus_one = sum(energy / frequency for frequency, energy in wave_bins)
    total_energy = sum(energy for _, energy in bins)
    low_frequency_energy = sum(energy for frequency, energy in bins if frequency < lower_frequency)

    # WATL is the instantaneous surface displacement relative to SET LEVEL,
    # not an absolute AHD water level. Prefer SWASH's own duration-averaged HS
    # and SETUP outputs; retain the time-series estimates as a fallback.
    native = parse_swash_stationary_results(stats_path) if stats_path else None
    setup = native[1] if native else mean_level
    hm0 = native[0] if native else 4.0 * math.sqrt(max(0.0, variance))
    return {
        "x": metadata["toeX"],
        "depth": metadata["toeStillWaterDepth"] + setup,
        "hm0": hm0,
        "tp": 1.0 / peak_frequency,
        "tmMinus10": moment_minus_one / moment_zero,
        "tm01": moment_zero / moment_one,
        "setup": setup,
        "samples": count,
        "sampleStep": sample_step,
        "periodBandMin": 1.0 / upper_frequency,
        "periodBandMax": 1.0 / lower_frequency,
        "lowFrequencyEnergyFraction": low_frequency_energy / total_energy if total_energy else 0.0,
    }


def write_swash_animation_artifact(
    path: Path, metadata: dict[str, Any], output_path: Path
) -> dict[str, Any]:
    """Convert the retained SWASH profile table to compact, compressed JSON."""
    if not path.is_file():
        raise CaseError("SWASH completed without creating surface.tbl for the surface animation.")
    grouped: dict[float, list[tuple[float, float]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("$", "%", "*")):
            continue
        try:
            values = [float(value) for value in stripped.split()]
        except ValueError:
            continue
        if len(values) < 3 or not all(math.isfinite(value) and abs(value) < 1e20 for value in values[:3]):
            continue
        timestamp, x, water_offset = values[:3]
        grouped.setdefault(round(timestamp, 6), []).append((x, water_offset))
    if not grouped:
        raise CaseError("SWASH surface.tbl did not contain valid profile frames.")

    expected_points = metadata["animationIntervals"] + 1
    frames: list[tuple[float, list[tuple[float, float]]]] = []
    for timestamp in sorted(grouped):
        values = sorted(grouped[timestamp], key=lambda item: item[0])
        if len(values) == expected_points:
            frames.append((timestamp, values))
    if not frames:
        counts = sorted({len(values) for values in grouped.values()})
        raise CaseError(
            f"SWASH surface.tbl did not contain a complete {expected_points}-point frame "
            f"(found frame sizes {counts})."
        )

    bed_points = sorted(
        ({"chainage": item["chainage"], "elevation": item["elevation"]} for item in metadata["profile"]),
        key=lambda item: item["chainage"],
    )
    x_values = [value[0] for value in frames[0][1]]
    minimum_chainage = bed_points[0]["chainage"]
    maximum_chainage = bed_points[-1]["chainage"]
    coordinate_tolerance = max(1e-6, metadata["domainLength"] * 1e-6)
    chainages: list[float] = []
    for x in x_values:
        chainage = metadata["boundaryChainage"] - x
        if chainage < minimum_chainage:
            if minimum_chainage - chainage > coordinate_tolerance:
                raise CaseError(
                    f"SWASH surface coordinate maps to chainage {chainage:.9g} m, outside the model domain."
                )
            chainage = minimum_chainage
        elif chainage > maximum_chainage:
            if chainage - maximum_chainage > coordinate_tolerance:
                raise CaseError(
                    f"SWASH surface coordinate maps to chainage {chainage:.9g} m, outside the model domain."
                )
            chainage = maximum_chainage
        chainages.append(chainage)
    bed_elevations = [interpolate_elevation(bed_points, chainage) for chainage in chainages]
    still_water_level = metadata["condition"]["waterLevel"]
    frame_times = [timestamp for timestamp, _ in frames]
    time_differences = sorted(
        second - first for first, second in zip(frame_times, frame_times[1:]) if second > first
    )
    actual_output_step = (
        time_differences[len(time_differences) // 2] if time_differences else metadata["outputStep"]
    )
    surface_levels = [
        [round(still_water_level + water_offset, 6) for _, water_offset in values]
        for _, values in frames
    ]
    payload = {
        "format": "wrl-swash-surface-animation",
        "version": 1,
        "conditionId": metadata["condition"]["conditionId"],
        "peakPeriod": metadata["condition"]["period"],
        "stillWaterLevel": still_water_level,
        "retainedPeriods": metadata["animationPeriods"],
        "outputStep": actual_output_step,
        "frameTimes": [round(timestamp, 6) for timestamp in frame_times],
        "chainages": [round(value, 6) for value in chainages],
        "bedElevations": [round(value, 6) for value in bed_elevations],
        "surfaceElevations": surface_levels,
    }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with gzip.open(output_path, "wb", compresslevel=6) as stream:
        stream.write(encoded)
    return {
        "available": True,
        "frames": len(frames),
        "points": len(chainages),
        "retainedPeriods": metadata["animationPeriods"],
        "outputStep": actual_output_step,
        "compressedBytes": output_path.stat().st_size,
    }


class JobStore:
    def __init__(self, runs_dir: Path, engines: dict[str, Engine]) -> None:
        self.runs_dir = runs_dir.resolve()
        self.engines = engines
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def create(self, model_case: dict[str, Any]) -> dict[str, Any]:
        identifier = uuid.uuid4().hex[:12]
        job = {
            "id": identifier,
            "status": "queued",
            "engine": model_case["engine"],
            "createdAt": time.time(),
            "updatedAt": time.time(),
            "progress": 0,
            "message": "Queued",
            "results": [],
            "error": None,
        }
        with self.lock:
            self.jobs[identifier] = job
        thread = threading.Thread(target=self._run, args=(identifier, model_case), daemon=True)
        thread.start()
        return dict(job)

    def get(self, identifier: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(identifier)
            return json.loads(json.dumps(job)) if job else None

    def animation_path(self, identifier: str, result_index: int) -> Path | None:
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or result_index < 0 or result_index >= len(job.get("results", [])):
                return None
            result = job["results"][result_index]
            if result.get("engine") != "swash" or not result.get("animation", {}).get("available"):
                return None
            case_folder = result.get("caseFolder")
        if not isinstance(case_folder, str):
            return None
        candidate = (ROOT / case_folder / "surface_animation.json.gz").resolve()
        try:
            candidate.relative_to(self.runs_dir)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _update(self, identifier: str, **changes: Any) -> None:
        with self.lock:
            self.jobs[identifier].update(changes, updatedAt=time.time())

    def _run_swash_process(
        self,
        identifier: str,
        engine: Engine,
        case_dir: Path,
        condition: dict[str, Any],
        condition_index: int,
        condition_total: int,
        metadata: dict[str, Any],
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            [engine.executable],
            cwd=case_dir,
            env=engine.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        simulation_seconds = metadata["simulationSeconds"]
        log_tail = ""
        last_percent = -1
        started = time.monotonic()
        reader_finished = False
        while process.poll() is None or not reader_finished or not output_queue.empty():
            if time.monotonic() - started > 7200:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired([engine.executable], 7200)
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                reader_finished = True
                continue
            log_tail = (log_tail + line)[-MAX_LOG_CHARS:]
            matches = re.findall(r"\[\s*(\d{1,3})%\]", line)
            if not matches:
                continue
            percent = max(0, min(100, int(matches[-1])))
            if percent <= last_percent:
                continue
            last_percent = percent
            simulated = simulation_seconds * percent / 100.0
            overall = min(99, int(100 * (condition_index + min(percent, 99) / 100.0) / condition_total))
            self._update(
                identifier,
                progress=overall,
                conditionProgress=percent,
                simulatedSeconds=round(simulated, 1),
                simulationSeconds=round(simulation_seconds, 1),
                message=(
                    f"Running SWASH condition {condition['conditionId']} "
                    f"({condition_index + 1}/{condition_total}) · simulated {percent}% "
                    f"({simulated:.0f} / {simulation_seconds:.0f} s)"
                ),
            )
        return subprocess.CompletedProcess([engine.executable], process.wait(), stdout=log_tail)

    def _run_xbeach_process(
        self,
        identifier: str,
        engine: Engine,
        case_dir: Path,
        condition: dict[str, Any],
        condition_index: int,
        condition_total: int,
        metadata: dict[str, Any],
    ) -> subprocess.CompletedProcess[str]:
        """Run XBeach while estimating progress from appended toe records."""
        process = subprocess.Popen(
            [engine.executable],
            cwd=case_dir,
            env=engine.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
            output_queue.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        log_tail = ""
        reader_finished = False
        last_percent = -1
        started = time.monotonic()
        output_path = case_dir / "point001.dat"
        expected_records = max(1, metadata["expectedRecords"])
        while process.poll() is None or not reader_finished or not output_queue.empty():
            if time.monotonic() - started > 7200:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired([engine.executable], 7200)
            try:
                line = output_queue.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line is None:
                reader_finished = True
            elif line:
                log_tail = (log_tail + line)[-MAX_LOG_CHARS:]
            records = output_path.stat().st_size // 24 if output_path.is_file() else 0
            percent = max(0, min(99, round(100 * records / expected_records)))
            if percent <= last_percent:
                continue
            last_percent = percent
            simulated = metadata["spinupSeconds"] + max(0, min(records, expected_records) - 1) * metadata["outputStep"]
            overall = min(99, int(100 * (condition_index + percent / 100.0) / condition_total))
            self._update(
                identifier,
                progress=overall,
                conditionProgress=percent,
                simulatedSeconds=round(simulated, 1),
                simulationSeconds=round(metadata["simulationSeconds"], 1),
                message=(
                    f"Running XBeach condition {condition['conditionId']} "
                    f"({condition_index + 1}/{condition_total}) · simulated {percent}% "
                    f"({simulated:.0f} / {metadata['simulationSeconds']:.0f} s)"
                ),
            )
        return subprocess.CompletedProcess([engine.executable], process.wait(), stdout=log_tail)

    def _run(self, identifier: str, model_case: dict[str, Any]) -> None:
        job_dir = self.runs_dir / identifier
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            (job_dir / "request.json").write_text(json.dumps(model_case, indent=2), encoding="utf-8")
            engine_key = model_case["engine"]
            engine = self.engines[engine_key]
            engine_label = engine.name
            self._update(identifier, status="running", message=f"Preparing {engine_label} cases")
            if not engine.available or not engine.executable:
                raise CaseError(f"{engine_label} is not installed or was not configured when the helper started.")
            results: list[dict[str, Any]] = []
            total = len(model_case["conditions"])
            for index, condition in enumerate(model_case["conditions"]):
                slug = safe_condition_slug(condition["conditionId"], index)
                case_dir = job_dir / slug
                if engine_key == "swan":
                    metadata = write_swan_case(case_dir, model_case, condition, index)
                elif engine_key == "swash":
                    metadata = write_swash_case(case_dir, model_case, condition, index)
                else:
                    metadata = write_xbeach_case(case_dir, model_case, condition, index)
                self._update(
                    identifier,
                    progress=round(index * 100 / total),
                    message=f"Running {engine_label} condition {condition['conditionId']} ({index + 1}/{total})",
                )
                if engine_key == "swash":
                    completed = self._run_swash_process(
                        identifier,
                        engine,
                        case_dir,
                        condition,
                        index,
                        total,
                        metadata,
                    )
                elif engine_key == "xbeach":
                    completed = self._run_xbeach_process(
                        identifier,
                        engine,
                        case_dir,
                        condition,
                        index,
                        total,
                        metadata,
                    )
                else:
                    completed = subprocess.run(
                        [engine.executable],
                        cwd=case_dir,
                        env=engine.environment(),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                        timeout=1800,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                log = completed.stdout[-MAX_LOG_CHARS:]
                (case_dir / "service.log").write_text(log, encoding="utf-8")
                if completed.returncode != 0:
                    raise CaseError(
                        f"{engine_label} failed for condition {condition['conditionId']} with exit code {completed.returncode}. "
                        f"See {case_dir / 'service.log'}."
                    )
                if engine_key == "swan":
                    toe = parse_swan_results(case_dir / "results.tab")
                elif engine_key == "swash":
                    toe = parse_swash_results(case_dir / "toe.tbl", metadata, case_dir / "stats.tab")
                else:
                    toe = parse_xbeach_results(case_dir / "point001.dat", metadata)
                result: dict[str, Any] = {
                        "engine": engine_key,
                        "conditionId": condition["conditionId"],
                        "input": condition,
                        "toe": toe,
                        "toeChainage": metadata["toeChainage"],
                        "toeElevation": metadata["toeElevation"],
                        "boundaryChainage": metadata["boundaryChainage"],
                        "gridSpacing": metadata["gridSpacing"],
                        "caseFolder": str(case_dir.relative_to(ROOT)),
                }
                if engine_key == "swash":
                    try:
                        animation = write_swash_animation_artifact(
                            case_dir / "surface.tbl", metadata, case_dir / "surface_animation.json.gz"
                        )
                        animation["url"] = f"/api/jobs/{identifier}/animations/{index}"
                    except CaseError as exc:
                        # The transformed toe statistics remain valid even if an
                        # optional visualisation artifact cannot be packaged.
                        animation = {"available": False, "error": str(exc)}
                    result["animation"] = animation
                results.append(result)
                self._update(identifier, results=results, progress=round((index + 1) * 100 / total))
            (job_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            self._update(identifier, status="complete", progress=100, message=f"Completed {total} {engine_label} condition(s)", results=results)
        except subprocess.TimeoutExpired:
            engine_key = str(model_case.get("engine", "model"))
            label = self.engines[engine_key].name if engine_key in self.engines else engine_key.upper()
            limit = "two hour" if model_case.get("engine") in {"swash", "xbeach"} else "30 minute"
            self._update(identifier, status="failed", message=f"{label} timed out", error=f"A {label} condition exceeded the {limit} limit.")
        except Exception as exc:  # job errors are returned to the UI, not the server loop
            self._update(identifier, status="failed", message="Job failed", error=str(exc))


class WaveModelHandler(BaseHTTPRequestHandler):
    server_version = "WaveFlumeService/" + SERVICE_VERSION

    @property
    def app(self) -> "WaveModelServer":
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: Any) -> None:
        # Browser polling happens roughly once a second; keep the helper console
        # readable and show model progress in the viewer instead.
        if self.command == "GET" and self.path.startswith("/api/jobs/"):
            return
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")

    def _json(self, status: HTTPStatus | int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "The service accepts loopback requests only."})
            return
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {
                    "service": "Wave Flume local model service",
                    "version": SERVICE_VERSION,
                    "apiVersion": API_VERSION,
                    "engines": {name: engine.public() for name, engine in self.app.engines.items()},
                    "runsFolder": str(self.app.store.runs_dir),
                },
            )
            return
        animation_match = re.fullmatch(r"/api/jobs/([0-9a-fA-F]{12})/animations/(\d+)", path)
        if animation_match:
            identifier, result_index_text = animation_match.groups()
            artifact = self.app.store.animation_path(identifier, int(result_index_text))
            if not artifact:
                self._json(HTTPStatus.NOT_FOUND, {"error": "SWASH surface animation not found."})
                return
            body = artifact.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/jobs/"):
            identifier = path.removeprefix("/api/jobs/").split("/", 1)[0]
            job = self.app.store.get(identifier)
            if job:
                self._json(HTTPStatus.OK, job)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Job not found."})
            return
        if path in {"/", "/wave_flume_bathymetry_viewer.html"}:
            if not self.app.viewer.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": f"Viewer not found: {self.app.viewer}"})
                return
            body = self.app.viewer.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        if not self._valid_host():
            self._json(HTTPStatus.FORBIDDEN, {"error": "The service accepts loopback requests only."})
            return
        path = urlparse(self.path).path
        if path != "/api/jobs":
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Invalid or oversized request body."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            model_case = validate_case(payload)
            engine = self.app.engines[model_case["engine"]]
            if not engine.available:
                self._json(
                    HTTPStatus.CONFLICT,
                    {
                        "error": f"{engine.name} is not available. Restart the helper with --{model_case['engine']} "
                        f"followed by the full path to {model_case['engine']}.exe."
                    },
                )
                return
            job = self.app.store.create(model_case)
            self._json(HTTPStatus.ACCEPTED, job)
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body is not valid JSON."})
        except CaseError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


class WaveModelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self) -> None:
        # On Windows, SO_REUSEADDR can allow two local helpers to listen on the
        # same port. Requests may then reach an older process with stale model
        # configuration. Exclusive binding makes a duplicate launch fail
        # clearly instead of producing an inconsistent viewer state.
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address: tuple[str, int], viewer: Path, runs_dir: Path, engines: dict[str, Engine]) -> None:
        super().__init__(address, WaveModelHandler)
        self.viewer = viewer
        self.engines = engines
        self.store = JobStore(runs_dir, engines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--viewer", type=Path, default=DEFAULT_VIEWER)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Optional JSON file containing executable paths")
    parser.add_argument("--swan", help="Full path to swan.exe; otherwise the helper searches PATH")
    parser.add_argument("--swash", help="Full path to swash.exe; otherwise the helper searches PATH")
    parser.add_argument("--xbeach", help="Full path to xbeach.exe; otherwise the helper searches PATH")
    parser.add_argument(
        "--runtime-path",
        action="append",
        default=[],
        help="Directory containing executable runtime DLLs; may be supplied more than once",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the viewer automatically")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config: dict[str, Any] = {}
    if args.config.is_file():
        try:
            loaded = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("the top level must be an object")
            config = loaded
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Could not read helper configuration {args.config}: {exc}") from exc
    viewer = args.viewer.resolve()
    runs_dir = args.runs.resolve()
    common_runtime_paths = merge_runtime_paths(
        normalise_runtime_paths(config.get("runtimePaths"), "runtimePaths"),
        normalise_runtime_paths(args.runtime_path, "--runtime-path"),
    )
    engines = {
        "swan": Engine(
            "SWAN",
            discover_executable(args.swan or config.get("swanExecutable"), ("swan.exe", "swan")),
            True,
            "1D stationary spectral transformation to the seawall toe",
            merge_runtime_paths(
                common_runtime_paths,
                normalise_runtime_paths(config.get("swanRuntimePaths"), "swanRuntimePaths"),
            ),
        ),
        "swash": Engine(
            "SWASH",
            discover_executable(args.swash or config.get("swashExecutable"), ("swash.exe", "swash")),
            True,
            "1D non-hydrostatic phase-resolving transformation to the seawall toe",
            merge_runtime_paths(
                common_runtime_paths,
                normalise_runtime_paths(config.get("swashRuntimePaths"), "swashRuntimePaths"),
            ),
        ),
        "xbeach": Engine(
            "XBeach",
            discover_executable(args.xbeach or config.get("xbeachExecutable"), ("xbeach.exe", "xbeach")),
            True,
            "1D surfbeat transformation with short-wave breaking, setup, and infragravity-wave effects",
            merge_runtime_paths(
                common_runtime_paths,
                normalise_runtime_paths(config.get("xbeachRuntimePaths"), "xbeachRuntimePaths"),
            ),
        ),
    }
    try:
        server = WaveModelServer((args.host, args.port), viewer, runs_dir, engines)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048:
            raise SystemExit(
                f"Port {args.port} is already in use. Close the existing Wave Flume helper window "
                "before starting another copy."
            ) from None
        raise
    url = f"http://{args.host}:{args.port}/"
    print(f"Wave Flume local model service {SERVICE_VERSION}")
    print(f"Viewer: {url}")
    print(f"SWAN: {engines['swan'].executable or 'not found'}")
    print(f"SWASH: {engines['swash'].executable or 'not found'}")
    print(f"XBeach: {engines['xbeach'].executable or 'not found'}")
    print("Press Ctrl+C to stop the helper.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping local model service.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
