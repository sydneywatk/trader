#!/usr/bin/env python3
"""One-command deploy of a strategy to QuantConnect.

Pushes a Python algorithm to a QuantConnect project, compiles it, runs a
backtest, and prints the key statistics — so shipping a new algo is one command:

    python quantconnect/deploy.py quantconnect/sid_quantconnect_experiments.py
    python quantconnect/deploy.py path/to/algo.py --project "My Algo" \
        --params universe=watchlist side=long start_year=2024
    python quantconnect/deploy.py path/to/algo.py --no-backtest   # deploy + compile only

Or via the Makefile:  make deploy STRATEGY=quantconnect/sid_quantconnect_experiments.py

Credentials (QuantConnect -> Account -> Security): set in the environment or in
a (gitignored) .env at the repo root:

    QC_USER_ID=123456
    QC_API_TOKEN=your_api_token
"""
import argparse
import base64
import hashlib
import os
import sys
import time

import requests

API = "https://www.quantconnect.com/api/v2"
POLL_SECONDS = 5
COMPILE_TIMEOUT = 180
BACKTEST_TIMEOUT = 60 * 40


class QCError(RuntimeError):
    """A QuantConnect API call returned success=false."""


def _load_env():
    """Load .env from the repo root if present (python-dotenv optional)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
        return
    except Exception:
        pass
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _auth_headers():
    user_id = os.environ.get("QC_USER_ID")
    token = os.environ.get("QC_API_TOKEN")
    if not user_id or not token:
        sys.exit("Missing QC_USER_ID / QC_API_TOKEN — set them in your environment "
                 "or .env (QuantConnect -> Account -> Security).")
    timestamp = str(int(time.time()))
    hashed = hashlib.sha256(f"{token}:{timestamp}".encode()).hexdigest()
    auth = base64.b64encode(f"{user_id}:{hashed}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Timestamp": timestamp}


def _post(endpoint, payload):
    resp = requests.post(f"{API}/{endpoint}", json=payload, headers=_auth_headers(), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success", False):
        raise QCError(f"{endpoint}: {data.get('errors')}")
    return data


def get_or_create_project(name, language="Py"):
    for project in _post("projects/read", {}).get("projects", []):
        if project.get("name") == name:
            return project["projectId"]
    created = _post("projects/create", {"name": name, "language": language})
    return created["projects"][0]["projectId"]


def push_main(project_id, content):
    try:
        _post("files/update", {"projectId": project_id, "name": "main.py", "content": content})
    except QCError:
        _post("files/create", {"projectId": project_id, "name": "main.py", "content": content})


def compile_project(project_id):
    compile_id = _post("compile/create", {"projectId": project_id})["compileId"]
    deadline = time.time() + COMPILE_TIMEOUT
    while time.time() < deadline:
        result = _post("compile/read", {"projectId": project_id, "compileId": compile_id})
        state = result.get("state")
        if state == "BuildSuccess":
            return compile_id
        if state == "BuildError":
            sys.exit(f"Compile failed:\n  " + "\n  ".join(result.get("logs", [])))
        time.sleep(POLL_SECONDS)
    sys.exit("Compile timed out.")


def run_backtest(project_id, compile_id, name, params=None):
    payload = {"projectId": project_id, "compileId": compile_id, "backtestName": name}
    if params:
        payload["parameters"] = params
    backtest_id = _post("backtests/create", payload)["backtest"]["backtestId"]
    print(f"  backtest queued: {backtest_id}")
    deadline = time.time() + BACKTEST_TIMEOUT
    while time.time() < deadline:
        backtest = _post("backtests/read", {"projectId": project_id, "backtestId": backtest_id})["backtest"]
        if backtest.get("completed"):
            return backtest
        time.sleep(10)
    sys.exit("Backtest timed out (still running on QuantConnect).")


def main():
    parser = argparse.ArgumentParser(description="Deploy a strategy to QuantConnect in one command.")
    parser.add_argument("strategy", help="Path to the .py algorithm file.")
    parser.add_argument("--project", default=None, help="QC project name (default: derived from filename).")
    parser.add_argument("--name", default="deploy", help="Backtest name.")
    parser.add_argument("--params", nargs="*", default=[], help="Backtest parameters as key=value.")
    parser.add_argument("--no-backtest", action="store_true", help="Deploy + compile only.")
    args = parser.parse_args()

    _load_env()
    if not os.path.exists(args.strategy):
        sys.exit(f"No such file: {args.strategy}")
    content = open(args.strategy).read()
    project_name = args.project or "Deploy " + os.path.splitext(os.path.basename(args.strategy))[0]
    params = {}
    for kv in args.params:
        if "=" not in kv:
            sys.exit(f"Bad --params entry (need key=value): {kv}")
        key, val = kv.split("=", 1)
        params[key] = val

    try:
        print(f"-> project: {project_name!r}")
        project_id = get_or_create_project(project_name)
        print(f"   projectId {project_id} — pushing {args.strategy}")
        push_main(project_id, content)
        print("   compiling ...")
        compile_id = compile_project(project_id)
        print("   build success")
        url = f"https://www.quantconnect.com/project/{project_id}"
        if args.no_backtest:
            print(f"\nDeployed (compile only): {url}")
            return
        print("   backtesting ...")
        backtest = run_backtest(project_id, compile_id, args.name, params or None)
        stats = backtest.get("statistics", {})
        print("\n=== Results ===")
        for key in ("Win Rate", "Total Orders", "Net Profit", "Profit-Loss Ratio",
                    "Sharpe Ratio", "Drawdown", "Expectancy"):
            if key in stats:
                print(f"  {key:<18}: {stats[key]}")
        print(f"\n  {url}")
    except QCError as exc:
        sys.exit(f"QuantConnect API error: {exc}")


if __name__ == "__main__":
    main()
