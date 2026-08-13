import argparse
import uvicorn
import os

def main():
    parser = argparse.ArgumentParser(description="LLM-Shield-Proxy: Secure PII Redaction Proxy")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind socket to this host")
    parser.add_argument("--port", type=int, default=8000, help="Bind socket to this port")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    
    args = parser.parse_args()
    
    # We must run uvicorn programmatically to boot the FastAPI app
    uvicorn.run(
        "llm_shield_proxy.main:app", 
        host=args.host, 
        port=args.port, 
        workers=args.workers,
        reload=args.reload
    )

if __name__ == "__main__":
    main()
