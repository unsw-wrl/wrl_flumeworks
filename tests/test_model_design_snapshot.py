from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from flumeworks.model_design import wave_model_service


EXPECTED_HASHES = {
    "wave_flume_bathymetry_viewer.html": "e5ee8668884627ce9a0e82fdd2dde739bed505f285e8cd282dbe31fa82c65cc1",
    "wave_model_service.py": "86ccaf3c952d30b73f13016e2793d4cf6ea288ad830f5ea19fd571877cb05005",
}


def test_model_design_files_match_reviewed_snapshot() -> None:
    root = Path(wave_model_service.__file__).resolve().parent
    actual = {
        # The repository's existing attributes allow Windows checkouts to use
        # CRLF. Compare canonical LF bytes so line-ending policy cannot make an
        # otherwise unchanged imported file fail its integrity check.
        name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in EXPECTED_HASHES
    }

    assert actual == EXPECTED_HASHES


def test_swan_breaking_coefficient_is_validated_and_preserved() -> None:
    model_case = wave_model_service.validate_case(
        {
            "engine": "swan",
            "bathymetry": [
                {"chainage": 0, "elevation": -1},
                {"chainage": 10, "elevation": -5},
            ],
            "conditions": [
                {
                    "conditionId": "1",
                    "waterLevel": 1,
                    "statsDepth": -4,
                    "waveHeight": 1,
                    "period": 8,
                }
            ],
            "structure": {"toeChainage": 0},
            "options": {"swanBreakingCoefficient": 0.65},
        }
    )

    assert model_case["options"]["swanBreakingCoefficient"] == 0.65


def test_hrw_2026_achievable_wave_curves_are_embedded() -> None:
    viewer = Path(wave_model_service.__file__).resolve().parent / "wave_flume_bathymetry_viewer.html"
    html = viewer.read_text(encoding="utf-8")
    match = re.search(
        r'<script id="hrwSpecs2026Data"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL,
    )

    assert match is not None
    data = json.loads(match.group(1))
    assert data["sheet"] == "HR Wallingford Specs 2026"
    assert len(data["periods"]) == 51
    assert data["periods"][0] == 0.8
    assert data["periods"][-1] == 5
    assert [series["depth"] for series in data["series"]] == [
        "0.5 m",
        "0.6 m",
        "0.7 m",
        "0.8 m",
        "0.9 m",
        "1.0 m",
        "1.1 m",
        "1.2 m",
    ]
    assert data["series"][1]["values"][0] == 74.105088
    assert data["series"][4]["values"][12] == 279.883893


def test_achievable_wave_chart_uses_independent_froude_scaling() -> None:
    viewer = Path(wave_model_service.__file__).resolve().parent / "wave_flume_bathymetry_viewer.html"
    html = viewer.read_text(encoding="utf-8")

    assert 'const DEFAULT_HRW_DEPTHS = ["0.6 m","0.9 m"]' in html
    assert 'let achievableScaleMin = 10' in html
    assert 'let achievableScaleMax = 40' in html
    assert 'let activeAchievableConditionIds = new Set()' in html
    assert 'condition.period/Math.sqrt(scale)' in html
    assert 'condition.waveHeight*1000/scale' in html
    assert 'HRW Specs 2026 - ${item.series.depth} depth' in html
