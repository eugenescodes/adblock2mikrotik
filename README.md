# adblock2mikrotik

Convert AdBlock/AdGuard filter lists to MikroTik RouterOS DNS adlist format.

## Overview

A conversion utility designed to transform popular ad-blocking filter lists (AdGuard and Hagezi) into a compact, memory-efficient format compatible with MikroTik RouterOS 7.15+ DNS adlist feature. The primary goal is to create a minimal, optimized host file that addresses the limited memory constraints of low-resource devices like the ```hAP series``` (which has 16 MB storage but less than 3 MB free after upgrading to RouterOS 7), for example the [RB951Ui-2nD hAP](https://mikrotik.com/product/RB951Ui-2nD) router

## Features

- Converts AdBlock/AdGuard syntax to MikroTik DNS adlist format
- Removes duplicates and optimizes storage space
- Supports multiple input filter list formats
- Compatible with RouterOS 7.15 and newer
- Preserves only domain-based rules
- Removes comments and unnecessary elements

Supports common AdBlock/AdGuard filter rules, including:

- Domain rules (`||example.com^`)
- Basic URL rules
- Comment lines (automatically removed)

Generates a clean list of domains in MikroTik DNS adlist format:

```text
0.0.0.0 example.com
0.0.0.0 ads.example.net
0.0.0.0 tracking.example.org
```

## Use

How to implement DNS adblocking on MikroTik RouterOS 7.15+ using online blocklists. You must have active internet connextion and basic RouterOS configuration knowledge.
To add a URL-based adlist for DNS adblocking, use the following command in the router terminal:

```routeros
/ip/dns/adlist add url=https://github.com/eugenescodes/adblock2mikrotik/main/hosts.txt ssl-verify=no
```

For a comprehensive guide on DNS adblocking and adlist configuration, refer to the official MikroTik documentation - [DNS Adlist - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/37748767/DNS#DNS-Adlist)

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- AdGuard and Hagezi communities for maintaining comprehensive filter lists
- MikroTik for implementing DNS adlist feature in RouterOS 7.15

## Note

This tool is not affiliated with MikroTik or AdGuard.
