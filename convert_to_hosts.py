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

# Default sources are loaded from config.toml.example (see load_config below)
# instead of being duplicated as a Python literal here. config.toml.example is
# the single source of truth — update sources there, and both the "cp config
# .toml.example config.toml" workflow and the built-in fallback stay in sync
# automatically.
_DEFAULT_CONFIG_FILE = Path(__file__).resolve().parent / "config.toml.example"


def _get_output_file() -> Path:
    """Resolve output file path from OUTPUT_DIR env var or fall back to CWD.

    OUTPUT_DIR is set in Docker to /output (a dedicated writable volume).
    When running locally (uv or bare Python), OUTPUT_DIR is not set -> writes to CWD.
    """
    output_dir = os.environ.get("OUTPUT_DIR")
    return Path(output_dir) / "hosts.txt" if output_dir else Path("hosts.txt")


def _source_name(url: str) -> str:
    """Return the file name portion of a source URL (for logs/headers)."""
    return url.rpartition("/")[2]


def _read_source_urls(path: Path) -> list[str] | None:
    """Read the ``[sources] urls`` list from a TOML file.

    Returns:
        The list of URLs (deduplicated, order preserved), or None if the file
        is missing, is not valid TOML, or has no well-formed ``urls`` list
        under ``[sources]``. None (rather than an exception) lets callers
        apply their own fallback/error messaging.
    """
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return None

    sources = config.get("sources")
    if not isinstance(sources, dict):
        return None

    urls = sources.get("urls")
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        return None

    # Collapse duplicates (order preserved): main() keys fetched results by URL
    # and consumes each entry exactly once, so a repeated URL would both fetch
    # twice and raise KeyError during conversion.
    return list(dict.fromkeys(urls))


def load_config(config_file: str | Path = "config.toml") -> list[str]:
    """Load sources from TOML config file.

    An explicitly provided config_file that *exists* but is unusable (malformed
    TOML, no ``[sources] urls`` list, wrong value types, or an empty url list)
    is a configuration error: the problem is reported and an empty list is
    returned, so a typo in your own config is never silently replaced by the
    defaults. Only a *missing* config_file falls back to config.toml.example,
    the single source of truth for default sources. If that bundled fallback is
    itself unavailable, that is reported too and an empty list is returned.

    Args:
        config_file: Path to config.toml file

    Returns:
        List of source URLs, or an empty list when the configuration is
        unusable — main() treats an empty list as fatal.
    """
    config_path = Path(config_file)

    if config_path.exists():
        urls = _read_source_urls(config_path)
        if urls:
            print(f"Loaded {len(urls)} sources from {config_file}")
            return urls
        print(
            f"Error: {config_file} has no usable [sources] urls — "
            "refusing to fall back to defaults."
        )
        return []

    print(
        f"\nNote: {config_file} not found, using default sources from {_DEFAULT_CONFIG_FILE.name}"
    )

    default_urls = _read_source_urls(_DEFAULT_CONFIG_FILE)
    if not default_urls:
        print(
            f"Error: default source file {_DEFAULT_CONFIG_FILE} is missing or invalid."
        )
        return []

    print(
        f"Loaded {len(default_urls)} default sources from {_DEFAULT_CONFIG_FILE.name}"
    )
    return default_urls


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
                    rules: list[str] = []
                    for raw_line in response.iter_lines(decode_unicode=False):
                        line = (
                            raw_line.decode("utf-8", errors="replace")
                            if isinstance(raw_line, bytes)
                            else str(raw_line)
                        )
                        if line.strip() and not line.lstrip().startswith("#"):
                            rules.append(line)

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
) -> None:
    """Write validated domains to hosts file with header metadata.

    Produces a hosts-format file with:
        - A descriptive header (timestamp, source URLs, domain counts)
        - Per-source sections with the "0.0.0.0 " prefix added at write time
        - A final total count line

    The file is written atomically: content is first written to a hidden
    temporary file in the same directory as output_file, then moved into
    place with Path.replace() (an atomic rename on POSIX and Windows). This
    guarantees that readers of output_file (e.g. RouterOS fetching it over
    HTTP, or a concurrent process) never observe a partially-written file,
    even if this process is interrupted mid-write. On failure, the temporary
    file is removed and the exception is re-raised; output_file is left
    untouched.

    Args:
        output_file: Destination file path.
        source_data: Ordered mapping of URL → list of validated domain strings.
            Insertion order is the configured source order and drives the
            order of the header's source list and of the file's sections.
        total_count: Total number of unique domains across all sources.
    """
    current_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    source_lines = "".join(
        f"# - {_source_name(url)} --> {len(domains):,} unique domains\n"
        for url, domains in source_data.items()
    )
    url_lines = "".join(f"# - {url}\n" for url in source_data)

    header = (
        "# Title: Unified DNS blocklist optimized for RouterOS\n"
        "#\n"
        "# URL to add in RouterOS:\n"
        "# https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt\n"
        "#\n"
        "# Homepage: https://github.com/eugenescodes/adblock2mikrotik\n"
        "# License: https://github.com/eugenescodes/adblock2mikrotik/blob/main/LICENSE\n"
        "#\n"
        f"# Last modified: {current_time}\n"
        "#\n"
        "# This filter is generated from the following DNS blocklist sources:\n"
        f"{url_lines}"
        "#\n"
        f"# Total unique domains: {total_count:,}\n"
        f"{source_lines}"
        "#\n"
    )

    tmp_file = output_file.with_name(f".{output_file.name}.tmp")

    try:
        with tmp_file.open("w", encoding="utf-8") as f:
            f.write(header)

            for url, domains in source_data.items():
                f.write(f"\n# Source: {url}\n\n")
                for domain in domains:
                    # prefix is added directly during file writing
                    f.write(f"0.0.0.0 {domain}\n")
                f.write(f"\n# Converted {len(domains):,} rules from this source\n\n")

            f.write(f"\n# Total unique domains: {total_count:,}\n")
    except Exception:
        tmp_file.unlink(missing_ok=True)
        raise

    tmp_file.replace(output_file)


def main() -> None:
    start_time = time.monotonic()
    urls = load_config("config.toml")

    if not urls:
        # load_config has already reported why the source list is unusable;
        # fail loudly (non-zero) instead of leaving a stale hosts.txt in place.
        raise SystemExit(1)

    output_file = _get_output_file()

    unique_domains: set[str] = set()
    source_data: dict[str, list[str]] = {}
    raw_results: dict[str, list[str]] = {}
    failed_sources: list[str] = []

    print(f"\nFetching {len(urls)} source(s)...")

    # Stage 1: Asynchronous loading (we maintain a good UX with logging as results come in)
    with ThreadPoolExecutor(max_workers=len(urls)) as executor:
        futures = {executor.submit(fetch_rules, url): url for url in urls}

        for future in as_completed(futures):
            url = futures[future]
            try:
                rules, fetch_elapsed = future.result()
            except Exception as exc:
                print(f"  - {_source_name(url)}: ERROR: {exc}")
                rules, fetch_elapsed = [], 0.0
            else:
                if rules:
                    print(
                        f"  - {_source_name(url)}: {len(rules):,} lines ({fetch_elapsed:.2f}s)"
                    )
                else:
                    # fetch_rules returns [] once its retries are exhausted, and an
                    # upstream filter list is never legitimately empty.
                    print(f"  - {_source_name(url)}: ERROR: no rules fetched")

            if not rules:
                failed_sources.append(url)
            raw_results[url] = rules

    # A configured source that could not be fetched means the artifact would be
    # narrower than what the config promises, so the whole run fails below.
    if failed_sources:
        print(
            f"\nError: {len(failed_sources)} of {len(urls)} source(s) failed to fetch "
            f"({', '.join(failed_sources)}) — refusing to publish a partial list."
        )
        raise SystemExit(1)

    # Stage 2: Sequential processing and deduplication strictly in order of config (urls)
    print("\nConverting and deduplicating...")

    for url in urls:
        converted = []
        for rule in raw_results[url]:
            domain = extract_domain(rule)
            if domain and domain not in unique_domains:
                unique_domains.add(domain)
                converted.append(domain)

        source_data[url] = converted
        # raw results for this source are no longer needed once converted
        del raw_results[url]

        print(f"  - {_source_name(url)}: {len(converted):,} unique domains")

    if not unique_domains:
        # Every source answered, but none contained a supported ||domain^ rule:
        # nothing to write, and exiting 0 would report success while leaving the
        # previously published hosts.txt in place indefinitely.
        print(
            "Error: no valid rules were converted from any source "
            "(sources empty or in an unsupported format)."
        )
        raise SystemExit(1)

    print(f"\nTotal unique domains across all sources: {len(unique_domains):,}")

    write_output(output_file, source_data, len(unique_domains))

    elapsed_time = time.monotonic() - start_time
    print(f"Done! Written to: {output_file}")
    print(f"Elapsed: {elapsed_time:.2f}s\n")


if __name__ == "__main__":
    main()
