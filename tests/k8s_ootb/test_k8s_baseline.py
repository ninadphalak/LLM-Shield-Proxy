import subprocess
import time

import pytest


def is_k8s_running():
    try:
        subprocess.run(["kubectl", "cluster-info"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not is_k8s_running(), reason="Kubernetes cluster not available")
def test_sidecar_intercepts_traffic():
    # Wait for the pod to be ready
    print("Waiting for pod to be ready...")
    wait_cmd = ["kubectl", "wait", "--for=condition=ready", "pod", "-l", "app=llm-shield", "--timeout=120s"]
    subprocess.run(wait_cmd, check=True)

    # Get the pod name
    get_pod_cmd = ["kubectl", "get", "pods", "-l", "app=llm-shield", "-o", "jsonpath={.items[0].metadata.name}"]
    pod_name = subprocess.check_output(get_pod_cmd).decode("utf-8").strip()
    assert pod_name != ""

    # Give the proxy a little extra time to start its server after the pod is ready
    time.sleep(5)

    # Exec into the mock-app container and curl the proxy on the shared localhost
    print(f"Executing curl from mock-app in pod {pod_name}...")
    curl_cmd = [
        "kubectl",
        "exec",
        pod_name,
        "-c",
        "mock-app",
        "--",
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "http://localhost:8000/healthz",
    ]

    output = subprocess.check_output(curl_cmd).decode("utf-8").strip()
    print(f"Curl response code: {output}")

    # Assert that the sidecar intercepts and responds with 200 OK
    assert output == "200"
