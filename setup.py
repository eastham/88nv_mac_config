#!/usr/bin/env python3
"""
88NV ATC Mac Mini Setup Script
Reads config.md, applies network/hostname config, installs software.

Usage:
    python3 setup.py [options]

Options:
    --config PATH         Path to config.md (default: same dir as this script)
    --ip IP               Override STATIC_IP from config.md
    --subnet MASK         Override SUBNET from config.md
    --gateway GW          Override GATEWAY from config.md
    --dns DNS             Override DNS from config.md
    --hostname NAME       Override HOSTNAME from config.md
    --disable-wifi        Override DISABLE_WIFI to true
    --skip-install        Skip software install steps (network/hostname only)
    --dry-run             Print commands without executing them
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile
import textwrap

try:
    from markdown_it import MarkdownIt
except ImportError:
    print("ERROR: markdown-it-py is not installed. Run bootstrap.sh first.")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "config.md")

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
    """Parse config.md. Returns dict of dicts, keyed by section heading."""
    with open(path) as f:
        text = f.read()

    md = MarkdownIt()
    tokens = md.parse(text)

    config = {}
    current_section = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # Section heading
        if tok.type == "heading_open":
            content_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            if content_tok and content_tok.type == "inline":
                current_section = content_tok.content.strip()
                config[current_section] = {}
            i += 1

        # Table rows: scan for tr/td patterns
        elif tok.type == "tr_open" and current_section:
            # Collect td inline contents
            cells = []
            j = i + 1
            while j < len(tokens) and tokens[j].type != "tr_close":
                if tokens[j].type == "inline":
                    cells.append(tokens[j].content.strip())
                j += 1
            if len(cells) == 2:
                key, val = cells[0], cells[1]
                if key != "Key":  # skip header row
                    config[current_section][key] = val
            i = j

        i += 1

    return config

# ── command runner ────────────────────────────────────────────────────────────

dry_run = False

def run(cmd, *, check=True, capture=False, sudo=False):
    """Run a shell command with logging."""
    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + (cmd if isinstance(cmd, list) else cmd.split())
    if isinstance(cmd, str):
        cmd = cmd.split()
    _print(f"  + {' '.join(cmd)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    kwargs = {"check": check}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    else:
        kwargs["stdout"] = sys.stdout
        kwargs["stderr"] = sys.stderr
    return subprocess.run(cmd, **kwargs)

def run_shell(cmd_str, *, check=True, sudo=False):
    """Run a shell string (allows pipes/redirects)."""
    _print(f"  + {cmd_str}")
    if dry_run:
        return 0
    if sudo and os.geteuid() != 0:
        cmd_str = "sudo " + cmd_str
    return subprocess.run(cmd_str, shell=True, check=check,
                          stdout=sys.stdout, stderr=sys.stderr).returncode

# ── steps ─────────────────────────────────────────────────────────────────────

def step_network(net, ip, disable_wifi):
    _print("\n════════════════════════════════════════")
    _print("  [network] Configuring static IP")
    _print("════════════════════════════════════════")
    ok = True
    try:
        # Find active Ethernet interface
        result = run(["networksetup", "-listallhardwareports"], capture=True)
        iface = None
        lines = result.stdout.splitlines()
        for idx, line in enumerate(lines):
            if "Ethernet" in line or "USB 10/100" in line or "Thunderbolt" in line:
                for sub in lines[idx:idx+5]:
                    m = re.search(r"Device:\s+(\S+)", sub)
                    if m:
                        iface = m.group(1)
                        break
                if iface:
                    break
        if not iface:
            _print("  WARNING: Could not auto-detect Ethernet interface — trying en0")
            iface = "en0"
        _print(f"  Interface: {iface}")

        subnet = net["SUBNET"]
        gateway = net["GATEWAY"]
        dns = net["DNS"]

        run(["networksetup", "-setmanual", iface, ip, subnet, gateway], sudo=True)
        run(["networksetup", "-setdnsservers", iface, dns], sudo=True)
        _print(f"  Static IP {ip}/{subnet} gw {gateway} dns {dns} set on {iface}")

        if disable_wifi:
            _print("  Disabling WiFi...")
            # Find WiFi interface
            result2 = run(["networksetup", "-listallhardwareports"], capture=True)
            wifi_iface = None
            lines2 = result2.stdout.splitlines()
            for idx2, line2 in enumerate(lines2):
                if "Wi-Fi" in line2 or "AirPort" in line2:
                    for sub2 in lines2[idx2:idx2+5]:
                        m2 = re.search(r"Device:\s+(\S+)", sub2)
                        if m2:
                            wifi_iface = m2.group(1)
                            break
                    if wifi_iface:
                        break
            if wifi_iface:
                run(["networksetup", "-setairportpower", wifi_iface, "off"], sudo=True)
                _print(f"  WiFi ({wifi_iface}) disabled")
            else:
                _print("  WARNING: Could not find WiFi interface to disable")

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


def step_hosts(hosts_entries):
    _print("\n════════════════════════════════════════")
    _print("  [hosts] Writing /etc/hosts entries")
    _print("════════════════════════════════════════")
    ok = True
    try:
        marker_start = "# 88NV ATC BEGIN"
        marker_end = "# 88NV ATC END"

        with open("/etc/hosts") as f:
            existing = f.read()

        # Strip old 88NV block if present
        existing = re.sub(
            rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
            "", existing, flags=re.DOTALL
        )

        block_lines = [marker_start]
        for ip, hostname in hosts_entries:
            block_lines.append(f"{ip:<16} {hostname}")
        block_lines.append(marker_end)
        block = "\n".join(block_lines) + "\n"

        new_content = existing.rstrip("\n") + "\n\n" + block

        # Write via temp file + sudo mv
        with tempfile.NamedTemporaryFile("w", suffix=".hosts", delete=False) as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name

        run(["cp", tmp_path, "/etc/hosts"], sudo=True)
        os.unlink(tmp_path)
        _print(f"  Added {len(hosts_entries)} entries to /etc/hosts")
        for ip, hostname in hosts_entries:
            _print(f"    {ip}  {hostname}")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("hosts", ok)
    return ok


def step_install_repo(repo_url, repo_dir):
    _print("\n════════════════════════════════════════")
    _print("  [repo] Cloning/updating adsb_actions2")
    _print("════════════════════════════════════════")
    ok = True
    try:
        repo_dir = os.path.expanduser(repo_dir)
        parent = os.path.dirname(repo_dir)
        os.makedirs(parent, exist_ok=True)

        if os.path.isdir(os.path.join(repo_dir, ".git")):
            _print(f"  Repo exists at {repo_dir} — pulling...")
            run(["git", "-C", repo_dir, "pull"])
        else:
            _print(f"  Cloning {repo_url} -> {repo_dir}...")
            run(["git", "clone", repo_url, repo_dir])
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
        run(["pip3", "install", "kivy[base]"])
        run(["pip3", "install", f"{repo_dir}[gui]"])
        _print("  Dependencies installed")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("pip", ok)
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

        # Remove existing 88NV entries (by URL match)
        existing_urls = {b[1] for b in bookmarks}
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

        # Kill Safari if running so the write isn't overwritten on quit
        run(["killall", "Safari"], check=False)

        with open(bookmarks_path, "wb") as f:
            plistlib.dump(plist, f, fmt=plistlib.FMT_XML)

        _print(f"  {len(bookmarks)} bookmarks written to Safari BookmarksBar")
        _print("  NOTE: Launch Safari to verify bookmark bar is visible (View → Show Favorites Bar)")
    except Exception as e:
        _print(f"  ERROR: {e}")
        ok = False
    step_banner("bookmarks", ok)
    return ok


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global dry_run

    parser = argparse.ArgumentParser(description="88NV ATC Mac Mini Setup")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--ip", help="Override static IP (normally derived from Hosts table by hostname)")
    parser.add_argument("--subnet")
    parser.add_argument("--gateway")
    parser.add_argument("--dns")
    parser.add_argument("--hostname")
    parser.add_argument("--disable-wifi", action="store_true")
    parser.add_argument("--skip-install", action="store_true",
                        help="Skip repo clone and pip install; apply network/hostname/hosts only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing them")
    args = parser.parse_args()
    dry_run = args.dry_run

    open_log()

    _print("")
    _print("════════════════════════════════════════════════════")
    _print("  88NV ATC Mac Mini Setup")
    _print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print("════════════════════════════════════════════════════")
    if dry_run:
        _print("  *** DRY RUN — no changes will be made ***")
    _print("")

    # Load config
    _print(f"Reading config from: {args.config}")
    config = parse_config(args.config)

    net = config.get("Network", {})
    repo_cfg = config.get("Repo", {})
    hosts_section = config.get("Hosts", {})
    bookmarks_section = config.get("Bookmarks", {})

    # CLI overrides
    if args.subnet:    net["SUBNET"] = args.subnet
    if args.gateway:   net["GATEWAY"] = args.gateway
    if args.dns:       net["DNS"] = args.dns
    if args.hostname:  net["HOSTNAME"] = args.hostname

    disable_wifi = args.disable_wifi or net.get("DISABLE_WIFI", "false").lower() == "true"

    hostname = net.get("HOSTNAME", "atc-mac")
    repo_url = repo_cfg.get("REPO_URL", "https://github.com/eastham/adsb_actions2")
    repo_dir = repo_cfg.get("REPO_DIR", "~/git2/adsb_actions2")

    # Parse hosts table: dict keys are IPs, values are hostnames
    hosts_entries = [(ip, hostname_val) for ip, hostname_val in hosts_section.items()]

    # Derive this machine's static IP from the Hosts table by matching HOSTNAME
    static_ip = args.ip  # CLI override takes precedence
    if not static_ip:
        for ip, hostname_val in hosts_entries:
            if hostname_val == hostname:
                static_ip = ip
                break
    if not static_ip:
        _print(f"ERROR: HOSTNAME '{hostname}' not found in Hosts table and --ip not provided.")
        _print("       Add this machine to the Hosts section of config.md or pass --ip.")
        sys.exit(1)
    _print(f"Resolved static IP for '{hostname}': {static_ip}")

    # Parse bookmarks table: dict keys are URLs, values are labels
    bookmarks = [(url, label) for url, label in bookmarks_section.items()]

    results = {}

    # ── Network ──
    results["network"] = step_network(net, static_ip, disable_wifi)

    # ── Hostname ──
    results["hostname"] = step_hostname(hostname)

    # ── /etc/hosts ──
    results["hosts"] = step_hosts(hosts_entries)

    if not args.skip_install:
        # ── Clone repo ──
        results["repo"] = step_install_repo(repo_url, repo_dir)

        # ── pip install ──
        results["pip"] = step_pip_install(repo_dir)

    # ── Safari bookmarks ──
    results["bookmarks"] = step_safari_bookmarks(bookmarks)

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
        _print("Some steps FAILED — review output above and re-run failed steps.")
    _print(f"\nFull log saved to: {log_path}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
