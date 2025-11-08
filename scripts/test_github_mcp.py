#!/usr/bin/env python3
"""Quick sanity-check script for the local GitHub MCP server.

Usage:
    export GITHUB_PERSONAL_ACCESS_TOKEN=...
    python scripts/test_github_mcp.py --owner capston2025 --repo capston --path README.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SERVER_DIR = Path("/Users/coldmans/학습/github-mcp-server")
SERVER_CMD = ["./github-mcp-server", "stdio", "--toolsets=default", "--read-only"]
PROTOCOL_VERSION = "2024-11-05"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a file via the GitHub MCP server")
    parser.add_argument("--owner", default="capston2025", help="GitHub repository owner")
    parser.add_argument("--repo", default="capston", help="GitHub repository name")
    parser.add_argument("--path", default="README.md", help="File path inside the repository")
    parser.add_argument("--server-dir", default=str(SERVER_DIR), help="Directory that contains github-mcp-server binary")
    parser.add_argument("--toolsets", default="default", help="Comma separated toolset list")
    parser.add_argument(
        "--no-read-only",
        action="store_false",
        dest="read_only",
        help="Allow write-capable tools (defaults to read-only)",
    )
    parser.set_defaults(read_only=True)
    return parser.parse_args()


def ensure_requirements(args: argparse.Namespace) -> None:
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        sys.exit("GITHUB_PERSONAL_ACCESS_TOKEN is not set")
    server_path = Path(args.server_dir)
    binary = server_path / "github-mcp-server"
    if not binary.exists():
        sys.exit(f"github-mcp-server binary not found at {binary}")


def spawn_server(args: argparse.Namespace) -> subprocess.Popen:
    env = os.environ.copy()
    token = env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_PERSONAL_ACCESS_TOKEN disappeared from environment")
    cmd = ["./github-mcp-server", "stdio", f"--toolsets={args.toolsets}"]
    if args.read_only:
        cmd.append("--read-only")

    return subprocess.Popen(
        cmd,
        cwd=args.server_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def send(proc: subprocess.Popen, payload: dict) -> None:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen, expected_id: int) -> dict:
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("Server closed connection before replying")
        message = json.loads(line)
        if message.get("id") == expected_id:
            return message
        # ignore notifications


def extract_snippet(result: dict) -> str:
    contents = result.get("content", [])
    snippets: list[str] = []
    for item in contents:
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            snippets.append(item["text"])
        elif item.get("type") == "resource":
            resource = item.get("resource", {})
            if isinstance(resource.get("text"), str):
                snippets.append(resource["text"])
            for embedded in resource.get("contents", []):
                if embedded.get("type") == "text":
                    snippets.append(embedded.get("text", ""))
    return "\n".join(snippets)


def main() -> None:
    args = parse_args()
    ensure_requirements(args)

    proc = spawn_server(args)
    try:
        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-smoke-test", "version": "0.1"},
                },
            },
        )
        _ = recv(proc, 1)

        send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_file_contents",
                    "arguments": {"owner": args.owner, "repo": args.repo, "path": args.path},
                },
            },
        )
        response = recv(proc, 2)
        result = response.get("result", {})
        snippet = extract_snippet(result)
        if not snippet:
            print("(No text snippet returned)")
        else:
            print("--- File snippet ---")
            print(snippet[:800])
            print("---------------------")
    finally:
        try:
            send(proc, {"jsonrpc": "2.0", "id": 99, "method": "shutdown"})
        except Exception:
            pass
        proc.terminate()
        stderr = proc.stderr.read()
        if stderr:
            print(stderr, file=sys.stderr)


if __name__ == "__main__":
    main()
