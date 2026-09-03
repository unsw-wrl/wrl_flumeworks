# Imported Model Design viewer

These files were copied on 2026-09-03 from the existing `wave_flume_and_bathy`
project. The viewer subsequently received the WRL blue branding requested for
FlumeWorks; no viewer behaviour was changed.

- `wave_flume_bathymetry_viewer.html`
  - Imported SHA-256: `D64D5A09F6B73D72887FD77E99909B3708DD3DA6590BB626570485035E31D1A0`
  - Current reviewed SHA-256: `2398328635217C33FC0E3BCB8817935F86BE1E304DA01332588F776A1EFFBB5B`
- `wave_model_service.py`
  - Imported/current SHA-256: `26FA8EA2EBA9B819A1BC3DC91098B02E6ACCA9EF8E8CB902BD34BC425B70B3E1`

The application shell imports the service as a compatibility module and displays the viewer under
the top-level **Model Design** workspace. Changes to these two files should be developed and tested
as deliberate Model Design changes, and the current hashes in this record and the snapshot tests updated.
