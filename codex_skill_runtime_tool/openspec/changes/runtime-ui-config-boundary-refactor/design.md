# Design: Runtime UI Config Boundary Refactor

## Boundary

The new module owns pure or near-pure configuration helpers:

- env file parsing and value expansion;
- model configuration projection and update generation;
- managed env block writing;
- runtime path resolution;
- service configuration normalization.

`server.py` remains responsible for HTTP routing, process ownership, session/job coordination, and UI response assembly.

## Interface

The config module exports explicit functions and keeps repository-specific defaults in a small `RuntimePaths` value:

- `load_env(path)`
- `model_config_from_env(values, runtime_env, paths)`
- `model_config_updates_from_payload(payload, current)`
- `write_env_updates(path, updates, delete_when_empty=...)`
- `apply_runtime_env_to_process(values)`
- `runtime_env_exports(values, paths)`
- `configured_services(values)`
- `service_by_id(values, service_id)`
- `portable_path(value, paths, fallback=...)`
- `state_root_from_env(runtime_env, paths)`
- `runtime_env_paths(runtime_env, paths)`

This avoids hardcoding a single local user path while preserving the existing default behavior derived from the installed tool root and workspace root.

## Risk Control

- Keep function behavior equivalent to existing code.
- Do not change route names or response shapes.
- Add focused selftests for the extracted module before changing broader backend code.
- Run compile checks on `server.py`, the new module, and the selftest script.

## Future Work

Once this seam is stable, later changes can split:

- project/session file tree helpers;
- conversation event projection;
- process/job lifecycle;
- HTTP route dispatch.
