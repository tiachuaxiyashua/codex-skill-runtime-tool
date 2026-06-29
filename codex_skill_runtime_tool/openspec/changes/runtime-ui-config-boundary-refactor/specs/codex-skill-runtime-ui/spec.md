# Spec Delta: Runtime UI Config Boundary

## ADDED Requirements

### Requirement: UI Backend Separates Configuration Parsing

Runtime UI SHALL keep environment, model, service, and path configuration parsing in a dedicated backend module rather than inline in the HTTP handler module.

#### Scenario: Server reads model configuration

- **WHEN** `/api/model-config` reads `skill-runtime.env`
- **THEN** the HTTP handler delegates env parsing and model projection to the config module
- **AND** the response shape remains compatible with the previous UI contract.

#### Scenario: Server starts configured services

- **WHEN** `/api/services/<id>/start` resolves a configured service
- **THEN** the HTTP handler delegates service discovery and runtime env export construction to the config module
- **AND** no game-specific, CCGS-specific, Forge-specific, ComfyUI-specific, provider-specific, or local absolute path branch is required.

### Requirement: UI Config Boundary Is Selftested

Runtime UI SHALL provide a focused selftest for configuration parsing behavior.

#### Scenario: Config selftest runs

- **WHEN** the config boundary selftest is executed
- **THEN** it verifies env parsing, model config projection, model env update writing, service config parsing, and runtime path resolution
- **AND** it exits successfully only when all checks pass.
