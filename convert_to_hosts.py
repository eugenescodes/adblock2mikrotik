import os
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import requests

# Pre-compiled once at module load for performance
_COMMENT_RE = re.compile(r"\s*#.*$")
# Domain validation (RFC 1123 ASCII subset):
# - labels: [a-zA-Z0-9], hyphens allowed inside, max 63 chars each (no leading/trailing hyphens)
# - total length: 1–253 chars
# - TLD: ASCII alpha only, 2–24 chars
# Note: IDN/punycode TLDs (xn--) are intentionally excluded
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}$"
)

# OUTPUT_DIR is set in Docker to /output (a dedicated writable volume).
# When running locally (uv or bare Python), OUTPUT_DIR is not set -> writes to CWD.
_output_dir = os.environ.get("OUTPUT_DIR")
OUTPUT_FILE = os.path.join(_output_dir, "hosts.txt") if _output_dir else "hosts.txt"

# Default sources (fallback if config.toml is not found)
SOURCES = [
    # "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/light.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.mini.txt",
    # "https://...",  # add more sources as needed
]


def load_config(config_file: str = "config.toml") -> list[str]:
    """Load sources from TOML config file.

    Falls back to default sources if config file doesn't exist.

    Args:
        config_file: Path to config.toml file

    Returns:
        List of source URLs
    """
    if not os.path.exists(config_file):
        print(f"Note: {config_file} not found, using default sources")
        return SOURCES

    try:
        with open(config_file, "rb") as f:
            config = tomllib.load(f)
        urls = config.get("sources", {}).get("urls", SOURCES)
        print(f"Loaded {len(urls)} sources from {config_file}")
        return urls
    except Exception as e:
        print(f"Error loading {config_file}: {e}. Using default sources.")
        return SOURCES


def fetch_rules(url: str) -> tuple[list[str], float]:
    """Fetch rules from URL with retry logic and return (rules, elapsed_time_in_seconds).

    Fetches AdBlock rules from a remote URL with exponential backoff retry mechanism.
    Streams the response to avoid loading large files entirely into memory.
    Pre-filters empty lines and comment-only lines so callers receive only candidate rules.

    Args:
        url: The remote URL to fetch rules from.

    Returns:
        A tuple of (rules, elapsed_time_seconds) where:
            - rules: List of non-empty, non-comment lines from the response,
              or empty list if all attempts fail.
            - elapsed_time_seconds: Total time spent fetching (including retries), as float.

    Note:
        Attempts up to 3 times with exponential backoff: 2s after 1st failure, 4s after 2nd.
        Prints diagnostic messages on retry and final failure.
    """
    fetch_start = time.time()
    # Retry-logic: 3 attempts with exponential backoff: 2s → 4s (no wait after final attempt)
    # Pre-filters empty lines and comment-only lines (#) before returning,
    # so callers receive only candidate adblock rules.
    last_exception = None
    for attempt in range(3):
        try:
            # stream=True: avoids loading the entire response into memory at once
            with requests.get(url, timeout=(3, 10), stream=True) as response:
                response.raise_for_status()
                rules = [
                    line
                    for line in response.iter_lines(decode_unicode=True)
                    if line and not line.lstrip().startswith("#")
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
    """Convert AdBlock-style rule to /etc/hosts format (0.0.0.0 domain).

    Transforms AdBlock/uBlock Origin rules (e.g., "||example.com^") into the hosts file
    format compatible with MikroTik RouterOS DNS adlist (e.g., "0.0.0.0 example.com").

    Strips comments and whitespace. Only accepts rules with:
        - "||" prefix (domain anchor)
        - "^" separator (end of domain marker)
        - Valid RFC-compliant domain: each label alphanumeric + hyphens (no leading/trailing),
          max 63 chars per label, min 2-char alpha TLD. Rejects double-dots, underscores, etc.

    Args:
        rule: Raw AdBlock rule string, may include comments (# comment) or trailing modifiers.

    Returns:
        "0.0.0.0 domain.com" for valid rules, or None if rule is invalid/empty/unsupported.

    Examples:
        >>> convert_rule("||example.com^")
        "0.0.0.0 example.com"
        >>> convert_rule("||ads.google.com^$third-party")
        "0.0.0.0 ads.google.com"
        >>> convert_rule("||invalid_domain^")
        None
        >>> convert_rule("# comment")
        None
    """
    rule = _COMMENT_RE.sub("", rule).strip()
    if not rule:
        return None
    if rule.startswith("||") and "^" in rule:
        domain = rule[2:].split("^")[0]
        if _DOMAIN_RE.match(domain):
            return f"0.0.0.0 {domain.lower()}"
    return None


def main() -> None:
    """Main entry point: orchestrate fetching, converting, and writing DNS adlist.

    Executes the complete pipeline:
        1. Load sources from config.toml (or use default sources)
        2. Fetch AdBlock rules from all sources in parallel using ThreadPoolExecutor
        3. Convert each rule to /etc/hosts format (0.0.0.0 domain.com)
        4. Deduplicate entries across all sources
        5. Exit early if no rules were fetched (skips writing hosts.txt)
        6. Write to OUTPUT_FILE with a descriptive header including:
           - Timestamp (UTC)
           - Original source URLs and domain counts
           - Generation metadata

    Each source's converted rules are grouped in the output file with section comments.
    Progress is printed to stdout (fetch times, conversion counts, total elapsed time).
    """
    start_time = time.time()
    urls = load_config("config.toml")

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

    if not unique_rules:
        print("Warning: No valid rules were converted. Skipping writing to file.")
        return

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

    with open(OUTPUT_FILE, "w", encoding="utf-8") as output_file:
        output_file.write(header)

        for url, rules in source_data.items():
            output_file.write(f"\n# Source: {url}\n\n")
            for rule in rules:
                output_file.write(rule + "\n")
            output_file.write(
                f"\n# Converted {len(rules):,} rules from this source\n\n"
            )

        output_file.write(f"\n# Total unique domains: {len(unique_rules)}\n")

    elapsed_time = time.time() - start_time
    print(f"Done! Written to: {OUTPUT_FILE}")
    print(f"Elapsed: {elapsed_time:.2f}s")


if __name__ == "__main__":
    main()
