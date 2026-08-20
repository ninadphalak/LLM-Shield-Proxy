#!/usr/bin/env bash

# Strictly exit on any failure
set -e
set -o pipefail

# Navigate to the root of the project
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "Starting OOTB Verification Suite..."
echo "=========================================="

echo ""
echo "[1/3] Running PyPI/CLI Happy Path Test..."
py tests/ootb/test_pypi_cli.py

echo ""
echo "[2/3] Running Docker Standalone Happy Path Test..."
py tests/ootb/test_docker_standalone.py

echo ""
echo "[3/3] Running Docker Compose Integration Test..."
COMPOSE_FILE="tests/ootb/docker-compose.test.yml"

# Ensure clean slate
docker-compose -f "$COMPOSE_FILE" down -v --remove-orphans || true

# Stand up the compose stack
docker-compose -f "$COMPOSE_FILE" up -d

echo "Waiting for stack to become healthy (sleeping 5s)..."
sleep 5

# Ping the compose stack to verify it is alive
STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/healthz || echo "000")

if [ "$STATUS_CODE" -eq 200 ]; then
    echo "Stack is healthy. Got 200 OK from /healthz"
else
    echo "Error: Stack failed healthcheck. Expected 200 OK, got HTTP $STATUS_CODE"
    echo "--- Proxy Logs ---"
    docker-compose -f "$COMPOSE_FILE" logs proxy
    echo "------------------"
    docker-compose -f "$COMPOSE_FILE" down -v
    exit 1
fi

echo "Tearing down compose stack..."
docker-compose -f "$COMPOSE_FILE" down -v

echo ""
# Print "OOTB VERIFICATION PASSED" in green
echo -e "\033[0;32mOOTB VERIFICATION PASSED\033[0m"
exit 0
