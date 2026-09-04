import argparse
import os
import socket
import sys

from ui.app import create_app

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def is_port_available(host: str, port: int) -> bool:
    """Check if the target port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except socket.error:
            return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Avi to FortiADC Migration UI")
    parser.add_argument("--host", default=os.environ.get("MIGRATION_UI_HOST", "127.0.0.1"), help="Binding host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MIGRATION_UI_PORT", "5000")), help="Binding port")
    parser.add_argument("--debug", action="store_true", default=os.environ.get("MIGRATION_UI_DEBUG", "").lower() in {"1", "true", "yes"}, help="Enable debug mode")
    
    args = parser.parse_args()
    host = args.host
    port = args.port
    debug = args.debug

    # Port Guard: Collision detection without auto-kill
    if not is_port_available(host, port):
        print(f"\n[!][CONFLICT] Port {port} is already in use on {host}.")
        print(f"[!][TIP] This usually happens when a previous UI instance is still running.")
        print(f"[!][TIP] Use '--port <NEW_PORT>' to run on a different port.")
        print(f"[!][TIP] Windows: run 'netstat -ano | findstr :{port}' to find the PID, then 'taskkill /F /PID <PID>'.")
        sys.exit(1)

    print(f"[*] Port {port} is clear. Launching Migration Operator Console...")
    app = create_app({"DEBUG": debug})
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
