import os
import signal
import sys
from proxy import Proxy
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

def run_proxy():
    # Configuration from environment variables
    # Defaults: port 8899, no auth unless PROXY_USER and PROXY_PASS are set
    # Railway provides 'PORT' environment variable
    port = int(os.environ.get("PROXY_PORT") or os.environ.get("PORT") or 8899)
    username = os.environ.get("PROXY_USER")
    password = os.environ.get("PROXY_PASS")
    
    # proxy.py arguments
    args = [
        "--hostname", "0.0.0.0",
        "--port", str(port),
    ]
    
    if username and password:
        # Format: user:pass
        args.extend(["--basic-auth", f"{username}:{password}"])
        print(f"Starting authenticated proxy on port {port} for user: {username}")
    else:
        print(f"Starting unauthenticated proxy on port {port}")
        print("WARNING: It is highly recommended to set PROXY_USER and PROXY_PASS environment variables.")

    # Create and run the proxy
    with Proxy(args) as p:
        # Handle termination signals gracefully
        def signal_handler(sig, frame):
            print("\nShutting down proxy...")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # This will block until the proxy stops or a signal is received
        # In proxy.py, Proxy.__enter__ starts the server in background threads
        # We need to keep the main thread alive.
        try:
            # Join the server thread if possible or just wait
            # proxy.py's Proxy() context manager handles start/stop
            # We just need to wait for signals.
            signal.pause()
        except AttributeError:
            # fallback for systems without signal.pause() (like Windows, though we're on Linux)
            import time
            while True:
                time.sleep(1)

if __name__ == "__main__":
    run_proxy()
