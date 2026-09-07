from __future__ import annotations

import hashlib
from pathlib import Path

from flumeworks.model_design import wave_model_service


EXPECTED_HASHES = {
    "wave_flume_bathymetry_viewer.html": "e7e825f33389d78813f08ff8b201f7e0631296564b2a5cf03eefe0759f9de466",
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
