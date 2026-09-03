# Imported Model Design viewer

These files were copied on 2026-09-03 from the existing `wave_flume_and_bathy`
project. The viewer subsequently received the WRL blue branding and a dedicated
FlumeWorks Wave Transformation presentation mode.

- `wave_flume_bathymetry_viewer.html`
  - Imported SHA-256: `D64D5A09F6B73D72887FD77E99909B3708DD3DA6590BB626570485035E31D1A0`
  - Current reviewed SHA-256: `FF364E081950F3309FD89C745E05473D1D664617DC3BADB438A0DD10E88DA3CB`
- `wave_model_service.py`
  - Imported SHA-256: `26FA8EA2EBA9B819A1BC3DC91098B02E6ACCA9EF8E8CB902BD34BC425B70B3E1`
  - Current reviewed SHA-256: `86CCAF3C952D30B73F13016E2793D4CF6EA288AD830F5EA19FD571877CB05005`

The application shell imports the service as a compatibility module and moves the same live viewer
between the top-level **Model Design** and **Tools / Wave Transformation** workspaces. Changes to
these two files should be developed and tested
as deliberate Model Design changes, and the current hashes in this record and the snapshot tests updated.
The current viewer also provides a parent-window bridge so its flume reference, bathymetry, wave
conditions, placement, and display settings can be stored inside the active `.flumeworks` file.
