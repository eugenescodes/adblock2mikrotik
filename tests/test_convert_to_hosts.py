import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import convert_to_hosts


def test_convert_rule_valid():
    """Test conversion of valid AdBlock rules to hosts format."""
    # Valid rule with || and ^
    assert convert_to_hosts.convert_rule("||example.com^") == "0.0.0.0 example.com"
    # Valid rule with modifiers after ^
    assert (
        convert_to_hosts.convert_rule("||example.com^$third-party")
        == "0.0.0.0 example.com"
    )
    # Rule with comment and whitespace
    assert (
        convert_to_hosts.convert_rule("||example.com^  # comment")
        == "0.0.0.0 example.com"
    )


def test_convert_rule_invalid():
    """Test that invalid/unsupported AdBlock rules return None."""
    # Empty rule
    assert convert_to_hosts.convert_rule("") is None
    # Comment only
    assert convert_to_hosts.convert_rule("# some comment") is None
    # Rule not starting with ||
    assert convert_to_hosts.convert_rule("|example.com^") is None
    # Invalid domain format (no TLD)
    assert convert_to_hosts.convert_rule("||invalid_domain^") is None
    # Domain with double dot (regex fix for bug #1)
    assert convert_to_hosts.convert_rule("||example..com^") is None
    # Domain with leading dot (regex fix for bug #1)
    assert convert_to_hosts.convert_rule("||.example.com^") is None
    # Domain with trailing dot (regex fix for bug #1)
    assert convert_to_hosts.convert_rule("||example.com.^") is None


@patch("convert_to_hosts.requests.get")
def test_fetch_rules_success(mock_get):
    """Test successful fetch of rules from URL on first attempt."""
    # Create a mock response that supports the context manager protocol
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = ["line1", "line2", "line3"]
    mock_response.raise_for_status = MagicMock()

    # Set up the mock to return itself when entering the context manager
    mock_get.return_value.__enter__.return_value = mock_response
    mock_get.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")
    assert result == ["line1", "line2", "line3"]
    assert isinstance(elapsed, float)
    mock_get.assert_called_once_with("http://fakeurl", timeout=(3, 10), stream=True)


@patch("convert_to_hosts.time.sleep")  # don't need wait really time in tests
@patch("convert_to_hosts.requests.get")
def test_fetch_rules_retries_then_fails(mock_get, mock_sleep, capsys):
    """Test fetch retry logic: 3 attempts with exponential backoff, then failure."""
    mock_get.side_effect = requests.RequestException("Mocked network error")

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == []
    assert isinstance(elapsed, float)
    assert mock_get.call_count == 3  # call 3 times due to retries

    # sleep calls: after 1st and 2nd attempts, but not after 3rd
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(2)  # backoff after 1st attempt
    mock_sleep.assert_any_call(4)  # backoff after 2nd attempt

    captured = capsys.readouterr()
    assert "Attempt 1 failed" in captured.out
    assert "Attempt 2 failed" in captured.out
    assert "Error fetching http://fakeurl after 3 attempts" in captured.out


@patch("convert_to_hosts.time.sleep")
@patch("convert_to_hosts.requests.get")
def test_fetch_rules_succeeds_on_retry(mock_get, mock_sleep):
    """Test successful fetch after first attempt fails (retry succeeds)."""
    # Create a proper mock response object
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = ["a", "b"]
    mock_response.raise_for_status = MagicMock()

    # Set up the mock to return itself when entering the context manager
    mock_get.side_effect = [
        requests.RequestException("Temporary error"),
        MagicMock(
            __enter__=lambda self: mock_response, __exit__=MagicMock(return_value=None)
        ),
    ]

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == ["a", "b"]
    assert isinstance(elapsed, float)
    assert mock_get.call_count == 2
    assert mock_sleep.call_count == 1


@patch("convert_to_hosts.requests.get")
def test_fetch_rules_filters_comments(mock_get):
    """Test that fetch_rules pre-filters empty lines and comment-only lines.

    Verifies (bug fix #3): comments and empty strings are removed in fetch_rules
    itself, before reaching convert_rule, so callers receive only candidate rules.
    """
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = [
        "||example.com^",
        "# Title: some blocklist header",
        "",
        "||test.com^",
        "  # indented comment",  # leading whitespace before #
    ]
    mock_get.return_value.__enter__.return_value = mock_response
    mock_get.return_value.__exit__ = MagicMock(return_value=None)

    result, elapsed = convert_to_hosts.fetch_rules("http://fakeurl")

    assert result == ["||example.com^", "||test.com^"]
    assert len(result) == 2
    assert isinstance(elapsed, float)


@patch("convert_to_hosts.fetch_rules")
@patch("builtins.open", new_callable=mock_open)
def test_main(mock_file, mock_fetch_rules):
    """Test main orchestration: fetch, convert, deduplicate, and write to file.

    Verifies:
        - File is opened in write mode with UTF-8 encoding
        - Header with metadata is written
        - Rules are converted and deduplicated (duplicates removed)
        - Output is written with section comments
    """
    # Mock fetch_rules to return sample rules and elapsed time
    mock_fetch_rules.return_value = (
        [
            "||example.com^",
            "||example.com^",  # Duplicate
            "||test.com^$third-party",
            "# comment",
            "",
        ],
        0.5,  # elapsed time
    )

    convert_to_hosts.main()

    # Check that file was opened for writing
    mock_file.assert_called_once_with("hosts.txt", "w", encoding="utf-8")

    # Get the file handle to check write calls
    handle = mock_file()

    # Check that header was written
    header_written = any(
        "Title:" in call.args[0] for call in handle.write.call_args_list
    )
    assert header_written

    # Check that converted rules were written
    written_text = "".join(call.args[0] for call in handle.write.call_args_list)
    assert "0.0.0.0 example.com" in written_text
    assert written_text.count("0.0.0.0 example.com") == 1
    assert "0.0.0.0 test.com" in written_text
    assert "# Converted 2 rules from this source" in written_text


@patch("convert_to_hosts.fetch_rules")
@patch("builtins.open", new_callable=mock_open)
def test_main_empty_rules_skips_file_write(mock_file, mock_fetch_rules, capsys):
    """Test that main() skips writing to file when no valid rules are converted.

    Verifies (bug fix #2):
        - File is NOT opened if unique_rules set is empty
        - Warning message is printed
        - Function returns early without writing file
    """
    # Mock fetch_rules to return rules that won't convert to valid domains
    mock_fetch_rules.return_value = (
        [
            "# comment only",
            "",
            "invalid_rule_without_pipes",
            "|single_pipe^",
        ],
        0.5,  # elapsed time
    )

    convert_to_hosts.main()

    # File should NOT be opened at all
    mock_file.assert_not_called()

    # Check that warning message was printed
    captured = capsys.readouterr()
    assert "Warning: No valid rules were converted" in captured.out
    assert "Skipping writing to file" in captured.out
