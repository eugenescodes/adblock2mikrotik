import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import convert_to_hosts

# ---------------------------------------------------------------------------
# fetch_rules
# ---------------------------------------------------------------------------


@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_success(mock_session_cls):
    """Test successful fetch of rules from URL on first attempt."""
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

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")
    assert result == ["||example.com^", "||test.com^"]
    assert isinstance(elapsed, float)


@patch("convert_to_hosts.time.sleep")
@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_retries_then_fails(mock_session_cls, mock_sleep, capsys):
    """Test fetch retry logic: 3 attempts with exponential backoff, then failure."""
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.RequestException("Network error")
    mock_session_cls.return_value.__enter__.return_value = mock_session

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == []
    assert mock_session.get.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2)
    mock_sleep.assert_any_call(4)

    captured = capsys.readouterr()
    assert "Error fetching http://fakeurl after 3 attempts" in captured.out


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config(tmp_path, monkeypatch):
    """Point convert_to_hosts._DEFAULT_CONFIG_FILE at a controlled temp file,
    so fallback tests don't depend on the real config.toml.example content.
    """
    default_file = tmp_path / "config.toml.example"
    default_file.write_text(
        '[sources]\nurls = ["https://default.example/a.txt", "https://default.example/b.txt"]\n'
    )
    monkeypatch.setattr(convert_to_hosts, "_DEFAULT_CONFIG_FILE", default_file)
    return default_file


DEFAULT_URLS = ["https://default.example/a.txt", "https://default.example/b.txt"]


def test_load_config_file_not_found(default_config, capsys):
    """Falls back to config.toml.example when config.toml does not exist."""
    result = convert_to_hosts.load_config("nonexistent_config.toml")

    assert result == DEFAULT_URLS
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_load_config_reads_urls(tmp_path):
    """Reads URL list from a valid config.toml (no fallback needed)."""
    config = tmp_path / "config.toml"
    config.write_text(
        '[sources]\nurls = ["https://example.com/list1.txt", "https://example.com/list2.txt"]\n'
    )

    result = convert_to_hosts.load_config(config)

    assert result == ["https://example.com/list1.txt", "https://example.com/list2.txt"]


def test_load_config_missing_urls_key_falls_back_to_defaults(default_config, tmp_path):
    """Falls back to config.toml.example when [sources] exists but 'urls' is absent."""
    config = tmp_path / "config.toml"
    config.write_text("[sources]\n# no urls key\n")

    result = convert_to_hosts.load_config(config)

    assert result == DEFAULT_URLS


def test_load_config_invalid_toml_falls_back_to_defaults(
    default_config, tmp_path, capsys
):
    """Falls back to config.toml.example and prints a note for malformed TOML."""
    config = tmp_path / "config.toml"
    config.write_text("this is not valid toml ][[\n")

    result = convert_to_hosts.load_config(config)

    assert result == DEFAULT_URLS
    captured = capsys.readouterr()
    assert "no usable" in captured.out


@pytest.mark.parametrize(
    "make_default_file",
    [
        lambda p: None,  # default file simply doesn't exist
        lambda p: p.write_text("[sources]\n# no urls key here either\n"),
    ],
    ids=["default_file_missing", "default_file_has_no_urls"],
)
def test_load_config_returns_empty_when_fallback_unavailable(
    tmp_path, monkeypatch, capsys, make_default_file
):
    """If config.toml is missing AND the bundled config.toml.example is itself
    missing or unusable, load_config must degrade to an empty list (not raise)
    so main() can report a clear error instead of crashing.
    """
    default_file = tmp_path / "config.toml.example"
    make_default_file(default_file)
    monkeypatch.setattr(convert_to_hosts, "_DEFAULT_CONFIG_FILE", default_file)

    result = convert_to_hosts.load_config(tmp_path / "nonexistent_config.toml")

    assert result == []
    captured = capsys.readouterr()
    assert "Error" in captured.out


# ---------------------------------------------------------------------------
# _get_output_file
# ---------------------------------------------------------------------------


def test_get_output_file_default():
    """Returns 'hosts.txt' in CWD when OUTPUT_DIR is not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OUTPUT_DIR", None)
        assert convert_to_hosts._get_output_file() == Path("hosts.txt")


def test_get_output_file_with_env(monkeypatch):
    """Returns path inside OUTPUT_DIR when env var is set."""
    monkeypatch.setenv("OUTPUT_DIR", "/output")
    assert convert_to_hosts._get_output_file() == Path("/output/hosts.txt")


# ---------------------------------------------------------------------------
# main / write_output
# ---------------------------------------------------------------------------


@patch("convert_to_hosts.fetch_rules")
@patch("convert_to_hosts.load_config")
@patch("pathlib.Path.replace")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main(mock_file, mock_replace, mock_load_config, mock_fetch_rules):
    """Test main orchestration: fetch, convert, deduplicate, and write to file."""
    mock_load_config.return_value = DEFAULT_URLS
    mock_fetch_rules.return_value = (
        [
            "||example.com^",
            "||example.com^",  # duplicate
            "||test.com^$third-party",
            "# comment",
            "",
        ],
        0.5,
    )

    convert_to_hosts.main()

    # Verify that the file was opened for writing via pathlib (the temp file)
    mock_file.assert_called_once_with("w", encoding="utf-8")

    # Verify the temp file was atomically moved into place over the real output file
    mock_replace.assert_called_once_with(convert_to_hosts._get_output_file())

    # fetch_rules must be called once per source URL
    assert mock_fetch_rules.call_count == len(DEFAULT_URLS)

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


@patch("convert_to_hosts.fetch_rules")
@patch("convert_to_hosts.load_config")
@patch("pathlib.Path.replace")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_distinct_unique_domains_per_source(
    mock_file, mock_replace, mock_load_config, mock_fetch_rules
):
    """Each source can contribute genuinely different unique domains — not just
    "first source has everything, the rest are 0", as in test_main.

    Also verifies that a domain repeated *across different* sources (not just
    duplicated within one source) is still deduplicated globally and counted
    only once, attributed to whichever source is processed first (config order).
    """
    mock_load_config.return_value = DEFAULT_URLS
    url_a, url_b = DEFAULT_URLS

    rules_by_url = {
        url_a: ["||alpha.com^", "||shared.com^"],
        url_b: ["||beta.com^", "||shared.com^"],  # shared.com repeats across sources
    }
    mock_fetch_rules.side_effect = lambda url: (rules_by_url[url], 0.1)

    convert_to_hosts.main()

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


@patch("convert_to_hosts.fetch_rules")
@patch("convert_to_hosts.load_config")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_empty_rules_skips_file_write(
    mock_file, mock_load_config, mock_fetch_rules, capsys
):
    """Test that main() skips writing to file when no valid rules are converted."""
    mock_load_config.return_value = ["https://example.com/list.txt"]
    mock_fetch_rules.return_value = (
        [
            "# comment only",
            "",
            "invalid_rule_without_pipes",
            "|single_pipe^",
        ],
        0.5,
    )

    convert_to_hosts.main()

    mock_file.assert_not_called()

    captured = capsys.readouterr()
    assert "Warning: No valid rules were converted" in captured.out
    assert "Skipping writing to file" in captured.out


@patch("convert_to_hosts.load_config")
@patch("convert_to_hosts.fetch_rules")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_no_sources_skips_conversion(
    mock_file, mock_fetch_rules, mock_load_config, capsys
):
    """Regression test: if no sources are configured (config.toml AND
    config.toml.example both unusable), main() must exit gracefully instead of
    crashing — ThreadPoolExecutor(max_workers=0) raises ValueError otherwise.
    """
    mock_load_config.return_value = []

    convert_to_hosts.main()

    mock_fetch_rules.assert_not_called()
    mock_file.assert_not_called()

    captured = capsys.readouterr()
    assert "No sources configured" in captured.out


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
def test_extract_domain_valid(rule, expected):
    """Test extraction of valid domains from AdBlock rules."""
    assert convert_to_hosts.extract_domain(rule) == expected


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
def test_extract_domain_invalid(rule):
    """Test that invalid/unsupported Adblock rules return None."""
    assert convert_to_hosts.extract_domain(rule) is None


def test_write_output_direct(tmp_path):
    """Direct unit test for write_output — verifies structure without going through main()."""
    output_file = tmp_path / "hosts.txt"
    urls = ["https://example.com/list.txt"]
    source_data = {urls[0]: ["example.com", "test.com"]}

    convert_to_hosts.write_output(output_file, source_data, 2, urls)

    content = output_file.read_text()

    # check Header
    assert "Title:" in content
    assert "Last modified:" in content
    assert "Total unique domains: 2" in content
    assert "# - https://example.com/list.txt" in content

    # check that the 0.0.0.0 prefix is successfully inserted during file writing
    assert "# Source: https://example.com/list.txt" in content
    assert "0.0.0.0 example.com" in content
    assert "0.0.0.0 test.com" in content
    assert "# Converted 2 rules from this source" in content

    # Footer
    assert content.strip().endswith("Total unique domains: 2")


def test_write_output_no_leftover_temp_file(tmp_path):
    """After a successful write, the hidden .tmp file must not remain on disk."""
    output_file = tmp_path / "hosts.txt"
    urls = ["https://example.com/list.txt"]
    source_data = {urls[0]: ["example.com"]}

    convert_to_hosts.write_output(output_file, source_data, 1, urls)

    assert output_file.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_write_output_preserves_existing_file_on_failure(tmp_path):
    """If writing fails mid-way, the original output_file must be left untouched
    and the temporary file must be cleaned up (no partial/corrupt file visible)."""
    output_file = tmp_path / "hosts.txt"
    output_file.write_text("previous good content\n")
    urls = ["https://example.com/list.txt"]
    source_data = {urls[0]: ["example.com"]}

    with patch("pathlib.Path.open", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            convert_to_hosts.write_output(output_file, source_data, 1, urls)

    # Original file untouched, no leftover temp file
    assert output_file.read_text() == "previous good content\n"
    assert list(tmp_path.glob(".*.tmp")) == []
