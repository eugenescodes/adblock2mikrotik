import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import requests

# Pre-compiled once at module load for performance
_COMMENT_RE = re.compile(r"#.*$")
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# OUTPUT_DIR is set in Docker to /output (a dedicated writable volume).
# When running locally (uv or bare Python), OUTPUT_DIR is not set -> writes to CWD.
_output_dir = os.environ.get("OUTPUT_DIR")
OUTPUT_FILE = os.path.join(_output_dir, "hosts.txt") if _output_dir else "hosts.txt"

SOURCES = [
    # "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/light.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.mini.txt",
    # "https://...",  # add more sources as needed
]


def fetch_rules(url: str) -> tuple[list[str], float]:
    """Fetch rules from URL and return (rules, elapsed_time_in_seconds)."""
    fetch_start = time.time()
    # Retry-logic: 3 attempts with exponential backoff: 2s → 4s (no wait after final attempt)
    last_exception = None
    for attempt in range(3):
        try:
            # stream=True: avoids loading the entire response into memory at once
            with requests.get(url, timeout=(3, 10), stream=True) as response:
                response.raise_for_status()
                rules = [
                    line for line in response.iter_lines(decode_unicode=True) if line
                ]
                elapsed = time.time() - fetch_start
                return rules, elapsed
        except requests.RequestException as e:
            last_exception = e
            if attempt < 2:  # don't wait after the last attempt
                wait = 2 ** (attempt + 1)  # 2s, then 4s
                print(f"Attempt {attempt + 1} failed for {url}. Retrying in {wait}s...")
                time.sleep(wait)
    print(f"Error fetching {url} after 3 attempts: {last_exception}")
    elapsed = time.time() - fetch_start
    return [], elapsed


def convert_rule(rule: str) -> str | None:
    rule = _COMMENT_RE.sub("", rule).strip()
    if not rule:
        return None
    if rule.startswith("||") and "^" in rule:
        domain = rule[2:].split("^")[0]
        if _DOMAIN_RE.match(domain):
            return f"0.0.0.0 {domain}"
    return None


def main() -> None:
    start_time = time.time()
    urls = SOURCES

    unique_rules: set[str] = set()
    source_data: dict[str, list[str]] = {}

    print(f"Starting conversion of {len(urls)} source(s)...\n")

    with ThreadPoolExecutor(max_workers=len(urls)) as executor:
        futures = {executor.submit(fetch_rules, url): url for url in urls}

        for future in as_completed(futures):
            url = futures[future]
            rules, fetch_elapsed = future.result()
            print(
                f"Fetched {len(rules):,} lines from {url.split('/')[-1]} ({fetch_elapsed:.2f}s)"
            )
            # print(f"Fetched {len(rules):,} lines from {url} ({fetch_elapsed:.2f}s)")

            converted = []
            for rule in rules:
                result = convert_rule(rule)
                if result and result not in unique_rules:
                    unique_rules.add(result)
                    converted.append(result)

            source_data[url] = converted
            print(f"Converted {len(converted):,} unique domains\n")

    # Restore original URL order (as_completed is non-deterministic)
    source_data = {url: source_data[url] for url in urls if url in source_data}

    print(f"Total unique domains across all sources: {len(unique_rules):,}")

    # Write header with timestamp
    current_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    source_lines = "".join(
        f"# - {url.split('/')[-1]} --> {len(rules):,} unique domains\n"
        for url, rules in source_data.items()
    )
    url_lines = "".join(f"# - {url}\n" for url in urls)

    header = (
        "# Title: Unified DNS blocklist optimized for RouterOS,\n"
        "# compiled from Hagezi sources\n"
        "#\n"
        "# URL to add in RouterOS:\n"
        "# https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt\n"
        "#\n"
        "# Homepage: https://github.com/eugenescodes/adblock2mikrotik\n"
        "# License: https://github.com/eugenescodes/adblock2mikrotik/blob/main/LICENSE\n"
        "#\n"
        f"# Last modified: {current_time}\n"
        "#\n"
        "# This filter is generated using the following Hagezi DNS blocklist sources:\n"
        f"{url_lines}"
        "#\n"
        f"# Total unique domains: {len(unique_rules):,}\n"
        f"{source_lines}"
        "#\n"
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write(header)

        for url, rules in source_data.items():
            file.write(f"\n# Source: {url}\n\n")
            for rule in rules:
                file.write(rule + "\n")
            file.write(f"\n# Converted {len(rules):,} rules from this source\n\n")

        file.write(f"\n# Total unique domains: {len(unique_rules)}\n")

    elapsed_time = time.time() - start_time
    print(f"Done! Written to: {OUTPUT_FILE}")
    print(f"Elapsed: {elapsed_time:.2f}s")


if __name__ == "__main__":
    main()
