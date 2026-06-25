# 88NV ATC Mac Mini Configuration

Edit this file before running `setup.py`. Each machine gets its own hostname
and optionally a static IP — update the Network section for each machine.
The Hosts, Bookmarks, and Repo sections are typically the same across all machines.

**Network interface selection:** setup.py prefers Ethernet if present, otherwise
falls back to WiFi (which must already be associated before running setup.py).

---

## Network -- details for THIS machine.  Replace FILL_ME_IN on HOSTNAME and IP (see below)

| Key          | Value         |
|--------------|---------------|
| HOSTNAME     | FILL_ME_IN    |
| IP           | FILL_ME_IN    |
| SUBNET       | 255.255.252.0 |
| GATEWAY      | 10.4.0.1      |
| DNS          | 10.4.0.1      |

`IP`: Set to a static IP address (e.g. `10.4.0.22`) for critical machines that
need a fixed address. Set to `dhcp` for machines that can use dynamic addressing.
If static, SUBNET, GATEWAY, and DNS must also be set.

---

## Hosts

These entries are written to `/etc/hosts` on each machine, enabling local
hostname resolution even when upstream DNS is unavailable.


| IP        | Hostname  |
|-----------|-----------|
| 10.4.0.1  | gateway   |
| 10.4.0.10 | boxone    |
| 10.4.0.11 | boxtwo    |
| 10.4.0.20 | monitor   |
| 10.4.0.21 | scenictent |
| 10.4.0.22 | obsdeck  |
| 10.4.0.23 | webcam  |

To get plaintext /etc/hosts, use setup.py --emit-etchosts, or use this (may be out of date)

```
10.4.0.1        gateway
10.4.0.10       boxone
10.4.0.11       boxtwo
10.4.0.20       monitor
10.4.0.21       scenictent
10.4.0.22       obsdeck
10.4.0.23       webcam
```

---

## Repo

| Key      | Value                                    |
|----------|------------------------------------------|
| REPO_URL | https://github.com/eastham/adsb_actions |
| REPO_DIR | ~/git-mac/adsb_actions                     |

---

## Autostart -- apps to launch at login. Replace FILL_ME_IN in LAUNCH_ARGS.

| Key                    | Value                                                                  |
|------------------------|------------------------------------------------------------------------|
| OPEN_CHROME_BOOKMARK_1 | ADS-B map                                                              |
| OPEN_CHROME_BOOKMARK_2 | PTS                                                                    |
| LAUNCH_SCRIPT          | src/controller.py                                                      |
| LAUNCH_ARGS            | -- --rules ui.yaml --ipaddr boxone --port 30006 tests/regions.kml |
| STARTUP_DELAY          | 10                                                                     |

---

## Bookmarks

These URLs are added to Safari's bookmarks bar. Labels should be short and
descriptive — ATC volunteers use these to quickly reach operational resources.

| URL                      | Label         |
|--------------------------|---------------|
| http://boxone/tar1090    | ADS-B map  |
| https://www.appsheet.com/start/0886479f-2675-4bce-8b5e-cb3d60d7b31c   | PTS   |
| https://docs.google.com/document/d/1dOE_1WoR1acItrbT1CSZHPqc-PcSgqOkt4oUqlUPvIg/edit?tab=t.0#heading=h.jylnajbc9cbi | Crewmember Guide |
| https://docs.google.com/document/d/1C2O1AeeZSba5TJfLiJlfpmqamLAOirWHizOJGSLeUN4/edit?tab=t.0#heading=h.4cqmcn4ssgzd   | 88NV Help page - resources   |
| https://docs.google.com/document/d/1uvCXIm6yDlk7wH4fBz1JjfJ6qabCRsEWE96xpaSQdEk/edit?tab=t.0         | Emergency Manual  |
| https://docs.google.com/document/d/1zrkDD2gY5OURTFcHfdGXJEyyE5pMtX7mIuOa4hVm8E0/edit?tab=t.0#heading=h.89pykzcxl76r | Irregular Operations Playbook |

