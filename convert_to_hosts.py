import os
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests

# Domain validation (RFC 1123 ASCII subset):
# - labels: [a-zA-Z0-9], hyphens allowed inside, max 63 chars each (no leading/trailing hyphens)
# - total length: 1–253 chars
# - TLD: ASCII alpha only, 2–24 chars
# Note: IDN/punycode TLDs (xn--) are intentionally excluded
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}$"
)

# Default sources (fallback if config.toml is not found)
# Keep in sync with config.toml.example
SOURCES = [
    # "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/light.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt",
    # "https://...",  # add more sources as needed
]


def _get_output_file() -> Path:
    """Resolve output file path from OUTPUT_DIR env var or fall back to CWD.

    OUTPUT_DIR is set in Docker to /output (a dedicated writable volume).
    When running locally (uv or bare Python), OUTPUT_DIR is not set -> writes to CWD.
    """
    output_dir = os.environ.get("OUTPUT_DIR")
    return Path(output_dir) / "hosts.txt" if output_dir else Path("hosts.txt")


def load_config(config_file: str | Path = "config.toml") -> list[str]:
    """Load sources from TOML config file.

    Falls back to default sources if config file doesn't exist or is malformed.

    Args:
        config_file: Path to config.toml file

    Returns:
        List of source URLs
    """
    config_path = Path(config_file)
    if not config_path.exists():
        print(f"Note: {config_file} not found, using default sources")
        return SOURCES

    try:
        with config_path.open("rb") as f:
            config = tomllib.load(f)
        urls = config.get("sources", {}).get("urls", SOURCES)
        print(f"Loaded {len(urls)} sources from {config_file}")
        return urls
    except tomllib.TOMLDecodeError as e:
        print(f"Error loading {config_file}: {e}. Using default sources.")
        return SOURCES


def fetch_rules(url: str) -> tuple[list[str], float]:
    """Fetch rules from URL with retry logic and return (rules, elapsed_time_in_seconds).

    Fetches AdBlock rules from a remote URL with exponential backoff retry mechanism.
    Streams the response to avoid loading large files entirely into memory.
    Pre-filters empty lines and comment-only lines so callers receive only candidate rules.

    A dedicated Session is created per call so each thread has its own connection pool
    without sharing mutable state across threads (requests.Session is not thread-safe).

    Args:
        url: The remote URL to fetch rules from.

    Returns:
        A tuple of (rules, elapsed_time_seconds) where:
            - rules: List of non-empty, non-comment lines, or [] if all attempts fail.
            - elapsed_time_seconds: Total time spent fetching (including retries).

    Note:
        Attempts up to 3 times with exponential backoff: 2s after 1st failure, 4s after 2nd.
    """
    fetch_start = time.monotonic()
    last_exception = None

    with requests.Session() as session:
        for attempt in range(3):
            try:
                with session.get(url, timeout=(3, 10), stream=True) as response:
                    response.raise_for_status()
                    rules = [
                        line
                        for line in response.iter_lines(decode_unicode=True)
                        if line.strip() and not line.lstrip().startswith("#")
                    ]
                    elapsed = time.monotonic() - fetch_start
                    return rules, elapsed
            except requests.RequestException as e:
                last_exception = e
                if attempt < 2:
                    wait = 2 ** (attempt + 1)
                    print(
                        f"Attempt {attempt + 1} failed for {url}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)

    print(f"Error fetching {url} after 3 attempts: {last_exception}")
    elapsed = time.monotonic() - fetch_start
    return [], elapsed


def extract_domain(rule: str) -> str | None:
    """Extract and validate domain from AdBlock-style rule.

    Transforms AdBlock/uBlock Origin rules (e.g., "||example.com^") into a
    clean domain string. Strips trailing comments, modifiers ($third-party),
    and whitespace. Rejects domains that fail RFC 1123 validation.

    Args:
        rule: Raw AdBlock rule string, may include comments or modifiers.

    Returns:
        Lowercase domain string (e.g., "example.com") for valid rules,
        or None if the rule is invalid, empty, or unsupported.

    Examples:
        >>> extract_domain("||example.com^")
        "example.com"
        >>> extract_domain("||ads.google.com^$third-party")
        "ads.google.com"
        >>> extract_domain("||invalid_domain^")
        None
    """

    rule = rule.split("#", 1)[0].strip()
    if not rule:
        return None

    if rule.startswith("||") and "^" in rule:
        domain = rule[2:].split("^")[0]
        if _DOMAIN_RE.match(domain):
            return domain.lower()
    return None


def write_output(
    output_file: Path,
    source_data: dict[str, list[str]],
    total_count: int,
    urls: list[str],
) -> None:
    """Write validated domains to hosts file with header metadata.

    Produces a hosts-format file with:
        - A descriptive header (timestamp, source URLs, domain counts)
        - Per-source sections with the "0.0.0.0 " prefix added at write time
        - A final total count line

    Args:
        output_file: Destination file path.
        source_data: Ordered mapping of URL → list of validated domain strings.
        total_count: Total number of unique domains across all sources.
        urls: Original URL list, used to preserve source order in the header.
    """
    current_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    source_lines = "".join(
        f"# - {url.split('/')[-1]} --> {len(domains):,} unique domains\n"
        for url, domains in source_data.items()
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
        f"# Total unique domains: {total_count:,}\n"
        f"{source_lines}"
        "#\n"
    )

    with output_file.open("w", encoding="utf-8") as f:
        f.write(header)

        for url, domains in source_data.items():
            f.write(f"\n# Source: {url}\n\n")
            for domain in domains:
                # prefix is added directly during file writing
                f.write(f"0.0.0.0 {domain}\n")
            f.write(f"\n# Converted {len(domains):,} rules from this source\n\n")

        f.write(f"\n# Total unique domains: {total_count:,}\n")


def main() -> None:
    start_time = time.monotonic()
    urls = load_config("config.toml")
    output_file = _get_output_file()

    unique_domains: set[str] = set()
    source_data: dict[str, list[str]] = {}
    raw_results: dict[str, list[str]] = {}

    print(f"Starting conversion of {len(urls)} source(s)...\n")

    # Stage 1: Asynchronous loading (we maintain a good UX with logging as results come in)
    with ThreadPoolExecutor(max_workers=len(urls)) as executor:
        futures = {executor.submit(fetch_rules, url): url for url in urls}

        for future in as_completed(futures):
            url = futures[future]
            rules, fetch_elapsed = future.result()
            print(
                f"Fetched {len(rules):,} lines from {url.split('/')[-1]} ({fetch_elapsed:.2f}s)"
            )
            raw_results[url] = rules

    # Stage 2: Sequential processing and deduplication strictly in order of config (urls)
    for url in urls:
        if url not in raw_results:
            continue

        converted = []
        for rule in raw_results[url]:
            domain = extract_domain(rule)
            if domain and domain not in unique_domains:
                unique_domains.add(domain)
                converted.append(domain)

        source_data[url] = converted

        filename = url.split("/")[-1]
        print(f"Converted {len(converted):,} unique domains from {filename}\n")

    if not unique_domains:
        print("Warning: No valid rules were converted. Skipping writing to file.")
        return

    print(f"Total unique domains across all sources: {len(unique_domains):,}")

    write_output(output_file, source_data, len(unique_domains), urls)

    elapsed_time = time.monotonic() - start_time
    print(f"Done! Written to: {output_file}")
    print(f"Elapsed: {elapsed_time:.2f}s")


if __name__ == "__main__":
    main()
