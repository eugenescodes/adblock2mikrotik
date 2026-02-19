# adblock2mikrotik

> [!TIP]
> URL to add in RouterOS: <https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt>

Convert ad-blocking filter lists to MikroTik RouterOS DNS adlist format.

## Overview

A conversion utility designed to transform popular ad-blocking filter lists (such as Hagezi) into a compact, memory-efficient format compatible with MikroTik RouterOS 7.15+ DNS adlist feature.

### Source Filter Lists

- Hagezi [Multi PRO mini](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#ledger-multi-pro-mini-recommended-for-browsermobile-adblockers-): --> [link to file on adblock format](https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt)
- Hagezi [Threat Intelligence Feeds - Mini version](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#closed_lock_with_key-threat-intelligence-feeds---mini-version-): --> [link to file on adblock format](https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt)
- Hagezi [Gambling - Mini version](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#slot_machine-gambling---mini-version-): --> [link to file on adblock format](https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.mini.txt)

The primary goal is to create a minimal, optimized host file that addresses the limited memory constraints of low-resource devices like the ```hAP series``` (which has 16 MB storage but less than 3 MB free after upgrading to RouterOS 7), for example the [RB951Ui-2nD hAP](https://mikrotik.com/product/RB951Ui-2nD) router

## Features

- Converts ad-blocking filter list syntax to MikroTik DNS adlist format
- Removes duplicates and optimizes storage space
- Supports multiple input filter list formats
- Compatible with RouterOS 7.15 and newer
- Preserves only domain-based rules
- Removes comments and unnecessary elements

Supports common ad-blocking filter rules, including:

- Domain rules (`||example.com^`)
- Basic URL rules
- Comment lines (automatically removed)

Generates a clean list of domains in MikroTik DNS adlist format:

```text
0.0.0.0 example.com
0.0.0.0 ads.example.net
0.0.0.0 tracking.example.org
```

## Usage

This tool converts popular ad-blocking filter lists into MikroTik RouterOS DNS adlist format. It is designed for use with MikroTik RouterOS 7.15+.

### Running the tool

You can run the Python script directly:

```bash
python convert_to_hosts.py
```

Or using Docker:

```bash
docker build -t convert_to_hosts .
docker run --rm -v $(pwd):/app -e PYTHONPATH=/app --entrypoint python convert_to_hosts convert_to_hosts.py
```

### Implementing DNS adblocking on MikroTik RouterOS

You must have an active internet connection and basic RouterOS configuration knowledge.

To add a URL-based adlist for DNS adblocking, use the following command in the router terminal:

```routeros
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=no
```

If you want to use `ssl-verify=yes`, you can download and import [CA certificates](https://curl.se/docs/caextract.html) using the following commands:

```routeros
/tool fetch url=https://curl.se/ca/cacert.pem
```

The resulting output should be:

```routeros
      status: finished
  downloaded: 225KiB  
       total: 225KiB  
    duration: 1s 
```

Then run the next command:

```routeros
/certificate import file-name=cacert.pem passphrase=""
```

Output should be:

```routeros
certificates-imported: 149
     private-keys-imported:   0
            files-imported:   0
       decryption-failures:   0
  keys-with-no-certificate:   0
```

After that, run the following command:

```routeros
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=yes
```

### Additional Resources

For a comprehensive guide on DNS adblocking and adlist configuration, refer to the official MikroTik documentation:

- [DNS Adlist - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/37748767/DNS#DNS-Adlist)
- [Certificates - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/2555969/Certificates)

## Development

This project uses [uv](https://docs.astral.sh/uv/) for package management and [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

### Prerequisites

Install `uv` (replaces `pip` and `venv`) [more info about uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Setup

```bash
# Create virtual environment and install dependencies
uv venv
uv pip install -r requirements.txt

# Install Ruff globally via uv
uv tool install ruff
```

### Linting and formatting

Ruff replaces both `flake8` (linter) and `black` (formatter) in a single tool [more info about ruff](https://docs.astral.sh/ruff/installation/):

```bash
# Check for lint errors
ruff check .

# Fix lint errors automatically
ruff check . --fix

# Format code
ruff format .

# Check formatting without applying changes
ruff format --check .
```

### Running tests

```bash
# Run tests directly
uv run pytest tests/

# Or inside Docker
docker build -t convert_to_hosts .
docker run --rm -v $(pwd):/app -e PYTHONPATH=/app --entrypoint pytest convert_to_hosts -v tests/test_convert_to_hosts.py
```

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Hagezi communities for maintaining comprehensive filter lists
- MikroTik for implementing DNS adlist feature in RouterOS 7.15

## Note

This tool is not affiliated with MikroTik
