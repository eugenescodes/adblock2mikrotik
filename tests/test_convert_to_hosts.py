import pytest
from unittest.mock import patch, mock_open
import builtins
import convert_to_hosts


def test_convert_rule_valid():
    # Valid rule with || and ^
    assert convert_to_hosts.convert_rule("||example.com^") == "0.0.0.0 example.com"
    # Valid rule with modifiers after ^
    assert convert_to_hosts.convert_rule("||example.com^$third-party") == "0.0.0.0 example.com"
    # Rule with comment and whitespace
    assert convert_to_hosts.convert_rule("||example.com^  # comment") == "0.0.0.0 example.com"


def test_convert_rule_invalid():
    # Empty rule
    assert convert_to_hosts.convert_rule("") is None
    # Comment only
    assert convert_to_hosts.convert_rule("# some comment") is None
    # Rule not starting with ||
    assert convert_to_hosts.convert_rule("|example.com^") is None
    # Invalid domain format
    assert convert_to_hosts.convert_rule("||invalid_domain^") is None


@patch("convert_to_hosts.requests.get")
def test_fetch_rules_success(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.text = "line1\nline2\nline3"
    result = convert_to_hosts.fetch_rules("http://fakeurl")
    assert result == ["line1", "line2", "line3"]
    mock_get.assert_called_once_with("http://fakeurl", timeout=(3, 10))


@patch("convert_to_hosts.fetch_rules")
@patch("builtins.open", new_callable=mock_open)
def test_main(mock_file, mock_fetch_rules):
    # Mock fetch_rules to return sample rules
    mock_fetch_rules.return_value = [
        "||example.com^",
        "||test.com^$third-party",
        "# comment",
        "",
    ]

    convert_to_hosts.main()

    # Check that file was opened for writing
    mock_file.assert_called_once_with("hosts.txt", "w", encoding="utf-8")

    # Get the file handle to check write calls
    handle = mock_file()

    # Check that header was written
    header_written = any("Title:" in call.args[0] for call in handle.write.call_args_list)
    assert header_written

    # Check that converted rules were written
    written_text = "".join(call.args[0] for call in handle.write.call_args_list)
    assert "0.0.0.0 example.com" in written_text
    assert "0.0.0.0 test.com" in written_text
    assert "# Converted 2 rules from this source" in written_text
