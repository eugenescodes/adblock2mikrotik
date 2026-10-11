import argparse
import logging
import os
import re
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from http.client import HTTPException
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from adblock2mikrotik import __version__

# Domain validation (RFC 1123 ASCII subset):
# - Labels: ASCII letters/digits; hyphens only inside, max 63 chars.
# - total length: 1-253 chars
# - TLD: ASCII letters only, 2-24 chars
# Note: IDN/punycode TLDs (xn--) are intentionally excluded
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}$"
)

# The packaged config is the single source of truth for default URLs.
# importlib.resources finds it in local, Docker, and uvx installs.
_DEFAULT_CONFIG_FILE: Traversable = (
    files("adblock2mikrotik") / "config.toml.example"
)

logger = logging.getLogger(__name__)


def _get_output_file() -> Path:
    """Get output path from OUTPUT_DIR or the current directory."""
    output_dir = os.environ.get("OUTPUT_DIR")
    return Path(output_dir) / "hosts.txt" if output_dir else Path("hosts.txt")


def _source_name(url: str) -> str:
    """Return the URL's final path segment for logs and headers."""
    return url.rpartition("/")[2]


def _read_source_urls(path: Traversable) -> list[str] | None:
    """Read unique source URLs from TOML, preserving configured order.

    Return None if the file is missing, invalid, or lacks a valid
    ``[sources] urls`` list.
    """
    if not path.is_file():
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
    if not isinstance(urls, list) or not all(
        isinstance(url, str) for url in urls
    ):
        return None

    # Avoid duplicate fetches; main() consumes each URL's result once.
    return list(dict.fromkeys(urls))


def load_config(
    config_file: str | Path = "config.toml", *, required: bool = False
) -> list[str]:
    """Load URLs from a config file or the packaged defaults.

    Invalid existing files return an empty list instead of silently
    falling back. A missing file falls back unless ``required`` is true.

    Args:
        config_file: TOML file to read.
        required: Treat a missing file as an error.

    Returns:
        Source URLs, or an empty list if the configuration is unusable.
    """
    config_path = Path(config_file)

    if required and not config_path.exists():
        logger.error(f"Config file {config_file} not found.")
        return []

    if config_path.exists():
        urls = _read_source_urls(config_path)
        if urls:
            logger.info(f"Loaded {len(urls)} sources from {config_file}")
            return urls
        logger.error(
            f"{config_file} has no usable [sources] urls — "
            "refusing to fall back to defaults."
        )
        return []

    logger.info(
        f"{config_file} not found, using default sources "
        f"from {_DEFAULT_CONFIG_FILE.name}"
    )

    default_urls = _read_source_urls(_DEFAULT_CONFIG_FILE)
    if not default_urls:
        logger.error(
            f"Default source file {_DEFAULT_CONFIG_FILE} "
            "is missing or invalid."
        )
        return []

    logger.info(
        f"Loaded {len(default_urls)} default sources "
        f"from {_DEFAULT_CONFIG_FILE.name}"
    )
    return default_urls


def fetch_domains(url: str) -> tuple[list[str], float]:
    """Fetch a filter list and return domains with elapsed time.

    Streams HTTP(S) sources and retries transient failures with backoff.
    Unsupported URL schemes return no domains.

    Args:
        url: HTTP(S) source URL.

    Returns:
        Validated domains and elapsed time, or an empty list on failure.
    """
    fetch_start = time.monotonic()
    if urlsplit(url).scheme.lower() not in {"http", "https"}:
        logger.error("Unsupported URL scheme for source: %s", url)
        return [], time.monotonic() - fetch_start

    last_exception: Exception | None = None

    for attempt in range(3):
        try:
            # The source scheme was restricted to HTTP(S) above.
            with urlopen(url, timeout=10) as response:  # noqa: S310
                domains: list[str] = []
                for raw_line in response:
                    domain = extract_domain(
                        raw_line.decode("utf-8", errors="replace")
                    )
                    if domain:
                        domains.append(domain)

                elapsed = time.monotonic() - fetch_start
                return domains, elapsed
        except (OSError, HTTPException) as e:
            last_exception = e
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    f"Attempt {attempt + 1} failed for {url}: {e}. "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)

    logger.error(f"Error fetching {url} after 3 attempts: {last_exception}")
    elapsed = time.monotonic() - fetch_start
    return [], elapsed


def extract_domain(rule: str) -> str | None:
    """Extract and validate a domain from an AdBlock rule.

    Return a lowercase domain, or None for invalid rules. Comments,
    modifiers, and surrounding whitespace are stripped.
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
    """Write a hosts file with source metadata using atomic replacement.

    Args:
        output_file: Destination path.
        source_data: Ordered mapping of source URLs to unique domains.
        total_count: Number of unique domains across all sources.
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
        "# https://eugenescodes.github.io/adblock2mikrotik/hosts.txt\n"
        "#\n"
        "# Homepage: https://github.com/eugenescodes/adblock2mikrotik\n"
        "# License: https://github.com/eugenescodes/adblock2mikrotik/blob/main/LICENSE\n"
        "#\n"
        f"# Last modified: {current_time}\n"
        "#\n"
        "# This filter is generated from the following DNS "
        "blocklist sources:\n"
        f"{url_lines}"
        "#\n"
        f"# Total unique domains: {total_count:,}\n"
        f"{source_lines}"
        "#\n"
    )

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_file.parent,
            prefix=f".{output_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            f.write(header)

            for url, domains in source_data.items():
                f.write(f"\n# Source: {url}\n\n")
                for domain in domains:
                    # prefix is added directly during file writing
                    f.write(f"0.0.0.0 {domain}\n")
                f.write(
                    f"\n# Converted {len(domains):,} rules "
                    "from this source\n\n"
                )

            f.write(f"\n# Total unique domains: {total_count:,}\n")

        tmp_path.replace(output_file)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    """Command-line interface of the ``adblock2mikrotik`` command."""
    parser = argparse.ArgumentParser(
        prog="adblock2mikrotik",
        description=(
            "Convert AdBlock-style filter lists (||domain^) into a hosts file "
            "for the MikroTik RouterOS DNS adlist."
        ),
        epilog=(
            "Without --config, sources are read from ./config.toml "
            "if it exists, "
            "otherwise the bundled default sources are used. "
            "Without --output, "
            "hosts.txt is written to $OUTPUT_DIR if set, otherwise to the "
            "current directory."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        metavar="FILE",
        help="TOML file with the [sources] urls list (must exist)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="FILE",
        help="where to write the hosts file",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help=(
            "fetch, validate and deduplicate as usual, but don't "
            "write the hosts file"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only log warnings and errors",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """
    Entry point of the ``adblock2mikrotik`` command.

    Args:
        argv:Command-line arguments without the program name; None means
             ``sys.argv[1:]``.
    """
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    start_time = time.monotonic()
    if args.config is not None:
        urls = load_config(args.config, required=True)
    else:
        urls = load_config("config.toml")

    if not urls:
        # load_config has already reported why the source list
        # is unusable;
        # fail loudly (non-zero) instead of leaving a stale
        # hosts.txt in place.
        raise SystemExit(1)

    output_file: Path = args.output or _get_output_file()

    fetched_domains: dict[str, list[str]] = {}
    failed_sources: list[str] = []

    logger.info(f"Fetching {len(urls)} source(s)...")

    # Stage 1: Parallel fetching with in-flight conversion. Each worker
    # converts
    # its raw rule text into validated domains while still
    # streaming, so raw
    # rule lines are never held in memory — only domain strings are.
    # fetch_domains handles network errors itself (returning []),
    # so anything
    # future.result() raises is a bug: it propagates and aborts
    # the run before
    # hosts.txt is touched.
    with ThreadPoolExecutor(max_workers=min(len(urls), 3)) as executor:
        futures = {executor.submit(fetch_domains, url): url for url in urls}

        for future in as_completed(futures):
            url = futures[future]
            domains, fetch_elapsed = future.result()
            if domains:
                logger.info(
                    f"  - {_source_name(url)}: {len(domains):,} domains "
                    f"({fetch_elapsed:.2f}s)"
                )
            else:
                # fetch_domains returns [] once its
                # retries are exhausted
                # (or the source holds no supported
                # rule), and an upstream
                # filter list is never legitimately empty.
                logger.error(f"  - {_source_name(url)}: no domains fetched")
                failed_sources.append(url)
            fetched_domains[url] = domains

    # A configured source that could not be fetched means the
    # artifact would be
    # narrower than what the config promises, so the whole run
    # fails below.
    if failed_sources:
        logger.error(
            f"{len(failed_sources)} of {len(urls)} source(s) failed to fetch "
            f"({', '.join(failed_sources)}) — "
            "refusing to publish a partial list."
        )
        raise SystemExit(1)

    # Stage 2: Deduplication strictly in order of config (urls).
    # The first
    # source in the config keeps every domain it shares with later
    # sources,
    # and output sections follow the same deterministic order on
    # every run.
    logger.info("Deduplicating...")

    unique_domains: set[str] = set()
    source_data: dict[str, list[str]] = {}

    for url in urls:
        converted = []
        for domain in fetched_domains[url]:
            if domain not in unique_domains:
                unique_domains.add(domain)
                converted.append(domain)

        source_data[url] = converted
        # fetched domains for this source are no longer needed once
        # deduplicated
        del fetched_domains[url]

        logger.info(
            f"  - {_source_name(url)}: {len(converted):,} unique domains"
        )

    # unique_domains cannot be empty here: the failed_sources
    # guard above
    # guarantees every configured source yielded at least one
    # validated domain.

    logger.info(
        f"Total unique domains across all sources: {len(unique_domains):,}"
    )

    if args.dry_run:
        logger.info(f"Dry run: {output_file} was not written.")
    else:
        write_output(output_file, source_data, len(unique_domains))
        logger.info(f"Done! Written to: {output_file}")
    elapsed_time = time.monotonic() - start_time
    logger.info(f"Elapsed: {elapsed_time:.2f}s")
