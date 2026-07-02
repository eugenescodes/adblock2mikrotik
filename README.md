# adblock2mikrotik

Convert ad-blocking filter lists to MikroTik RouterOS DNS adlist format.

> [!TIP]
> Ready-to-use URL for RouterOS:
> `https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt`

## Overview

Transforms popular ad-blocking filter lists (Hagezi) into a compact format compatible with the MikroTik RouterOS 7.15+ DNS adlist feature.
Optimized for memory-constrained low-resource devices like the [RB951Ui-2nD hAP](https://mikrotik.com/product/RB951Ui-2nD) (which has 16 MB storage).

### Sources

| List | Description |
| --- | --- |
| [Hagezi Multi PRO mini](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#ledger-multi-pro-mini-recommended-for-browsermobile-adblockers-) | General ad/tracker blocking |
| [Hagezi TIF mini](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#closed_lock_with_key-threat-intelligence-feeds---mini-version-) | Threat intelligence feeds |

## Features

- Converts `||example.com^` rules to MikroTik DNS adlist format (`0.0.0.0 example.com`)
- Deduplicates entries across all sources
- Validates domains against RFC label rules (rejects double-dots, leading/trailing hyphens)
- Pre-filters comments and empty lines for efficiency
- Writes `hosts.txt` atomically — a failed or interrupted run never leaves a partial file in place
- Compatible with RouterOS 7.15+

## Usage

### Option 1 — uv (recommended)

```bash
# Install uv if not already installed (macOS / Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and run
git clone https://github.com/eugenescodes/adblock2mikrotik
cd adblock2mikrotik
uv run convert_to_hosts.py
```

`uv run` automatically creates a virtual environment and installs dependencies — no manual setup required.

### Option 2 — Docker

```bash
docker build -t adblock2mikrotik .

# Linux / macOS
docker run --rm --user $(id -u):$(id -g) -v "$(pwd)":/output adblock2mikrotik

# Windows (PowerShell)
docker run --rm -v "${PWD}:/output" adblock2mikrotik
```

> [!NOTE]
> The `-v` flag mounts your current directory into the container at `/output`.
> The script writes `hosts.txt` to `/output`, so the file appears directly
> in your current directory on the host — no manual copying needed.
>
> On Linux, `--user $(id -u):$(id -g)` ensures the output file is owned by
> your current user. Not required on macOS or Windows (Docker Desktop handles this automatically).

After running either option, `hosts.txt` is created in the current directory.

## MikroTik RouterOS Integration

### Add adlist via URL

```routeros
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=no
```

### Optional: enable SSL verification

If you want to use `ssl-verify=yes`, you can download and import [CA certificates](https://curl.se/docs/caextract.html) using the following commands:

```routeros
/tool fetch url=https://curl.se/ca/cacert.pem
/certificate import file-name=cacert.pem passphrase=""
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=yes
```

See also the official MikroTik documentation:

- [DNS Adlist - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/37748767/DNS#DNS-Adlist)
- [Certificates - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/2555969/Certificates)

## Configuration

By default, the script uses few pre-configured Hagezi filter lists (see [Sources](#sources) above). These defaults live in `config.toml.example` — the same file you copy to customize your own sources, so there's a single place to look. You can override them by creating a `config.toml` file:

### Customize sources

- Copy the example configuration:

```bash
cp config.toml.example config.toml
```

- Edit `config.toml` to add or remove sources:

```toml
[sources]
urls = [
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt",
]
```

- Run the converter:

```bash
uv run convert_to_hosts.py
```

The script loads sources from `config.toml` if it exists next to `convert_to_hosts.py` (or in your current working directory) and is valid. Otherwise it falls back to `config.toml.example`, bundled alongside the script. If neither file provides a usable source list, the script prints a warning and exits without writing `hosts.txt`.

> [!NOTE]
> `config.toml.example` must stay in the same directory as `convert_to_hosts.py` — it's the built-in fallback, not just documentation.

#### Using a custom `config.toml` with Docker

The image only bundles `config.toml.example`; your own `config.toml` isn't baked in (it's excluded via `.dockerignore`, same as it's gitignored). To use one, mount it into the container at `/app`, alongside the script:

```bash
docker run --rm --user $(id -u):$(id -g) \
  -v "$(pwd)":/output \
  -v "$(pwd)/config.toml":/app/config.toml:ro \
  adblock2mikrotik
```

### Finding additional filter lists

You can use any blocklist in AdBlock format (`||domain.com^` syntax)

For more Hagezi lists, visit the [Hagezi DNS blocklists repository](https://github.com/hagezi/dns-blocklists)

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management and [Ruff](https://docs.astral.sh/ruff/) for linting/formatting.

### Prerequisites

Install `uv` (replaces `pip` and `venv`) [more info about uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Setup (development environment)

Development tools (ruff, pytest) are in the `dev` dependency group and need to be installed separately:

```bash
uv sync  # Installs all dependencies including dev tools
```

### Lint and format

```bash
uv run ruff check . --fix   # lint + autofix
uv run ruff format .        # format
```

### Tests

```bash
uv run pytest -v
```

> [!NOTE]
> Development dependencies (ruff, pytest) are **not** included in the Docker image.
> Use `uv sync` locally to run linting, formatting, and tests.
> The Docker image only includes production dependencies for running the converter.

## Contributing

1. Open a [GitHub issue](https://github.com/eugenescodes/adblock2mikrotik/issues) to discuss major changes before starting work.
2. Fork the repo and create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes and run tests: `uv run pytest -v`
4. Commit with a clear message and push to your fork.
5. Open a Pull Request targeting `main` with a description of what and why.

## License

[GNU GPL v3.0](LICENSE)

## Acknowledgments

- [Hagezi](https://github.com/hagezi/dns-blocklists) for maintaining comprehensive filter lists
- MikroTik for the DNS adlist feature in RouterOS 7.15+

---

> This tool is not affiliated with MikroTik.
