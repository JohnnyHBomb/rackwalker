#!/usr/bin/env bash
# Regenerate proxy/rackwalker_proxy/contracts_gen.py from contracts/scene.schema.json (pinned datamodel-code-generator).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
"${VENV:-$ROOT/.venv}/bin/datamodel-codegen" --input "$ROOT/contracts/scene.schema.json" --input-file-type jsonschema \
  --output-model-type pydantic_v2.BaseModel --target-python-version 3.12 --use-annotated --field-constraints \
  --use-standard-collections --use-union-operator --enum-field-as-literal all --disable-timestamp \
  --collapse-root-models --use-title-as-name --formatters builtin --output "${1:-$ROOT/proxy/rackwalker_proxy/contracts_gen.py}"
