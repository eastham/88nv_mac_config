# 88NV ATC Mac Mini Configuration

Edit this file before running `setup.py`. Each machine gets its own static IP
and hostname — update the Network section for each machine. The Hosts, Bookmarks,
and Repo sections are typically the same across all machines.

CLI arguments to `setup.py` override values in this file, so you can also pass
`--ip`, `--hostname`, etc. directly without editing here.

---

## Network

| Key          | Value         |
|--------------|---------------|
| SUBNET       | 255.255.252.0 |
| GATEWAY      | 10.4.0.1      |
| DNS          | 10.4.0.1      |
| HOSTNAME     | atc-mac1      |
| DISABLE_WIFI | false         |

---

## Repo

| Key      | Value                                    |
|----------|------------------------------------------|
| REPO_URL | https://github.com/eastham/adsb_actions |
| REPO_DIR | ~/git-mac/adsb_actions                     |

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

