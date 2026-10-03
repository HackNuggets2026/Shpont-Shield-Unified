import argparse
import logging
import os

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m controllayer", description="Run the AI Control Layer gateway.")
    ap.add_argument("--policy", default=os.environ.get("ACL_POLICY", "policy.yaml"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    os.environ["ACL_POLICY"] = args.policy
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("controllayer.gateway.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
