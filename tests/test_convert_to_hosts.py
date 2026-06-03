import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import convert_to_hosts


@pytest.mark.parametrize(
    "rule, expected",
    [
        ("||example.com^", "0.0.0.0 example.com"),
        ("||example.com^$third-party", "0.0.0.0 example.com"),
        ("||example.com^  # comment", "0.0.0.0 example.com"),
    ],
)
def test_convert_rule_valid(rule, expected):
    """Test conversion of valid AdBlock rules to hosts format."""
    assert convert_to_hosts.convert_rule(rule) == expected


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
def test_convert_rule_invalid(rule):
    """Test that invalid/unsupported AdBlock rules return None."""
    assert convert_to_hosts.convert_rule(rule) is None


@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_success(mock_session_cls):
    """Test successful fetch of rules from URL on first attempt."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = ["line1", "line2", "line3"]
    mock_response.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.get.return_value.__enter__.return_value = mock_response
    mock_session.get.return_value.__exit__ = MagicMock(return_value=None)
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")
    assert result == ["line1", "line2", "line3"]
    assert isinstance(elapsed, float)
    mock_session.get.assert_called_once_with(
        "http://fakeurl", timeout=(3, 10), stream=True
    )


@patch("convert_to_hosts.time.sleep")
@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_retries_then_fails(mock_session_cls, mock_sleep, capsys):
    """Test fetch retry logic: 3 attempts with exponential backoff, then failure."""
    mock_session = MagicMock()
    mock_session.get.side_effect = requests.RequestException("Mocked network error")
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == []
    assert isinstance(elapsed, float)
    assert mock_session.get.call_count == 3

    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2)
    mock_sleep.assert_any_call(4)

    captured = capsys.readouterr()
    assert "Attempt 1 failed" in captured.out
    assert "Attempt 2 failed" in captured.out
    assert "Error fetching http://fakeurl after 3 attempts" in captured.out


@patch("convert_to_hosts.time.sleep")
@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_succeeds_on_retry(mock_session_cls, mock_sleep):
    """Test successful fetch after first attempt fails (retry succeeds)."""
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = ["a", "b"]
    mock_response.raise_for_status = MagicMock()

    mock_session = MagicMock()
    mock_session.get.side_effect = [
        requests.RequestException("Temporary error"),
        MagicMock(
            __enter__=lambda self: mock_response, __exit__=MagicMock(return_value=None)
        ),
    ]
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == ["a", "b"]
    assert isinstance(elapsed, float)
    assert mock_session.get.call_count == 2
    assert mock_sleep.call_count == 1


@patch("convert_to_hosts.requests.Session")
def test_fetch_rules_filters_comments(mock_session_cls):
    """Test that fetch_rules pre-filters empty lines and comment-only lines."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = [
        "||example.com^",
        "# Title: some blocklist header",
        "",
        "||test.com^",
        "  # indented comment",
    ]
    mock_session = MagicMock()
    mock_session.get.return_value.__enter__.return_value = mock_response
    mock_session.get.return_value.__exit__ = MagicMock(return_value=None)
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == ["||example.com^", "||test.com^"]
    assert len(result) == 2
    assert isinstance(elapsed, float)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_file_not_found(capsys):
    """Falls back to SOURCES when config file does not exist."""
    result = convert_to_hosts.load_config("nonexistent_config.toml")

    assert result == convert_to_hosts.SOURCES
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_load_config_reads_urls(tmp_path):
    """Reads URL list from a valid config.toml."""
    config = tmp_path / "config.toml"
    config.write_text(
        '[sources]\nurls = ["https://example.com/list1.txt", "https://example.com/list2.txt"]\n'
    )

    result = convert_to_hosts.load_config(config)

    assert result == ["https://example.com/list1.txt", "https://example.com/list2.txt"]


def test_load_config_missing_urls_key_falls_back_to_defaults(tmp_path):
    """Falls back to SOURCES when [sources] section exists but 'urls' key is absent."""
    config = tmp_path / "config.toml"
    config.write_text("[sources]\n# no urls key\n")

    result = convert_to_hosts.load_config(config)

    assert result == convert_to_hosts.SOURCES


def test_load_config_invalid_toml_falls_back_to_defaults(tmp_path, capsys):
    """Falls back to SOURCES and prints an error for malformed TOML."""
    config = tmp_path / "config.toml"
    config.write_text("this is not valid toml ][[\n")

    result = convert_to_hosts.load_config(config)

    assert result == convert_to_hosts.SOURCES
    captured = capsys.readouterr()
    assert "Error loading" in captured.out


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
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main(mock_file, mock_fetch_rules):
    """Test main orchestration: fetch, convert, deduplicate, and write to file."""
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

    # Verify that the file was opened for writing via pathlib
    mock_file.assert_called_once_with("w", encoding="utf-8")

    # fetch_rules must be called once per source URL
    assert mock_fetch_rules.call_count == len(convert_to_hosts.SOURCES)

    handle = mock_file()
    written_text = "".join(call.args[0] for call in handle.write.call_args_list)

    assert "Title:" in written_text
    assert "0.0.0.0 example.com" in written_text
    assert written_text.count("0.0.0.0 example.com") == 1  # deduplicated globally
    assert "0.0.0.0 test.com" in written_text
    assert "# Converted 2 rules from this source" in written_text
    # First source converts 2 rules; all subsequent sources return duplicates -> 0 unique each
    assert written_text.count("# Converted 0 rules from this source") == len(convert_to_hosts.SOURCES) - 1


@patch("convert_to_hosts.fetch_rules")
@patch("pathlib.Path.open", new_callable=mock_open)
def test_main_empty_rules_skips_file_write(mock_file, mock_fetch_rules, capsys):
    """Test that main() skips writing to file when no valid rules are converted."""
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


def test_write_output_direct(tmp_path):
    """Direct unit test for write_output — verifies structure without going through main()."""
    output_file = tmp_path / "hosts.txt"
    urls = ["https://example.com/list.txt"]
    source_data = {urls[0]: ["0.0.0.0 example.com", "0.0.0.0 test.com"]}

    convert_to_hosts.write_output(output_file, source_data, 2, urls)

    content = output_file.read_text()

    # Header
    assert "Title:" in content
    assert "Last modified:" in content
    assert "Total unique domains: 2" in content
    assert "# - https://example.com/list.txt" in content

    # Section body
    assert "# Source: https://example.com/list.txt" in content
    assert "0.0.0.0 example.com" in content
    assert "0.0.0.0 test.com" in content
    assert "# Converted 2 rules from this source" in content

    # Footer
    assert content.strip().endswith("Total unique domains: 2")
