#!/usr/bin/env python3
"""Sync the local BMI30 version registry CSV to a Google Sheets Apps Script."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CSV = Path("docs/BMI30_version_registry_google_sheet.csv")
DEFAULT_WEBAPP_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbySnSSnTGsJmyMZFBrMHACiHDHRhBglk2zbpvCdjQiVd95ERR5MtiY65AO1lRIZ9YYh/exec"
)
DEFAULT_TOKEN_FILE = Path("secrets/bmi30_sheets.env")
TOKEN_PLACEHOLDERS = {"", "PASTE_TOKEN_HERE", "CHANGE_ME", "the same token from Script Properties"}


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
            if key:
                values[key] = value
    return values


def token_from_sources(token: str, token_file: Path) -> str:
    if token and token not in TOKEN_PLACEHOLDERS:
        return token
    env_value = os.getenv("BMI30_SHEETS_TOKEN", "")
    if env_value and env_value not in TOKEN_PLACEHOLDERS:
        return env_value
    env_file = Path(os.getenv("BMI30_SHEETS_TOKEN_FILE", str(token_file)))
    values = load_env_file(env_file)
    file_value = values.get("BMI30_SHEETS_TOKEN", "")
    if file_value and file_value not in TOKEN_PLACEHOLDERS:
        return file_value
    return ""


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = [{key: (value if value is not None else "") for key, value in row.items()} for row in reader]
    if not headers:
        raise ValueError(f"No CSV headers found in {path}")
    return headers, rows


def post_json(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response: {body[:500]}") from exc
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or result))
    return result


def redact(text: str, secrets: list[str]) -> str:
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "<redacted>")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Registry CSV path.")
    parser.add_argument("--url", default=os.getenv("BMI30_SHEETS_WEBAPP_URL", DEFAULT_WEBAPP_URL), help="Apps Script /exec URL.")
    parser.add_argument("--token", default="", help="Secret token; defaults to BMI30_SHEETS_TOKEN or secrets/bmi30_sheets.env.")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE, help="Default local token file path.")
    parser.add_argument("--key-column", default="ID версии", help="Column used for upsert matching.")
    parser.add_argument("--replace", action="store_true", help="Replace the sheet contents with the CSV instead of upserting.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Read CSV and print summary without uploading.")
    args = parser.parse_args(argv)

    headers, rows = read_csv(args.csv)
    print(f"CSV: {args.csv}")
    print(f"Rows: {len(rows)}")
    print(f"Columns: {len(headers)}")

    if args.dry_run:
        print("Dry run only; no upload performed.")
        return 0

    args.token = token_from_sources(args.token, args.token_file)
    if not args.token:
        print(
            "BMI30_SHEETS_TOKEN is required. Put it in secrets/bmi30_sheets.env "
            "or export it in the shell.",
            file=sys.stderr,
        )
        return 2

    payload = {
        "token": args.token,
        "headers": headers,
        "rows": rows,
        "keyColumn": args.key_column,
        "replace": bool(args.replace),
    }
    try:
        result = post_json(args.url, payload, args.timeout)
    except Exception as exc:
        print(redact(str(exc), [args.token]), file=sys.stderr)
        return 1
    print(
        "Uploaded: "
        f"mode={result.get('mode', 'upsert')} "
        f"updated={result.get('updated', 0)} "
        f"appended={result.get('appended', 0)} "
        f"sheet={result.get('sheetName', '')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
