#!/usr/bin/env bash
# LLM-Shield-Proxy 60-Second Quickstart

# 1. Spin up the security gateway
docker-compose up -d

# 2. Wait for health check
echo "Waiting for LLM-Shield-Proxy to be healthy..."
until curl -s http://localhost:8000/healthz > /dev/null; do
    sleep 1
done

# 3. Run the demo script
echo "Running Quickstart Demo..."
python examples/demo.py
