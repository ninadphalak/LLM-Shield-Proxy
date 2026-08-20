#!/usr/bin/env bash
set -e

# Jump to repository root
cd "$(dirname "$0")"
cd ../../

echo "Starting K8S Adversarial Test Harness..."

echo "Bringing up docker-compose test harness..."
docker compose -f deploy/test-harness/docker-compose.k8s-sim.yml up -d --build

echo "Waiting for services to be ready..."
sleep 15

set +e

echo "Running test_lifecycle.py..."
py -m pytest tests/k8s/test_lifecycle.py -s -v --noconftest
LIFECYCLE_RESULT=$?

echo "Running test_stream_resilience.py..."
py -m pytest tests/k8s/test_stream_resilience.py -s -v --noconftest
STREAM_RESULT=$?

echo "Running test_concurrency_cgroup.py..."
py -m pytest tests/k8s/test_concurrency_cgroup.py -s -v --noconftest
CONCURRENCY_RESULT=$?

set -e

echo "Tearing down test harness..."
docker compose -f deploy/test-harness/docker-compose.k8s-sim.yml down -v

if [ $LIFECYCLE_RESULT -eq 0 ] && [ $STREAM_RESULT -eq 0 ] && [ $CONCURRENCY_RESULT -eq 0 ]; then
    echo -e "\033[0;32mK8S ADVERSARIAL TEST HARNESS PASSED\033[0m"
    exit 0
else
    echo -e "\033[0;31mK8S ADVERSARIAL TEST HARNESS FAILED\033[0m"
    exit 1
fi
