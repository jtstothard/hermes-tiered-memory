#!/usr/bin/env bash
set -Eeuo pipefail

# Disposable topology lifecycle. All mutable state is beneath ROOT; no ambient
# Hermes home, config, credentials, or production endpoints are consulted.
ROOT="${TOPOLOGY_ROOT:-/tmp/hermes-tiered-memory-e2e}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/state"
PINS="$REPO_ROOT/e2e-topology/pins.json"
HERMES_SRC="${HERMES_SRC:-/home/hermes/hermes-agent}"
MNEMOSYNE_SRC="${MNEMOSYNE_SRC:-/tmp/mnemosyne-v314}"
HINDSIGHT_SRC="${HINDSIGHT_SRC:-/home/hermes/hindsight}"
VENV="$ROOT/venv"
# Include the disposable root in the container name so a prior cleanup cannot
# collide with a concurrent/just-finished disposable run.
COMPOSE_PROJECT="hermes-tiered-e2e-$(id -u)-$(printf %s "$ROOT" | cksum | cut -d ' ' -f 1)"
CONTAINER="${COMPOSE_PROJECT}-hindsight"

fail() { printf 'topology: %s\n' "$*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null || fail "required command not found: $1"; }

verify_pin() {
  local name="$1" src="$2" commit="$3" tag="$4"
  [[ -d "$src/.git" ]] || fail "$name source checkout missing: $src"
  git -C "$src" cat-file -e "$commit^{commit}" || fail "$name pinned commit is unavailable: $commit"
  [[ "$(git -C "$src" rev-parse "$tag^{commit}")" == "$commit" ]] || \
    fail "$name tag $tag does not resolve to pinned commit $commit"
  printf '%s %s %s %s\n' "$name" "$tag" "$commit" "$src"
}

materialize_pin() {
  local name="$1" src="$2" commit="$3"
  local out="$ROOT/sources/$name"
  mkdir -p "$out"
  git -C "$src" archive "$commit" | tar -x -C "$out"
  printf '%s\n' "$commit" > "$out/.pinned-commit"
}

start() {
  require_cmd docker; require_cmd git; require_cmd python3; require_cmd curl
  [[ ! -e "$ROOT" ]] || fail "refusing to reuse existing root: $ROOT (run teardown first)"
  mkdir -p "$ROOT"
  chmod 700 "$ROOT"
  trap 'teardown >/dev/null 2>&1 || true' ERR INT TERM
  mkdir -p "$STATE" "$ROOT/hermes/plugins" "$ROOT/mnemosyne" "$ROOT/hindsight"
  cp "$PINS" "$STATE/pins.json"

  verify_pin Hermes "$HERMES_SRC" 3ef6bbd201263d354fd83ec55b3c306ded2eb72a v2026.7.20 | tee "$STATE/versions.txt"
  verify_pin Mnemosyne "$MNEMOSYNE_SRC" 4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce v3.14.0 | tee -a "$STATE/versions.txt"
  verify_pin Hindsight "$HINDSIGHT_SRC" 08995e3013858e705fb4ca27c0ade3a286ef4750 v0.8.6 | tee -a "$STATE/versions.txt"
  materialize_pin hermes "$HERMES_SRC" 3ef6bbd201263d354fd83ec55b3c306ded2eb72a
  materialize_pin mnemosyne "$MNEMOSYNE_SRC" 4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce
  materialize_pin hindsight "$HINDSIGHT_SRC" 08995e3013858e705fb4ca27c0ade3a286ef4750

  # Template home contains synthetic config only. Deliberately do not copy ~/.hermes.
  cat > "$ROOT/hermes/config.yaml" <<'YAML'
memory:
  provider: mnemosyne
YAML
  chmod 600 "$ROOT/hermes/config.yaml"

  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet -e "$ROOT/sources/mnemosyne" \
    'cryptography>=41.0.0' 'httpx>=0.25.0'
  "$VENV/bin/pip" install --quiet -e "$REPO_ROOT[test]"
  "$VENV/bin/python" - <<PY
import os, sqlite3
os.environ['MNEMOSYNE_DATA_DIR'] = '$ROOT/mnemosyne'
os.environ['MNEMOSYNE_DB_PATH'] = '$ROOT/mnemosyne/e2e.sqlite'
from mnemosyne import remember
remember('e2e-alpha-001: vegan recipe notebook', source='disposable-fixture')
con=sqlite3.connect('$ROOT/mnemosyne/e2e.sqlite'); con.execute('select 1'); con.close()
PY

  # Build from the exact pinned source, with local models disabled. No host mounts.
  docker build --quiet --file "$HINDSIGHT_SRC/docker/standalone/Dockerfile" --target api-only \
    --build-arg INCLUDE_LOCAL_MODELS=true --build-arg PRELOAD_ML_MODELS=true \
    -t "$COMPOSE_PROJECT/hindsight:v0.8.6" "$ROOT/sources/hindsight" > "$STATE/image.txt"
  docker run -d --name "$CONTAINER" -p 127.0.0.1:0:8888 \
    -e HINDSIGHT_ENABLE_API=true -e HINDSIGHT_ENABLE_CP=false \
    -e HINDSIGHT_API_LLM_PROVIDER=ollama -e HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:9/v1 \
    -e HINDSIGHT_API_LLM_API_KEY=disposable-not-a-credential \
    -e HINDSIGHT_API_RETAIN_EXTRACTION_MODE=chunks \
    -e HINDSIGHT_API_EMBEDDING_PROVIDER=local \
    "$COMPOSE_PROJECT/hindsight:v0.8.6" > "$STATE/container-id"
  # Discover the ephemeral loopback port and wait for the actual health endpoint.
  HINDSIGHT_PORT="$(docker port "$CONTAINER" 8888/tcp | sed -E 's/.*:([0-9]+)$/\1/')"
  printf '%s\n' "$HINDSIGHT_PORT" > "$STATE/hindsight-port"
  for _ in $(seq 1 90); do curl -fsS "http://127.0.0.1:$HINDSIGHT_PORT/health" > "$STATE/hindsight-health.json" && break; sleep 2; done
  [[ -s "$STATE/hindsight-health.json" ]] || { docker logs "$CONTAINER" > "$STATE/hindsight.log" 2>&1 || true; fail 'Hindsight health check timed out'; }

  # Exercise the real pinned Hermes CLI against the template home. Hermes has
  # no unauthenticated health API; --version is the safe, deterministic probe.
  HERMES_HOME="$ROOT/hermes" PYTHONPATH="$ROOT/sources/hermes" \
    python3 -m hermes_cli.main --version > "$STATE/hermes-health.txt"
  grep -F '2026.7.20' "$STATE/hermes-health.txt" >/dev/null || \
    fail 'Hermes version probe did not report v2026.7.20'
  printf '%s\n' "$ROOT" > "$STATE/READY"
  trap - ERR INT TERM
  printf 'TOPOLOGY_READY root=%s hindsight=http://127.0.0.1:%s\n' "$ROOT" "$HINDSIGHT_PORT"
}

smoke() {
  [[ -f "$STATE/READY" ]] || fail 'topology is not started'
  require_cmd curl
  local port; port="$(<"$STATE/hindsight-port")"
  curl --fail --silent "http://127.0.0.1:$port/health" > "$STATE/smoke-health.json"
  HERMES_HOME="$ROOT/hermes" MNEMOSYNE_DATA_DIR="$ROOT/mnemosyne" MNEMOSYNE_DB_PATH="$ROOT/mnemosyne/e2e.sqlite" \
    "$VENV/bin/python" - <<'PY' > "$STATE/smoke-mnemosyne.json"
import json
from mnemosyne import recall, remember
remember('e2e-beta-001: isolated second profile', source='disposable-fixture')
print(json.dumps({'alpha': len(recall('vegan recipe notebook')), 'beta': len(recall('isolated second profile'))}, sort_keys=True))
PY
  grep -F '2026.7.20' "$STATE/hermes-health.txt" >/dev/null || fail 'Hermes smoke probe failed'
  [[ -s "$STATE/smoke-mnemosyne.json" ]] || fail 'Mnemosyne smoke probe failed'
  # Run the adapter's live contract test only against this disposable endpoint.
  HINDSIGHT_E2E_URL="http://127.0.0.1:$port" \
    PYTHONPATH="$REPO_ROOT" "$VENV/bin/python" -m pytest -q \
      "$REPO_ROOT/tests/integration/test_hindsight_l2_live.py" > "$STATE/smoke-hindsight-adapter.txt"
  grep -F '1 passed' "$STATE/smoke-hindsight-adapter.txt" >/dev/null || \
    fail 'Hindsight adapter live smoke probe failed'
  printf 'SMOKE_PASS hermes=healthy mnemosyne=healthy hindsight=healthy adapter=healthy\n'
}

adapter_test() {
  [[ -f "$STATE/READY" ]] || fail 'topology is not started'
  TOPOLOGY_ROOT="$ROOT" PYTHONPATH="$REPO_ROOT" \
    "$VENV/bin/python" "$REPO_ROOT/e2e-topology/live_mnemosyne_l1_test.py" \
    > "$STATE/live-mnemosyne-l1.txt"
  grep -F 'ADAPTER_PASS records=3 profiles=2' "$STATE/live-mnemosyne-l1.txt" >/dev/null || \
    fail 'live Mnemosyne L1 adapter probe failed'
  printf 'ADAPTER_PASS mnemosyne=l1\n'
}

teardown() {
  if [[ -f "$STATE/container-id" ]]; then docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; fi
  [[ "$ROOT" == /tmp/hermes-tiered-memory-e2e* || "$ROOT" == /tmp/tiered-memory-adapter-* ]] || \
    fail 'unsafe topology root; refusing deletion'
  rm -rf -- "$ROOT"
  printf 'TOPOLOGY_TORN_DOWN root=%s\n' "$ROOT"
}

test_twice() {
  for run in 1 2; do
    printf 'E2E_RUN %s/2\n' "$run"
    start
    smoke
    adapter_test
    teardown
    [[ ! -e "$ROOT" ]] || fail "temporary root leaked after run $run: $ROOT"
  done
  printf 'E2E_PASS runs=2\n'
}

case "${1:-}" in
  start) start ;;
  smoke) smoke ;;
  adapter-test) adapter_test ;;
  teardown) teardown ;;
  test) test_twice ;;
  *) echo "usage: $0 {start|smoke|adapter-test|teardown|test}" >&2; exit 2 ;;
esac
