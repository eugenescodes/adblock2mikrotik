import os
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

from adblock2mikrotik import __version__, converter

# ---------------------------------------------------------------------------
# fetch_domains
# ---------------------------------------------------------------------------


@patch("adblock2mikrotik.converter.requests.Session")
def test_fetch_domains_success(mock_session_cls: MagicMock) -> None:
    """Test successful fetch and in-stream conversion of domains on first attempt."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = [
        "||example.com^",
        "# comment",
        "  ",
        "||test.com^",
    ]
    mock_response.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.get.return_value.__enter__.return_value = mock_response
    mock_session_cls.return_value.__enter__.return_value = mock_session

    result, elapsed = converter.fetch_domains("http://fakeurl")
    assert result == ["example.com", "test.com"]
    assert isinstance(elapsed, float)


@patch("adblock2mikrotik.converter.time.sleep")
@patch("adblock2mikrotik.converter.requests.Session")
def test_fetch_domains_retries_then_fails(
    mock_session_cls: MagicMock,
    mock_sleep: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test fetch retry logic: 3 attempts with exponential backoff, then failure."""
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.RequestException("Network error")
    mock_session_cls.return_value.__enter__.return_value = mock_session

    result, elapsed = converter.fetch_domains("http://fakeurl")

    assert result == []
    assert mock_session.get.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2)
    mock_sleep.assert_any_call(4)

    assert "Error fetching http://fakeurl after 3 attempts" in caplog.text


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point converter._DEFAULT_CONFIG_FILE at a controlled temp file,
    so fallback tests don't depend on the real config.toml.example content.
    """
    default_file = tmp_path / "config.toml.example"
    default_file.write_text(
        '[sources]\nurls = ["https://default.example/a.txt", "https://default.example/b.txt"]\n'
    )
    monkeypatch.setattr(converter, "_DEFAULT_CONFIG_FILE", default_file)
    return default_file


DEFAULT_URLS = ["https://default.example/a.txt", "https://default.example/b.txt"]


def test_load_config_file_not_found(
    default_config: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Falls back to config.toml.example when config.toml does not exist."""
    result = converter.load_config("nonexistent_config.toml")

    assert result == DEFAULT_URLS
    assert "not found" in caplog.text


def test_load_config_reads_urls(tmp_path: Path) -> None:
    """Reads URL list from a valid config.toml (no fallback needed)."""
    config = tmp_path / "config.toml"
    config.write_text(
        '[sources]\nurls = ["https://example.com/list1.txt", "https://example.com/list2.txt"]\n'
    )

    result = converter.load_config(config)

    assert result == ["https://example.com/list1.txt", "https://example.com/list2.txt"]


def test_load_config_missing_urls_key_is_an_error(
    default_config: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A present config.toml without a usable [sources] urls is a config error:
    it must NOT be silently replaced by the bundled defaults."""
    config = tmp_path / "config.toml"
    config.write_text("[sources]\n# no urls key\n")

    result = converter.load_config(config)

    assert result == []
    assert "no usable" in caplog.text


def test_load_config_invalid_toml_is_an_error(
    default_config: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed TOML is a config error, not a reason to use the defaults."""
    config = tmp_path / "config.toml"
    config.write_text("this is not valid toml ][[\n")

    result = converter.load_config(config)

    assert result == []
    assert "no usable" in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        '[sources]\nurls = "https://example.com/list.txt"\n',  # string, not a list
        "[sources]\nurls = [1, 2]\n",  # list of non-strings
        "[sources]\nurls = []\n",  # empty list -> nothing to do
        'sources = "not a table"\n',  # [sources] is not a table at all
    ],
    ids=[
        "urls_is_string",
        "urls_has_non_strings",
        "urls_is_empty",
        "sources_is_not_a_table",
    ],
)
def test_load_config_rejects_malformed_sources(
    default_config: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    content: str,
) -> None:
    """Structurally wrong [sources] must be rejected, not taken at face value.

    Regression: a bare string (``urls = "https://…"``) used to be returned as-is
    and then iterated character by character, so main() would "fetch" from
    "h", "t", "t"… and silently produce a bogus file.
    """
    config = tmp_path / "config.toml"
    config.write_text(content)

    assert converter.load_config(config) == []
    assert "no usable" in caplog.text


def test_load_config_deduplicates_urls_preserving_order(
    default_config: Path, tmp_path: Path
) -> None:
    """Duplicate source URLs collapse to a single entry (config order kept).

    Regression: main() keys fetched results by URL and deletes each key as it
    converts, so a URL listed twice raised KeyError mid-conversion.
    """
    config = tmp_path / "config.toml"
    config.write_text(
        "[sources]\n"
        'urls = ["https://example.com/a.txt", "https://example.com/b.txt", '
        '"https://example.com/a.txt"]\n'
    )

    assert converter.load_config(config) == [
        "https://example.com/a.txt",
        "https://example.com/b.txt",
    ]


@pytest.mark.parametrize(
    "make_default_file",
    [
        lambda p: None,  # default file simply doesn't exist
        lambda p: p.write_text("[sources]\n# no urls key here either\n"),
    ],
    ids=["default_file_missing", "default_file_has_no_urls"],
)
def test_load_config_returns_empty_when_fallback_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    make_default_file: Callable[[Path], object],
) -> None:
    """If config.toml is missing AND the bundled config.toml.example is itself
    missing or unusable, load_config must degrade to an empty list (not raise)
    so main() can report a clear error instead of crashing.
    """
    default_file = tmp_path / "config.toml.example"
    make_default_file(default_file)
    monkeypatch.setattr(converter, "_DEFAULT_CONFIG_FILE", default_file)

    result = converter.load_config(tmp_path / "nonexistent_config.toml")

    assert result == []
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_bundled_default_config_is_valid() -> None:
    """The real config.toml.example must ship inside the installed package and
    hold a usable source list — otherwise the fallback breaks for everyone who
    runs without their own config.toml (CI, Docker, uvx)."""
    urls = converter._read_source_urls(converter._DEFAULT_CONFIG_FILE)

    assert urls
    assert all(url.startswith("https://") for url in urls)


# ---------------------------------------------------------------------------
# _get_output_file
# ---------------------------------------------------------------------------


def test_get_output_file_default() -> None:
    """Returns 'hosts.txt' in CWD when OUTPUT_DIR is not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OUTPUT_DIR", None)
        assert converter._get_output_file() == Path("hosts.txt")


def test_get_output_file_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns path inside OUTPUT_DIR when env var is set."""
    monkeypatch.setenv("OUTPUT_DIR", "/output")
    assert converter._get_output_file() == Path("/output/hosts.txt")


def test_source_name() -> None:
    """source_name returns the last path segment of a URL used in logs/header."""
    assert converter._source_name("https://example.com/list1.txt") == "list1.txt"
    assert converter._source_name("https://example.com/a/b/list2.txt") == "list2.txt"
    assert converter._source_name("https://example.com/") == ""


# ---------------------------------------------------------------------------
# main / write_output
# ---------------------------------------------------------------------------


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.replace")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main(
    mock_file: MagicMock,
    mock_replace: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
) -> None:
    """Test main orchestration: fetch, convert, deduplicate, and write to file."""
    mock_load_config.return_value = DEFAULT_URLS
    mock_fetch_domains.return_value = (
        [
            "example.com",
            "example.com",  # duplicate within one source
            "test.com",
        ],
        0.5,
    )

    converter.main([])

    # Verify that the file was opened for writing via pathlib (the temp file)
    mock_file.assert_called_once_with("w", encoding="utf-8")

    # Verify the temp file was atomically moved into place over the real output file
    mock_replace.assert_called_once_with(converter._get_output_file())

    # fetch_domains must be called once per source URL
    assert mock_fetch_domains.call_count == len(DEFAULT_URLS)

    handle = mock_file()
    written_text = "".join(call.args[0] for call in handle.write.call_args_list)

    assert "Title:" in written_text
    assert "0.0.0.0 example.com" in written_text
    assert written_text.count("0.0.0.0 example.com") == 1  # deduplicated globally
    assert "0.0.0.0 test.com" in written_text
    assert "# Converted 2 rules from this source" in written_text
    # First source converts 2 rules; all subsequent sources return duplicates -> 0 unique each
    assert (
        written_text.count("# Converted 0 rules from this source")
        == len(DEFAULT_URLS) - 1
    )


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.replace")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_distinct_unique_domains_per_source(
    mock_file: MagicMock,
    mock_replace: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
) -> None:
    """Each source can contribute genuinely different unique domains — not just
    "first source has everything, the rest are 0", as in test_main.

    Also verifies that a domain repeated *across different* sources (not just
    duplicated within one source) is still deduplicated globally and counted
    only once, attributed to whichever source is processed first (config order).
    """
    mock_load_config.return_value = DEFAULT_URLS
    url_a, url_b = DEFAULT_URLS

    domains_by_url = {
        url_a: ["alpha.com", "shared.com"],
        url_b: ["beta.com", "shared.com"],  # shared.com repeats across sources
    }
    mock_fetch_domains.side_effect = lambda url: (domains_by_url[url], 0.1)

    converter.main([])

    handle = mock_file()
    written_text = "".join(call.args[0] for call in handle.write.call_args_list)

    # Each source's own unique domain is present
    assert "0.0.0.0 alpha.com" in written_text
    assert "0.0.0.0 beta.com" in written_text

    # shared.com appears in both sources but must be written only once overall
    assert written_text.count("0.0.0.0 shared.com") == 1
    assert "Total unique domains: 3" in written_text

    # First source (url_a): alpha.com + shared.com = 2 new domains.
    # Second source (url_b): beta.com is new, shared.com was already seen = 1 new domain.
    assert "# Converted 2 rules from this source" in written_text
    assert "# Converted 1 rules from this source" in written_text


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_unfetchable_source_fails_the_run(
    mock_file: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A source that yields nothing must fail the run with a non-zero status —
    never a KeyError from the conversion loop, never a silent success."""
    mock_load_config.return_value = ["https://example.com/list.txt"]
    mock_fetch_domains.return_value = ([], 0.5)

    with pytest.raises(SystemExit) as excinfo:
        converter.main([])

    assert excinfo.value.code == 1
    mock_file.assert_not_called()
    assert "failed to fetch" in caplog.text


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_unexpected_worker_error_propagates(
    mock_file: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
) -> None:
    """fetch_domains handles network errors itself, so any other exception is
    a bug: it must propagate (non-zero exit) and nothing may be written."""
    mock_load_config.return_value = ["https://example.com/list.txt"]
    mock_fetch_domains.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        converter.main([])

    mock_file.assert_not_called()


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_partial_source_failure_exits_nonzero(
    mock_file: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If one of several sources is unavailable, publishing the smaller list
    would silently narrow the blocklist for every subscriber, so the run must
    fail and leave the previous hosts.txt in place."""
    url_ok = "https://example.com/ok.txt"
    url_bad = "https://example.com/bad.txt"
    mock_load_config.return_value = [url_ok, url_bad]
    mock_fetch_domains.side_effect = lambda url: (
        (["ads.example.com"], 0.1) if url == url_ok else ([], 0.1)
    )

    with pytest.raises(SystemExit) as excinfo:
        converter.main([])

    assert excinfo.value.code == 1
    mock_file.assert_not_called()
    assert "1 of 2 source(s) failed to fetch" in caplog.text
    assert url_bad in caplog.text


@patch("adblock2mikrotik.converter.fetch_domains")
@patch("adblock2mikrotik.converter.load_config")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_source_without_supported_rules_exits_nonzero(
    mock_file: MagicMock,
    mock_load_config: MagicMock,
    mock_fetch_domains: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every attempt succeeded, but the source contained no supported ||domain^
    rule — fetch_domains returns [] and the run must fail instead of exiting 0
    with a stale hosts.txt in place."""
    mock_load_config.return_value = ["https://example.com/list.txt"]
    mock_fetch_domains.return_value = ([], 0.5)

    with pytest.raises(SystemExit) as excinfo:
        converter.main([])

    assert excinfo.value.code == 1
    mock_file.assert_not_called()
    assert "no domains fetched" in caplog.text


@patch("adblock2mikrotik.converter.load_config")
@patch("adblock2mikrotik.converter.fetch_domains")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_no_sources_exits_before_conversion(
    mock_file: MagicMock,
    mock_fetch_domains: MagicMock,
    mock_load_config: MagicMock,
) -> None:
    """Regression test: an unusable source list must abort with a non-zero exit
    status *before* the thread pool is built — ThreadPoolExecutor(max_workers=0)
    raises ValueError otherwise, and a silent exit-0 would leave CI green while
    nothing is ever regenerated.
    """
    mock_load_config.return_value = []

    with pytest.raises(SystemExit) as excinfo:
        converter.main([])

    assert excinfo.value.code == 1
    mock_fetch_domains.assert_not_called()
    mock_file.assert_not_called()


# Parameterized tests for extract_domain validation
@pytest.mark.parametrize(
    "rule, expected",
    [
        ("||example.com^", "example.com"),
        ("||example.com^$third-party", "example.com"),
        ("||example.com^  # comment", "example.com"),
        ("||Sub.DomAIN.ExAmPlE.cOm^", "sub.domain.example.com"),
    ],
)
def test_extract_domain_valid(rule: str, expected: str) -> None:
    """Test extraction of valid domains from AdBlock rules."""
    assert converter.extract_domain(rule) == expected


@pytest.mark.parametrize(
    "rule",
    [
        "",
        "# some comment",
        "|example.com^",
        "||invalid_domain^",
        "||example..com^",
        "||.example.com^",
        "||example.com.^",
    ],
)
def test_extract_domain_invalid(rule: str) -> None:
    """Test that invalid/unsupported Adblock rules return None."""
    assert converter.extract_domain(rule) is None


def test_write_output_direct(tmp_path: Path) -> None:
    """Direct unit test for write_output — verifies structure without going through main()."""
    output_file = tmp_path / "hosts.txt"
    url = "https://example.com/list.txt"
    source_data = {url: ["example.com", "test.com"]}

    converter.write_output(output_file, source_data, 2)

    content = output_file.read_text()

    # check Header
    assert "Title:" in content
    assert "Last modified:" in content
    assert "# - https://example.com/list.txt" in content

    # check that the 0.0.0.0 prefix is successfully inserted during file writing
    assert f"# Source: {url}" in content
    assert "0.0.0.0 example.com" in content
    assert "0.0.0.0 test.com" in content
    assert "# Converted 2 rules from this source" in content

    # Footer
    assert content.strip().endswith("Total unique domains: 2")


def test_write_output_no_leftover_temp_file(tmp_path: Path) -> None:
    """After a successful write, the hidden .tmp file must not remain on disk."""
    output_file = tmp_path / "hosts.txt"
    source_data = {"https://example.com/list.txt": ["example.com"]}

    converter.write_output(output_file, source_data, 1)

    assert output_file.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_output_preserves_existing_file_on_failure(tmp_path: Path) -> None:
    """If writing fails mid-way, the original output_file must be left untouched
    and the temporary file must be cleaned up (no partial/corrupt file visible)."""
    output_file = tmp_path / "hosts.txt"
    output_file.write_text("previous good content\n")
    source_data = {"https://example.com/list.txt": ["example.com"]}

    with patch("pathlib.Path.open", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            converter.write_output(output_file, source_data, 1)

    # Original file untouched, no leftover temp file
    assert output_file.read_text() == "previous good content\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_output_removes_temp_file_on_mid_write_failure(tmp_path: Path) -> None:
    """A failure after the temp file was created (mid-write) must remove it and
    leave the original output_file untouched."""

    class FailingDomains(list[str]):
        def __iter__(self) -> Iterator[str]:
            raise OSError("disk full")

    output_file = tmp_path / "hosts.txt"
    output_file.write_text("previous good content\n")
    source_data: dict[str, list[str]] = {
        "https://example.com/list.txt": FailingDomains(["example.com"])
    }

    with pytest.raises(OSError, match="disk full"):
        converter.write_output(output_file, source_data, 1)

    assert output_file.read_text() == "previous good content\n"
    assert list(tmp_path.glob(".*.tmp")) == []


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_version_exits_without_running(
    mock_fetch_domains: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """--version prints the package version and never starts a conversion."""
    with pytest.raises(SystemExit) as excinfo:
        converter.main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"adblock2mikrotik {__version__}"
    mock_fetch_domains.assert_not_called()


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_help_exits_without_running(
    mock_fetch_domains: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: --help used to be ignored and ran (and overwrote hosts.txt)."""
    with pytest.raises(SystemExit) as excinfo:
        converter.main(["--help"])

    assert excinfo.value.code == 0
    assert "--config" in capsys.readouterr().out
    mock_fetch_domains.assert_not_called()


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_unknown_argument_is_rejected(mock_fetch_domains: MagicMock) -> None:
    """Unknown arguments are a usage error (exit 2), not silently ignored."""
    with pytest.raises(SystemExit) as excinfo:
        converter.main(["--no-such-option"])

    assert excinfo.value.code == 2
    mock_fetch_domains.assert_not_called()


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_missing_explicit_config_is_an_error(
    mock_fetch_domains: MagicMock,
    tmp_path: Path,
    default_config: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A --config file that doesn't exist must fail the run — falling back to
    the defaults would silently publish sources the user didn't ask for."""
    with pytest.raises(SystemExit) as excinfo:
        converter.main(["--config", str(tmp_path / "missing.toml")])

    assert excinfo.value.code == 1
    assert "not found" in caplog.text
    mock_fetch_domains.assert_not_called()


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_config_and_output_options(
    mock_fetch_domains: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--config selects the sources and --output the destination, taking
    precedence over ./config.toml and $OUTPUT_DIR."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "ignored"))
    config = tmp_path / "custom.toml"
    config.write_text('[sources]\nurls = ["https://example.com/custom.txt"]\n')
    output = tmp_path / "out" / "blocklist.txt"
    output.parent.mkdir()
    mock_fetch_domains.return_value = (["ads.example.com"], 0.1)

    converter.main(["-c", str(config), "-o", str(output), "-q"])

    mock_fetch_domains.assert_called_once_with("https://example.com/custom.txt")
    content = output.read_text()
    assert "# Source: https://example.com/custom.txt" in content
    assert "0.0.0.0 ads.example.com" in content
    assert not (tmp_path / "ignored").exists()


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_dry_run_does_not_write(
    mock_fetch_domains: MagicMock,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """--dry-run does the whole conversion but leaves the output untouched."""
    config = tmp_path / "config.toml"
    config.write_text('[sources]\nurls = ["https://example.com/list.txt"]\n')
    output = tmp_path / "hosts.txt"
    output.write_text("previous good content\n")
    mock_fetch_domains.return_value = (["ads.example.com"], 0.1)

    converter.main(["--dry-run", "-c", str(config), "-o", str(output)])

    mock_fetch_domains.assert_called_once()
    assert output.read_text() == "previous good content\n"
    assert list(tmp_path.glob(".*.tmp")) == []
    assert "Total unique domains across all sources: 1" in caplog.text
    assert "Dry run" in caplog.text


@patch("adblock2mikrotik.converter.fetch_domains")
def test_cli_dry_run_still_fails_on_unfetchable_source(
    mock_fetch_domains: MagicMock, tmp_path: Path
) -> None:
    """A dry run reports the same failures (exit 1) as a real run, so it can be
    used to validate a config before switching to it."""
    config = tmp_path / "config.toml"
    config.write_text('[sources]\nurls = ["https://example.com/list.txt"]\n')
    mock_fetch_domains.return_value = ([], 0.1)

    with pytest.raises(SystemExit) as excinfo:
        converter.main(["--dry-run", "-c", str(config)])

    assert excinfo.value.code == 1
