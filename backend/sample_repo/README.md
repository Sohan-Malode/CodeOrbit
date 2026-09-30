# Legacy Inventory Service

A small Flask backend intentionally shaped like a legacy service for CodeOrbit demonstrations.

The runtime path is:

`run.py -> app -> routes -> services -> repository -> models`

The service layer and model layer also retain a circular dependency that is visible in CodeOrbit's architecture analysis. The repository includes a few legacy modules that are no longer connected to the runtime path so the analyzer can demonstrate potentially unused/dead code.
