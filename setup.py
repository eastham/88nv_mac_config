#!/usr/bin/env python3
"""
88NV ATC Mac Mini Setup Script
Reads config.md, applies network/hostname config, installs software.

Usage:
    python3 setup.py [options]

Options:
    --config PATH         Path to config.md (default: same dir as this script)
    --skip-install        Skip software install steps (network/hostname only)
    --no-vnc              Skip enabling macOS Screen Sharing (VNC)
    --dry-run             Print commands without executing them
    --emit-etchosts       Print the /etc/hosts content that would be written, then exit
"""

import argparse
import datetime
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
try:
    from markdown_it import MarkdownIt
except ImportError:
    print("ERROR: markdown-it-py is not installed. Run bootstrap.sh first.")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "config.md")

# Seconds to let Chrome's profile singleton initialize after the process
# appears, before handing it additional URLs. Too short and the handoffs are
# dropped at cold boot and only the first window opens.
CHROME_SETTLE_SECS = 5
# Seconds between successive URL handoffs to the running Chrome instance.
CHROME_HANDOFF_SECS = 3

# Seconds to wait for `launchctl bootout` to finish removing the service before
# bootstrapping it again. launchd allows a job 5s to honor SIGTERM before it
# SIGKILLs, so this must exceed that.
LAUNCHCTL_BOOTOUT_TIMEOUT = 15

# ── logging ──────────────────────────────────────────────────────────────────

log_path = None
log_file = None

def open_log():
    global log_path, log_file
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.expanduser(f"~/88nv_setup_{ts}.log")
    log_file = open(log_path, "w", buffering=1)
    _print(f"Log file: {log_path}")

def _print(*args, **kwargs):
    text = " ".join(str(a) for a in args)
    print(text, **kwargs)
    if log_file:
        print(text, file=log_file)

def step_banner(label, ok):
    icon = "✅" if ok else "❌"
    _print(f"\n{icon}  {label}: {'SUCCESS' if ok else 'FAILURE'}")

# ── config parsing ────────────────────────────────────────────────────────────

def parse_config(path):
    """Parse config.md. Returns dict of dicts, keyed by the first word of each
    section heading."""
    with open(path) as f:
        text = f.read()

    md = MarkdownIt()
    tokens = md.parse(text)

    config = {}
    current_section = None
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            content_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            if content_tok and content_tok.type == "inline":
                current_section = content_tok.content.split()[0].strip()
                config[current_section] = {}
        elif tok.type == "inline" and current_section:
            first_data_row = True
            for line in tok.content.splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                # Tables may have extra trailing columns (e.g. Hosts has a MAC
                # column); only the first two are used as key/value.
                if len(cells) < 2 or not cells[0]:
                    continue
                if re.match(r'^[-:| ]+$', cells[0]):  # separator row
                    continue
                if first_data_row:  # header row
                    first_data_row = False
                    continue
                config[current_section][cells[0]] = cells[1]

    return config

# ── command runner ────────────────────────────────────────────────────────────

dry_run = False

def run(cmd, *, check=True, capture=False, sudo=False):
    """Run a shell command with logging."""
    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + (cmd if isinstance(cmd, list) else cmd.split())
    if isinstance(cmd, str):
        cmd = cmd.split()
    if dry_run and not capture:
        _print(f"  + {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    _print(f"  + {' '.join(cmd)}")
    kwargs = {"check": check}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    else:
        kwargs["stdout"] = sys.stdout
        kwargs["stderr"] = sys.stderr
    return subprocess.run(cmd, **kwargs)

# ── steps ─────────────────────────────────────────────────────────────────────

def _list_services():
    """Return [(service_name, device)] from networksetup, in service order.

    Only services listed here can be passed to networksetup -setmanual/-setdhcp;
    -listallhardwareports also reports ports with no service (e.g. unconfigured
    Thunderbolt), which networksetup rejects as "not a recognized network service".
    """
    result = run(["networksetup", "-listnetworkserviceorder"], capture=True)
    services = []
    name = None
    for line in result.stdout.splitlines():
        m = re.match(r"\(\d+\)\s+(.*\S)", line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r"Device:\s*([^)\s]*)", line)
        if m and name:
            dev = m.group(1)
            if dev:
                services.append((name, dev))
            name = None
    return services


def _detect_interfaces():
    """Return (eth_service, wifi_service, wifi_device). Any may be None.

    Classification comes from -listallhardwareports (which names the port type),
    but the returned ethernet/wifi values are *service* names from
    -listnetworkserviceorder, since -setmanual/-setdhcp only accept those and
    reject BSD device names like en0. wifi_device is the BSD name, which is what
    -setairportpower requires instead.
    """
    if dry_run:
        return "Ethernet", "Wi-Fi", "en0"

    # device -> service name, for the devices that actually have a service
    dev_to_service = {dev: name for name, dev in _list_services()}

    result = run(["networksetup", "-listallhardwareports"], capture=True)
    lines = result.stdout.splitlines()
    eth_dev = wifi_dev = None
    for idx, line in enumerate(lines):
        if not line.startswith("Hardware Port:"):
            continue
        dev = None
        for sub in lines[idx:idx+5]:
            m = re.search(r"Device:\s+(\S+)", sub)
            if m:
                dev = m.group(1)
                break
        if not dev or dev not in dev_to_service:
            # No configurable service for this port; networksetup would reject it.
            continue
        if dev.startswith("bridge") or "Bridge" in line:
            # Thunderbolt Bridge matches "Thunderbolt" below but is not a NIC.
            continue
        if eth_dev is None and ("Ethernet" in line or "USB 10/100" in line
                                or "Thunderbolt" in line):
            eth_dev = dev
        elif wifi_dev is None and ("Wi-Fi" in line or "AirPort" in line):
            wifi_dev = dev

    eth = dev_to_service.get(eth_dev) if eth_dev else None
    wifi = dev_to_service.get(wifi_dev) if wifi_dev else None
    return eth, wifi, wifi_dev


def _configure_iface(iface, ip, net):
    """Apply static IP or DHCP to iface. Returns True on success."""
    if ip.lower() == "dhcp":
        run(["networksetup", "-setdhcp", iface], sudo=True)
        _print(f"  {iface}: DHCP")
    else:
        subnet  = net.get("SUBNET")
        gateway = net.get("GATEWAY")
        dns     = net.get("DNS")
        if not all([subnet, gateway, dns]):
            _print("  ERROR: Static IP requires SUBNET, GATEWAY, and DNS in config.md.")
            return False
        run(["networksetup", "-setmanual", iface, ip, subnet, gateway], sudo=True)
        run(["networksetup", "-setdnsservers", iface, dns], sudo=True)
        _print(f"  {iface}: static {ip}/{subnet} gw {gateway} dns {dns}")
    return True


def step_network(net, ip):
    _print("\n════════════════════════════════════════")
    _print("  [network] Configuring network")
    _print("════════════════════════════════════════")
    ok = True
    try:
        eth, wifi, wifi_dev = _detect_interfaces()
        _print(f"  Detected — ethernet: {eth or 'none'}, wifi: {wifi or 'none'}")

        if eth:
            _print(f"  Using Ethernet ({eth})")
            ok = _configure_iface(eth, ip, net)
            if not ok and wifi:
                _print(f"  WARNING: Ethernet config failed — falling back to WiFi ({wifi})")
                ok = _configure_iface(wifi, ip, net)
            elif ok and wifi:
                run(["networksetup", "-setairportpower", wifi_dev, "off"], sudo=True)
                _print(f"  WiFi ({wifi}) disabled")
        elif wifi:
            _print(f"  No Ethernet found — using WiFi ({wifi})")
            ok = _configure_iface(wifi, ip, net)
        else:
            _print("  ERROR: No Ethernet or WiFi interface detected.")
            ok = False

    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("network", ok)
    return ok


def step_hostname(hostname):
    _print("\n════════════════════════════════════════")
    _print("  [hostname] Setting hostname")
    _print("════════════════════════════════════════")
    ok = True
    try:
        run(["scutil", "--set", "HostName", hostname], sudo=True)
        run(["scutil", "--set", "LocalHostName", hostname], sudo=True)
        run(["scutil", "--set", "ComputerName", hostname], sudo=True)
        _print(f"  Hostname set to: {hostname}")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("hostname", ok)
    return ok


def build_hosts_block(hosts_entries):
    """Return the /etc/hosts content with the 88NV block appended (old block replaced)."""
    marker_start = "# 88NV BEGIN"
    marker_end = "# 88NV END"

    with open("/etc/hosts") as f:
        existing = f.read()

    existing = re.sub(
        rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
        "", existing, flags=re.DOTALL
    )

    block_lines = [marker_start]
    for ip, hostname in hosts_entries:
        block_lines.append(f"{ip:<16} {hostname}")
    block_lines.append(marker_end)
    block = "\n".join(block_lines) + "\n"

    return existing.rstrip("\n") + "\n\n" + block


def step_hosts(hosts_entries):
    _print("\n════════════════════════════════════════")
    _print("  [hosts] Writing /etc/hosts entries")
    _print("════════════════════════════════════════")
    ok = True
    try:
        new_content = build_hosts_block(hosts_entries)
        _print(f"  Writing {len(hosts_entries)} entries to /etc/hosts")
        for ip, hostname in hosts_entries:
            _print(f"    {ip}  {hostname}")

        if not dry_run:
            with tempfile.NamedTemporaryFile("w", suffix=".hosts", delete=False) as tmp:
                tmp.write(new_content)
                tmp_path = tmp.name
            run(["cp", tmp_path, "/etc/hosts"], sudo=True)
            os.unlink(tmp_path)
            _print("  /etc/hosts updated")
        else:
            _print("  [dry-run] Would write the above entries to /etc/hosts")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("hosts", ok)
    return ok


def step_install_repo(repo_url, repo_dir, repo_branch=None):
    _print("\n════════════════════════════════════════")
    _print("  [repo] Cloning/updating adsb_actions")
    _print("════════════════════════════════════════")
    ok = True
    try:
        repo_dir = os.path.expanduser(repo_dir)
        parent = os.path.dirname(repo_dir)
        os.makedirs(parent, exist_ok=True)

        if os.path.isdir(os.path.join(repo_dir, ".git")):
            _print(f"  Repo exists at {repo_dir} — fetching...")
            run(["git", "-C", repo_dir, "fetch", "--all"])
            if repo_branch:
                _print(f"  Checking out branch {repo_branch}...")
                run(["git", "-C", repo_dir, "checkout", repo_branch])
            run(["git", "-C", repo_dir, "pull"])
        else:
            branch_args = ["--branch", repo_branch] if repo_branch else []
            branch_note = f" (branch {repo_branch})" if repo_branch else ""
            _print(f"  Cloning {repo_url}{branch_note} -> {repo_dir}...")
            run(["git", "clone"] + branch_args + [repo_url, repo_dir])
        _print(f"  Repo ready at {repo_dir}")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("repo", ok)
    return ok


def step_pip_install(repo_dir):
    _print("\n════════════════════════════════════════")
    _print("  [pip] Installing Python dependencies")
    _print("════════════════════════════════════════")
    ok = True
    try:
        repo_dir = os.path.expanduser(repo_dir)
        # Install package with gui extras (includes kivy[base] + kivymd)
        run([sys.executable, "-m", "pip", "install", "kivy[base]"])
        run([sys.executable, "-m", "pip", "install", f"{repo_dir}[gui]"])
        _print("  Dependencies installed")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("pip", ok)
    return ok


def step_vnc(password):
    _print("\n════════════════════════════════════════")
    _print("  [vnc] Enabling macOS Screen Sharing (VNC)")
    _print("════════════════════════════════════════")
    ok = True
    try:
        kickstart = (
            "/System/Library/CoreServices/RemoteManagement/"
            "ARDAgent.app/Contents/Resources/kickstart"
        )
        run([kickstart, "-activate", "-configure", "-access", "-on",
             "-clientopts", "-setvnclegacy", "-vnclegacy", "yes",
             "-clientopts", "-setvncpw", "-vncpw", password,
             "-restart", "-agent", "-privs", "-all"], sudo=True, check=False)
        _print("  Screen Sharing enabled on port 5900")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("vnc", ok)
    return ok


def step_safari_bookmarks(bookmarks):
    _print("\n════════════════════════════════════════")
    _print("  [bookmarks] Setting Safari bookmarks")
    _print("════════════════════════════════════════")
    ok = True
    try:
        # Safari stores bookmarks in ~/Library/Safari/Bookmarks.plist (binary plist).
        # We use Python's plistlib to read/modify/write it.
        import plistlib
        import shutil

        bookmarks_path = os.path.expanduser("~/Library/Safari/Bookmarks.plist")
        if not os.path.exists(bookmarks_path):
            _print("  WARNING: Safari bookmarks file not found — Safari may not have launched yet.")
            _print("  Skipping bookmark configuration.")
            step_banner("bookmarks", True)
            return True

        # Backup
        backup_path = bookmarks_path + ".88nv_backup"
        shutil.copy2(bookmarks_path, backup_path)
        _print(f"  Backed up to {backup_path}")

        with open(bookmarks_path, "rb") as f:
            plist = plistlib.load(f)

        # Find or create "BookmarksBar" children list
        bar = None
        children = plist.get("Children", [])
        for child in children:
            if child.get("Title") == "BookmarksBar":
                bar = child
                break
        if bar is None:
            bar = {"Title": "BookmarksBar", "WebBookmarkType": "WebBookmarkTypeList", "Children": []}
            children.append(bar)
            plist["Children"] = children

        bar_children = bar.get("Children", [])

        # Remove existing 88NV entries (by URL match) so re-runs don't duplicate
        existing_urls = {b[0] for b in bookmarks}
        bar_children = [c for c in bar_children
                        if c.get("URLString") not in existing_urls]

        # Append new bookmarks
        import uuid
        for url, label in bookmarks:
            bar_children.append({
                "WebBookmarkType": "WebBookmarkTypeLeaf",
                "URLString": url,
                "URIDictionary": {"title": label},
                "WebBookmarkUUID": str(uuid.uuid4()).upper(),
            })
            _print(f"    {label}: {url}")

        bar["Children"] = bar_children

        if not dry_run:
            # Kill Safari and sync agents BEFORE writing so they can't overwrite our file on quit
            for proc in ("Safari", "SafariBookmarksSyncAgent", "SafariCloudHistoryPushAgent"):
                run(["killall", proc], check=False)

            with open(bookmarks_path, "wb") as f:
                plistlib.dump(plist, f, fmt=plistlib.FMT_XML)
            _print(f"  {len(bookmarks)} bookmarks written to Safari BookmarksBar")
        else:
            _print(f"  [dry-run] Would write {len(bookmarks)} bookmarks to Safari BookmarksBar (Safari not killed)")

        _print("  NOTE: Launch Safari to verify bookmark bar is visible (View → Show Favorites Bar)")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("bookmarks", ok)
    return ok


# Chrome bookmark folder that 88NV-managed bookmarks live in. Keeping them in a
# named folder means re-runs replace the folder wholesale instead of trying to
# reconcile individual entries against whatever else is on the user's bar.
CHROME_FOLDER_NAME = "88NV"


def _chrome_checksum(roots):
    """Recompute the checksum Chrome stores alongside its bookmarks.

    Chrome validates this on load and silently drops the bookmark tree if it
    doesn't match, so it has to be rebuilt after any edit. The construction is
    Chrome's own: ids and type tags as UTF-8, names as UTF-16LE, depth-first
    over bookmark_bar, other, then synced.
    """
    md5 = hashlib.md5()

    def digest(node):
        node_type = node.get("type")
        if node_type == "url":
            md5.update(node["id"].encode())
            md5.update(node["name"].encode("utf-16-le"))
            md5.update(b"url")
            md5.update(node["url"].encode())
        elif node_type == "folder":
            md5.update(node["id"].encode())
            md5.update(node["name"].encode("utf-16-le"))
            md5.update(b"folder")
            for child in node.get("children", []):
                digest(child)

    for key in ("bookmark_bar", "other", "synced"):
        if key in roots:
            digest(roots[key])
    return md5.hexdigest()


def _chrome_max_id(roots):
    """Highest numeric id in the tree; new nodes must not collide with it."""
    highest = 0

    def walk(node):
        nonlocal highest
        node_id = node.get("id", "")
        if node_id.isdigit():
            highest = max(highest, int(node_id))
        for child in node.get("children", []) or []:
            walk(child)

    for root in roots.values():
        walk(root)
    return highest


def _chrome_timestamp():
    """Chrome epoch: microseconds since 1601-01-01 UTC."""
    epoch_delta = 11644473600  # seconds between 1601-01-01 and 1970-01-01
    return str(int((time.time() + epoch_delta) * 1_000_000))


def step_chrome_bookmarks(bookmarks):
    _print("\n════════════════════════════════════════")
    _print("  [chrome-bookmarks] Setting Chrome bookmarks")
    _print("════════════════════════════════════════")
    ok = True
    try:
        import shutil

        chrome_dir = os.path.expanduser(
            "~/Library/Application Support/Google/Chrome")
        if not os.path.isdir(chrome_dir):
            _print("  WARNING: Chrome profile not found — Chrome may not have launched yet.")
            _print("  Launch Chrome once, then re-run with:  --only chrome-bookmarks")
            step_banner("chrome-bookmarks", True)
            return True

        # A machine with more than one Chrome profile keeps bookmarks per
        # profile, and writing to "Default" on such a machine puts them
        # somewhere the operator never looks. Local State records which profile
        # Chrome opens by default; fall back to Default when it can't be read
        # (fresh install, unreadable JSON), which is the only profile there.
        profile_name = "Default"
        local_state_path = os.path.join(chrome_dir, "Local State")
        if os.path.exists(local_state_path):
            try:
                with open(local_state_path, "r", encoding="utf-8") as f:
                    local_state = json.load(f)
                profile_cfg = local_state.get("profile", {})
                candidate = (profile_cfg.get("last_used")
                             or (profile_cfg.get("last_active_profiles") or [None])[0])
                if candidate and os.path.isdir(os.path.join(chrome_dir, candidate)):
                    profile_name = candidate
            except (ValueError, OSError) as e:
                _print(f"  Could not read Local State ({e}) — using '{profile_name}' profile")

        profile_dir = os.path.join(chrome_dir, profile_name)
        bookmarks_path = os.path.join(profile_dir, "Bookmarks")
        _print(f"  Using Chrome profile: {profile_name}")

        if not os.path.isdir(profile_dir):
            _print(f"  WARNING: Chrome profile dir not found: {profile_dir}")
            _print("  Launch Chrome once, then re-run with:  --only chrome-bookmarks")
            step_banner("chrome-bookmarks", True)
            return True

        # Chrome holds bookmarks in memory and rewrites the file on exit, so a
        # running instance would clobber anything written here.
        chrome_was_running = subprocess.run(
            ["pgrep", "-x", "Google Chrome"],
            capture_output=True).returncode == 0
        if chrome_was_running:
            if dry_run:
                _print("  [dry-run] Chrome is running — would quit it before writing")
            else:
                _print("  Chrome is running — quitting it so it can't overwrite our changes")
                run(["osascript", "-e", 'quit app "Google Chrome"'], check=False)
                for _ in range(20):
                    still_up = subprocess.run(
                        ["pgrep", "-x", "Google Chrome"],
                        capture_output=True).returncode == 0
                    if not still_up:
                        break
                    time.sleep(0.5)
                else:
                    _print("  WARNING: Chrome did not quit — forcing it")
                    run(["killall", "Google Chrome"], check=False)
                    time.sleep(1)

        if not os.path.exists(bookmarks_path):
            # A fresh profile has no Bookmarks file until the first bookmark is
            # made; synthesize the minimal tree Chrome expects.
            _print("  No Bookmarks file yet — creating one")
            data = {"roots": {}, "version": 1}
        else:
            backup_path = bookmarks_path + ".88nv_backup"
            shutil.copy2(bookmarks_path, backup_path)
            _print(f"  Backed up to {backup_path}")
            with open(bookmarks_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Confirm our checksum construction matches the Chrome on THIS
            # machine before relying on it. If a future Chrome changes the
            # format, writing a checksum it rejects makes it discard the entire
            # bookmark bar — so bail out with the file untouched instead.
            stored = data.get("checksum")
            if stored and _chrome_checksum(data.get("roots", {})) != stored:
                _print("  ERROR: Chrome's bookmark checksum format is not the one this")
                _print("  script knows how to write (Chrome may have changed it).")
                _print("  Refusing to write — bookmarks left untouched.")
                step_banner("chrome-bookmarks", False)
                return False

        roots = data.setdefault("roots", {})
        for key in ("bookmark_bar", "other", "synced"):
            roots.setdefault(key, {
                "children": [],
                "date_added": _chrome_timestamp(),
                "date_modified": "0",
                "guid": str(uuid.uuid4()),
                "id": "",
                "name": key,
                "type": "folder",
            })

        next_id = _chrome_max_id(roots) + 1
        for key in ("bookmark_bar", "other", "synced"):
            if not roots[key].get("id"):
                roots[key]["id"] = str(next_id)
                next_id += 1

        bar = roots["bookmark_bar"]
        bar_children = bar.get("children", [])

        # Drop any previous 88NV folder so re-runs replace rather than duplicate.
        before = len(bar_children)
        bar_children = [c for c in bar_children
                        if not (c.get("type") == "folder"
                                and c.get("name") == CHROME_FOLDER_NAME)]
        if len(bar_children) != before:
            _print(f"  Removed existing '{CHROME_FOLDER_NAME}' folder from bookmarks bar")

        now = _chrome_timestamp()
        entries = []
        for url, label in bookmarks:
            entries.append({
                "date_added": now,
                "date_last_used": "0",
                "guid": str(uuid.uuid4()),
                "id": str(next_id),
                "name": label,
                "type": "url",
                "url": url,
            })
            next_id += 1
            _print(f"    {label}: {url}")

        folder = {
            "children": entries,
            "date_added": now,
            "date_last_used": "0",
            "date_modified": now,
            "guid": str(uuid.uuid4()),
            "id": str(next_id),
            "name": CHROME_FOLDER_NAME,
            "type": "folder",
        }
        next_id += 1

        # Prepend so the folder lands at the left edge of the bar, where it
        # stays visible no matter how many other bookmarks the user has.
        bar_children.insert(0, folder)
        bar["children"] = bar_children
        bar["date_modified"] = now

        data["checksum"] = _chrome_checksum(roots)
        data["version"] = data.get("version", 1)

        # sync_metadata describes the pre-edit tree; leaving it in place makes
        # Chrome try to reconcile against state that no longer exists, which can
        # undo the edit. Dropping it forces a clean re-sync.
        data.pop("sync_metadata", None)

        if not dry_run:
            tmp_path = bookmarks_path + ".88nv_tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=3)
            os.replace(tmp_path, bookmarks_path)
            # Chrome prefers Bookmarks.bak on a checksum failure; a stale one
            # would resurrect the old bar, so retire it.
            stale_bak = bookmarks_path + ".bak"
            if os.path.exists(stale_bak):
                os.remove(stale_bak)
            _print(f"  {len(bookmarks)} bookmarks written to Chrome "
                   f"'{CHROME_FOLDER_NAME}' folder on the bookmarks bar")
        else:
            _print(f"  [dry-run] Would write {len(bookmarks)} bookmarks to Chrome "
                   f"'{CHROME_FOLDER_NAME}' folder (Chrome not quit)")

        _print("  NOTE: Show the bar with View → Always Show Bookmarks Bar (⇧⌘B)")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("chrome-bookmarks", ok)
    return ok


def step_autostart(autostart_cfg, bookmarks_section, repo_dir):
    _print("\n════════════════════════════════════════")
    _print("  [autostart] Configuring login autostart")
    _print("════════════════════════════════════════")
    ok = True
    try:
        # Resolve Chrome URLs by matching bookmark labels
        # bookmarks_section is {URL: label}; invert to look up by label
        label_to_url = {label: url for url, label in bookmarks_section.items()}
        chrome_urls = []
        for key in sorted(k for k in autostart_cfg if k.startswith("OPEN_CHROME_BOOKMARK_")):
            label = autostart_cfg[key]
            url = label_to_url.get(label)
            if url:
                chrome_urls.append(url)
                _print(f"  Chrome: {label} -> {url}")
            else:
                _print(f"  WARNING: Bookmark label '{label}' not found in Bookmarks section — skipping")

        launch_script_rel = autostart_cfg.get("LAUNCH_SCRIPT", "")
        launch_args       = autostart_cfg.get("LAUNCH_ARGS", "")
        launch_cwd_cfg    = autostart_cfg.get("LAUNCH_CWD", "")
        startup_delay     = autostart_cfg.get("STARTUP_DELAY", "10")

        launch_script = ""
        launch_cwd = ""
        if launch_script_rel:
            launch_script = os.path.join(os.path.expanduser(repo_dir), launch_script_rel)
            if not dry_run and not os.path.exists(launch_script):
                _print(f"  WARNING: LAUNCH_SCRIPT does not exist: {launch_script}")
                _print("  The repo may not have been cloned yet, or the path is wrong.")

            # LAUNCH_CWD: the script is cwd-sensitive, so the working directory
            # is configurable. Absolute (or ~) paths are used as-is; relative
            # paths resolve against the repo dir. Default: the script's own dir.
            if launch_cwd_cfg:
                expanded = os.path.expanduser(launch_cwd_cfg)
                if os.path.isabs(expanded):
                    launch_cwd = expanded
                else:
                    launch_cwd = os.path.join(os.path.expanduser(repo_dir), expanded)
                launch_cwd = os.path.normpath(launch_cwd)
                if not dry_run and not os.path.isdir(launch_cwd):
                    _print(f"  WARNING: LAUNCH_CWD does not exist: {launch_cwd}")
            else:
                launch_cwd = os.path.dirname(launch_script)

            _print(f"  Launch script: {launch_script}")
            _print(f"  Launch args: {launch_args}")
            _print(f"  Launch cwd: {launch_cwd}")
            _print(f"  Python interpreter: {sys.executable}")

        home        = os.path.expanduser("~")
        script_dir  = os.path.join(home, "Library", "88nv")
        script_path = os.path.join(script_dir, "autostart.sh")
        plist_dir   = os.path.join(home, "Library", "LaunchAgents")
        plist_path  = os.path.join(plist_dir, "com.88nv.autostart.plist")
        log_path    = os.path.join(home, "Library", "Logs", "88nv_autostart.log")

        if not dry_run:
            os.makedirs(script_dir, exist_ok=True)
            os.makedirs(plist_dir, exist_ok=True)

        # ── Build launcher shell script ───────────────────────────────────────
        lines = [
            "#!/bin/bash",
            f"# 88NV autostart launcher — generated by setup.py on {datetime.datetime.now():%Y-%m-%d}",
            f'LOG="{log_path}"',
            'exec >> "$LOG" 2>&1',
            'echo "=== $(date) 88NV autostart ==="',
            f"sleep {startup_delay}",
        ]

        if chrome_urls:
            # Only the first `open -n` actually launches Chrome; the rest hand
            # their URL to the running instance via the profile singleton. At
            # cold boot that instance is still starting up, and handoffs that
            # arrive too early get dropped — which is why only one window
            # appeared. So: launch the first URL, poll until the process is up,
            # let the singleton settle, then hand off the rest with wider gaps.
            lines += [
                "",
                "# Chrome windows — pgrep guard skips opening if Chrome is already running",
                'pgrep -x "Google Chrome" > /dev/null || {',
                f'    open -na "Google Chrome" --args --new-window "{chrome_urls[0]}"',
            ]
            if len(chrome_urls) > 1:
                lines += [
                    "    # Wait for Chrome to come up before handing off more URLs",
                    "    for _ in $(seq 1 30); do",
                    '        pgrep -x "Google Chrome" > /dev/null && break',
                    "        sleep 1",
                    "    done",
                    "    # Let the singleton finish initializing so handoffs aren't dropped",
                    f"    sleep {CHROME_SETTLE_SECS}",
                ]
                for url in chrome_urls[1:]:
                    lines.append(f'    open -na "Google Chrome" --args --new-window "{url}"')
                    lines.append(f"    sleep {CHROME_HANDOFF_SECS}")
            else:
                lines.append(f"    sleep {CHROME_HANDOFF_SECS}")
            lines.append("}")

        if launch_script:
            # Run from launch_cwd; reference the script by a path relative to it
            # so the command works regardless of where cwd lands.
            script_from_cwd = os.path.relpath(launch_script, launch_cwd)
            lines += [
                "",
                f"# App launcher (python: {sys.executable})",
                f'cd "{launch_cwd}"',
                f'exec "{sys.executable}" "{script_from_cwd}" {launch_args}',
            ]

        script_content = "\n".join(lines) + "\n"

        if not dry_run:
            with open(script_path, "w") as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
            _print(f"  Wrote launcher: {script_path}")
        else:
            _print(f"  [dry-run] Would write launcher: {script_path}")
            for line in lines:
                _print(f"    {line}")

        # ── Build and write plist ─────────────────────────────────────────────
        import plistlib as _plistlib
        plist_data = {
            "Label": "com.88nv.autostart",
            "ProgramArguments": ["/bin/bash", script_path],
            "RunAtLoad": True,
            # No KeepAlive: run once at login only. With KeepAlive, launchd
            # relaunched the script every time the app exited, which retriggered
            # the Chrome block (and bounced the dock) without reopening windows.
            "StandardOutPath": log_path,
            "StandardErrorPath": log_path,
            "ProcessType": "Interactive",
        }

        if not dry_run:
            with open(plist_path, "wb") as f:
                _plistlib.dump(plist_data, f, fmt=_plistlib.FMT_XML)
            _print(f"  Wrote plist: {plist_path}")
        else:
            _print(f"  [dry-run] Would write plist: {plist_path}")

        # ── Load the LaunchAgent ──────────────────────────────────────────────
        if not dry_run:
            uid = str(os.getuid())
            label = "com.88nv.autostart"
            service = f"gui/{uid}/{label}"

            # bootout is asynchronous. If the running job ignores SIGTERM — the
            # launcher execs a GUI app, which does — launchd waits 5s before
            # SIGKILLing it. Bootstrapping inside that teardown window fails
            # with "Bootstrap failed 5: Input/output error", so poll until the
            # service is really gone from the domain before loading it again.
            run(["launchctl", "bootout", service], check=False)
            for _ in range(LAUNCHCTL_BOOTOUT_TIMEOUT):
                probe = run(["launchctl", "print", service],
                            capture=True, check=False)
                if probe.returncode != 0:
                    break
                time.sleep(1)
            else:
                _print(f"  WARNING: {label} still loaded after "
                       f"{LAUNCHCTL_BOOTOUT_TIMEOUT}s — bootstrap may fail")

            boot = run(["launchctl", "bootstrap", f"gui/{uid}", plist_path],
                       capture=True, check=False)
            if boot.returncode != 0:
                _print(f"  ERROR: launchctl bootstrap failed (rc={boot.returncode})")
                for stream in (boot.stdout, boot.stderr):
                    if stream and stream.strip():
                        _print(f"    {stream.strip()}")

            result = run(["launchctl", "list", "com.88nv.autostart"], capture=True, check=False)
            if result.returncode == 0 and "com.88nv.autostart" in result.stdout:
                _print("  LaunchAgent loaded and verified — will run at next login")
            else:
                _print("  WARNING: LaunchAgent may not have loaded correctly — check launchctl list com.88nv.autostart")
                ok = False
        else:
            _print(f"  [dry-run] Would run: launchctl bootout / bootstrap gui/{os.getuid()} {plist_path}")

    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("autostart", ok)
    return ok


# ── main ──────────────────────────────────────────────────────────────────────

ALL_STEPS = ["network", "hostname", "hosts", "repo", "pip",
             "vnc", "bookmarks", "chrome-bookmarks", "autostart"]


def main():
    global dry_run

    parser = argparse.ArgumentParser(description="88NV ATC Mac Mini Setup")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--skip-install", action="store_true",
                        help="Skip repo clone and pip install; apply network/hostname/hosts only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing them")
    parser.add_argument("--no-vnc", action="store_true",
                        help="Skip enabling macOS Screen Sharing (VNC)")
    parser.add_argument("--no-autostart", action="store_true",
                        help="Skip autostart configuration")
    parser.add_argument("--emit-etchosts", action="store_true",
                        help="Print the /etc/hosts content that would be written, then exit")
    parser.add_argument("--only", metavar="STEP",
                        help="Run only these steps (comma-separated). "
                             "See --list-steps for names.")
    parser.add_argument("--list-steps", action="store_true",
                        help="List the step names accepted by --only, then exit")
    args = parser.parse_args()

    if args.list_steps:
        print("Steps (in run order):")
        for name in ALL_STEPS:
            print(f"  {name}")
        return 0

    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in only if s not in ALL_STEPS]
        if unknown:
            print(f"ERROR: unknown step(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"Valid steps: {', '.join(ALL_STEPS)}", file=sys.stderr)
            return 2

    def should_run(name):
        return name in only if only else True
    dry_run = args.dry_run

    if args.emit_etchosts:
        config = parse_config(args.config)
        hosts_section = config.get("Hosts", {})
        hosts_entries = list(hosts_section.items())
        print(build_hosts_block(hosts_entries), end="")
        return 0

    open_log()

    _print("")
    _print("════════════════════════════════════════════════════")
    _print("  88NV Mac Mini Setup")
    _print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print("════════════════════════════════════════════════════")
    if dry_run:
        _print("  *** DRY RUN — no changes will be made ***")
    _print("")

    # Load config
    _print(f"Reading config from: {args.config}")
    config = parse_config(args.config)

    net = config.get("Network")
    if not net:
        _print("ERROR: Network section not found in config.md.")
        sys.exit(1)
    repo_cfg = config.get("Repo", {})
    hosts_section = config.get("Hosts", {})
    bookmarks_section = config.get("Bookmarks", {})

    hostname = net.get("HOSTNAME")
    if not hostname or hostname == "FILL_ME_IN":
        _print("ERROR: HOSTNAME not set in config.md Network table.")
        sys.exit(1)

    ip = net.get("IP")
    if not ip or ip == "FILL_ME_IN":
        _print("ERROR: IP not set in config.md Network table (use a static IP or 'dhcp').")
        sys.exit(1)
    _print(f"Host: {hostname} / {ip}")

    repo_url = repo_cfg.get("REPO_URL")
    repo_dir = repo_cfg.get("REPO_DIR")
    repo_branch = repo_cfg.get("REPO_BRANCH") or None
    if not args.skip_install and (not repo_url or not repo_dir):
        _print("ERROR: REPO_URL and REPO_DIR must be set in config.md Repo table.")
        sys.exit(1)

    hosts_entries = [(h_ip, hostname_val) for h_ip, hostname_val in hosts_section.items()]
    hosts_ip = next((h_ip for h_ip, h in hosts_entries if h == hostname), None)
    if hosts_ip is None:
        _print(f"ERROR: '{hostname}' not found in Hosts table in config.md.")
        sys.exit(1)
    if ip.lower() != "dhcp" and hosts_ip != ip:
        _print(f"ERROR: IP mismatch — Network table has {ip} but Hosts table has {hosts_ip} for '{hostname}'.")
        sys.exit(1)

    # Parse bookmarks table: dict keys are URLs, values are labels
    bookmarks = [(url, label) for url, label in bookmarks_section.items()]

    # Prompt for VNC password upfront
    vnc_password = None
    if not args.no_vnc and not dry_run and should_run("vnc"):
        while True:
            pw1 = getpass.getpass("Set VNC password (for Screen Sharing, or Enter to skip): ")
            if not pw1:
                _print("  Skipping VNC setup (no password entered).")
                args.no_vnc = True
                break
            pw2 = getpass.getpass("Confirm VNC password: ")
            if pw1 == pw2:
                vnc_password = pw1
                break
            print("Passwords do not match — try again.")

    results = {}

    # ── Network ──
    if should_run("network"):
        results["network"] = step_network(net, ip)

    # ── Hostname ──
    if should_run("hostname"):
        results["hostname"] = step_hostname(hostname)

    # ── /etc/hosts ──
    if should_run("hosts"):
        results["hosts"] = step_hosts(hosts_entries)

    if not args.skip_install:
        # ── Clone repo ──
        if should_run("repo"):
            results["repo"] = step_install_repo(repo_url, repo_dir, repo_branch)

        # ── pip install ──
        if should_run("pip"):
            results["pip"] = step_pip_install(repo_dir)

    # ── VNC / Screen Sharing ──
    if not args.no_vnc and should_run("vnc"):
        results["vnc"] = step_vnc(vnc_password or "")

    # ── Safari bookmarks ──
    if should_run("bookmarks"):
        results["bookmarks"] = step_safari_bookmarks(bookmarks)

    # ── Chrome bookmarks ──
    if should_run("chrome-bookmarks"):
        results["chrome-bookmarks"] = step_chrome_bookmarks(bookmarks)

    # ── Autostart ──
    autostart_cfg = config.get("Autostart", {})
    if not args.no_autostart and not args.skip_install and autostart_cfg \
            and should_run("autostart"):
        results["autostart"] = step_autostart(autostart_cfg, bookmarks_section, repo_dir)

    # ── Summary ──
    _print("\n════════════════════════════════════════════════════")
    _print("  SUMMARY")
    _print("════════════════════════════════════════════════════")
    all_ok = True
    for step, ok in results.items():
        icon = "✅" if ok else "❌"
        _print(f"  {icon}  {step}")
        if not ok:
            all_ok = False

    _print("")
    if all_ok:
        _print("All steps completed successfully.")
    else:
        failed = [s for s, ok in results.items() if not ok]
        _print("Some steps FAILED — review output above.")
        _print(f"Re-run just those steps with:  --only {','.join(failed)}")
    _print(f"\nFull log saved to: {log_path}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
