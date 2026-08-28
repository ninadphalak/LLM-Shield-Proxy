import subprocess
import time
import urllib.request


def test_docker_standalone_happy_path():
    image_name = "llm-shield:test"
    container_name = "llm-shield-test-run"

    # 1. Build the image
    subprocess.run(["docker", "build", "-t", image_name, "."], check=True)

    # Clean up any existing container with the same name
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # 2. Run the container
    subprocess.run(["docker", "run", "-d", "--name", container_name, "-p", "8001:8000", "-e", "SHIELD_ENCRYPTION_KEY=" + "00" * 32, "-e", "SHIELD_WATERMARK_SECRET=test-watermark", image_name], check=True)

    try:
        # 3. Ping healthz
        max_retries = 15
        success = False
        last_error = None
        for _ in range(max_retries):
            try:
                response = urllib.request.urlopen("http://127.0.0.1:8001/healthz", timeout=2)
                if response.getcode() == 200:
                    success = True
                    break
            except Exception as e:
                last_error = e
                time.sleep(1)

        if not success:
            print("--- DOCKER LOGS ---")
            subprocess.run(["docker", "logs", container_name])
            print("-------------------")
        assert success, f"Failed to get 200 OK from /healthz. Last error: {last_error}"
    finally:
        # 4. Tear down
        subprocess.run(["docker", "rm", "-f", container_name], check=True)


if __name__ == "__main__":
    test_docker_standalone_happy_path()
