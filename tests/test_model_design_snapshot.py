from __future__ import annotations

import hashlib
from pathlib import Path

from flumeworks.model_design import wave_model_service


EXPECTED_HASHES = {
    "wave_flume_bathymetry_viewer.html": "d64d5a09f6b73d72887fd77e99909b3708dd3da6590bb626570485035e31d1a0",
    "wave_model_service.py": "26fa8ea2eba9b819a1bc3dc91098b02e6acca9ef8e8cb902bd34bc425b70b3e1",
}


def test_imported_model_design_files_are_unchanged() -> None:
    root = Path(wave_model_service.__file__).resolve().parent
    actual = {
        # The repository's existing attributes allow Windows checkouts to use
        # CRLF. Compare canonical LF bytes so line-ending policy cannot make an
        # otherwise unchanged imported file fail its integrity check.
        name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in EXPECTED_HASHES
    }

    assert actual == EXPECTED_HASHES
