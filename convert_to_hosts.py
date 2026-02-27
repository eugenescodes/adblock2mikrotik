import re
from datetime import UTC, datetime

import requests


def fetch_rules(url):
    try:
        response = requests.get(
            url, timeout=(3, 10)
        )  # timeout=(connect_timeout, read_timeout)
        response.raise_for_status()  # Check for HTTP errors
        return response.text.splitlines()
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return []


def convert_rule(rule):
    # Remove comments and whitespace
    rule = re.sub(r"#.*$", "", rule).strip()

    if not rule:
        return None

    # Handle different rule formats
    if rule.startswith("||") and "^" in rule:
        # Extract domain from common ad-blocking rule style (e.g., ||domain^)
        domain = rule[2:].split("^")[0]
        # Remove any additional modifiers after ^
        domain = domain.split("$")[0]
        # Basic domain validation
        if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", domain):
            return f"0.0.0.0 {domain}"
    return None


def main():
    urls = [
        # "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/light.txt",
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt",
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt",
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.mini.txt",
        # "https://..."
    ]

    # Use a set to track unique rules
    unique_rules = set()

    source_data = {}  # { url: [converted_rules] }

    print(f"Starting conversion of {len(urls)} source(s)...\n")

    for url in urls:
        print(f"Fetching: {url}")
        rules = fetch_rules(url)
        print(f"Fetched {len(rules):,} lines")

        converted = []
        for rule in rules:
            result = convert_rule(rule)
            if result and result not in unique_rules:
                unique_rules.add(result)
                converted.append(result)
        source_data[url] = converted
        print(f"Converted {len(converted):,} unique domains\n")

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

    output_file = "hosts.txt"
    with open(output_file, "w", encoding="utf-8") as file:
        file.write(header)

        for url, rules in source_data.items():
            file.write(f"\n# Source: {url}\n\n")
            for rule in rules:
                file.write(rule + "\n")
            file.write(f"\n# Converted {len(rules):,} rules from this source\n\n")

        # Write total count at the end
        file.write(f"\n# Total unique domains: {len(unique_rules)}\n")

    print(f"Done! Written to: {output_file}")


if __name__ == "__main__":
    main()
