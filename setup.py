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
import os
import re
import subprocess
import sys
import tempfile
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

def _detect_interfaces():
    """Return (eth_iface, wifi_iface) from networksetup; either may be None."""
    if dry_run:
        return "en0", "en1"
    result = run(["networksetup", "-listallhardwareports"], capture=True)
    lines = result.stdout.splitlines()
    eth = wifi = None
    for idx, line in enumerate(lines):
        if not eth and ("Ethernet" in line or "USB 10/100" in line or "Thunderbolt" in line):
            for sub in lines[idx:idx+5]:
                m = re.search(r"Device:\s+(\S+)", sub)
                if m:
                    eth = m.group(1)
                    break
        if not wifi and ("Wi-Fi" in line or "AirPort" in line):
            for sub in lines[idx:idx+5]:
                m = re.search(r"Device:\s+(\S+)", sub)
                if m:
                    wifi = m.group(1)
                    break
    return eth, wifi


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
        eth, wifi = _detect_interfaces()
        _print(f"  Detected — ethernet: {eth or 'none'}, wifi: {wifi or 'none'}")

        if eth:
            _print(f"  Using Ethernet ({eth})")
            ok = _configure_iface(eth, ip, net)
            if not ok and wifi:
                _print(f"  WARNING: Ethernet config failed — falling back to WiFi ({wifi})")
                ok = _configure_iface(wifi, ip, net)
            elif ok and wifi:
                run(["networksetup", "-setairportpower", wifi, "off"], sudo=True)
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
        startup_delay     = autostart_cfg.get("STARTUP_DELAY", "10")

        launch_script = ""
        if launch_script_rel:
            launch_script = os.path.join(os.path.expanduser(repo_dir), launch_script_rel)
            if not dry_run and not os.path.exists(launch_script):
                _print(f"  WARNING: LAUNCH_SCRIPT does not exist: {launch_script}")
                _print("  The repo may not have been cloned yet, or the path is wrong.")
            _print(f"  Launch script: {launch_script}")
            _print(f"  Launch args: {launch_args}")
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
            lines += [
                "",
                "# Chrome windows — pgrep guard prevents duplicate windows on app-crash restart",
                'pgrep -x "Google Chrome" > /dev/null || {',
            ]
            for i, url in enumerate(chrome_urls):
                lines.append(f'    open -na "Google Chrome" --args --new-window "{url}"')
                if i < len(chrome_urls) - 1:
                    lines.append("    sleep 1")
            lines += ["    sleep 2", "}"]

        if launch_script:
            script_dir_path = os.path.dirname(launch_script)
            script_basename = os.path.basename(launch_script)
            lines += [
                "",
                f"# App launcher (python: {sys.executable})",
                f'cd "{script_dir_path}"',
                f'exec "{sys.executable}" "{script_basename}" {launch_args}',
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
            "KeepAlive": {"SuccessfulExit": False},
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
            run(["launchctl", "bootout", f"gui/{uid}/com.88nv.autostart"], check=False)
            run(["launchctl", "bootstrap", f"gui/{uid}", plist_path])

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
    args = parser.parse_args()
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
    if not args.no_vnc and not dry_run:
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
    results["network"] = step_network(net, ip)

    # ── Hostname ──
    results["hostname"] = step_hostname(hostname)

    # ── /etc/hosts ──
    results["hosts"] = step_hosts(hosts_entries)

    if not args.skip_install:
        # ── Clone repo ──
        results["repo"] = step_install_repo(repo_url, repo_dir, repo_branch)

        # ── pip install ──
        results["pip"] = step_pip_install(repo_dir)

    # ── VNC / Screen Sharing ──
    if not args.no_vnc:
        results["vnc"] = step_vnc(vnc_password or "")

    # ── Safari bookmarks ──
    results["bookmarks"] = step_safari_bookmarks(bookmarks)

    # ── Autostart ──
    autostart_cfg = config.get("Autostart", {})
    if not args.no_autostart and not args.skip_install and autostart_cfg:
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
        _print("Some steps FAILED — review output above and re-run failed steps.")
    _print(f"\nFull log saved to: {log_path}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
