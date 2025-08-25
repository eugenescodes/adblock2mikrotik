# adblock2mikrotik

Convert ad-blocking filter lists to MikroTik RouterOS DNS adlist format.

## Overview

A conversion utility designed to transform popular ad-blocking filter lists (such as Hagezi) into a compact, memory-efficient format compatible with MikroTik RouterOS 7.15+ DNS adlist feature.

### Source Filter Lists

- Hagezi [Multi PRO mini](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#ledger-multi-pro-mini-recommended-for-browsermobile-adblockers-): [link to file on adblock format](https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.mini.txt)
- Hagezi [Threat Intelligence Feeds - Mini version](https://github.com/hagezi/dns-blocklists?tab=readme-ov-file#closed_lock_with_key-threat-intelligence-feeds---mini-version-): [link to file on adblock format](https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt)

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

## Use

How to implement DNS adblocking on MikroTik RouterOS 7.15+ using online blocklists. You must have active internet connextion and basic RouterOS configuration knowledge.
To add a URL-based adlist for DNS adblocking, use the following command in the router terminal:

```routeros
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=no
```

If you want to use properties -`ssl-verify=yes` you can download and import [CA certificates](https://curl.se/docs/caextract.html) use next commands:

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

Then run next command:

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

After that run next command:

```routeros
/ip/dns/adlist add url=https://raw.githubusercontent.com/eugenescodes/adblock2mikrotik/refs/heads/main/hosts.txt ssl-verify=yes
```

For a comprehensive guide on DNS adblocking and adlist configuration, refer to the official MikroTik documentation:

- [DNS Adlist - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/37748767/DNS#DNS-Adlist)
- [Certificates - MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/2555969/Certificates)

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Hagezi communities for maintaining comprehensive filter lists
- MikroTik for implementing DNS adlist feature in RouterOS 7.15

## Note

This tool is not affiliated with MikroTik
