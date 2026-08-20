#!/usr/bin/env bash
set -e

echo "Starting K8S OOTB Baseline Verification..."

# 1. Create a local cluster using kind
echo "Creating kind cluster..."
kind create cluster --name llm-shield-test

# 2. Build the proxy Docker image
echo "Building proxy Docker image..."
docker build -t llm-shield-proxy:latest .

# 3. Load the image into the cluster
echo "Loading image into kind cluster..."
kind load docker-image llm-shield-proxy:latest --name llm-shield-test

# 4. Apply the YAML manifest
echo "Applying Kubernetes manifests..."
kubectl apply -f tests/k8s_ootb/happy_path_sidecar.yaml

# 5. Execute the Pytest script
echo "Running Pytest suite..."
set +e
py -m pytest tests/k8s_ootb/test_k8s_baseline.py -s -v --noconftest
TEST_RESULT=$?
set -e

# 6. Delete the KinD cluster
echo "Cleaning up kind cluster..."
kind delete cluster --name llm-shield-test

# 7. Print result and exit
if [ $TEST_RESULT -eq 0 ]; then
    echo -e "\033[0;32mK8S OOTB BASELINE PASSED\033[0m"
    exit 0
else
    echo -e "\033[0;31mK8S OOTB BASELINE FAILED\033[0m"
    exit 1
fi
