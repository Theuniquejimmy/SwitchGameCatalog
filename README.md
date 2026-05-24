# Switch Game Catalog

A local Windows desktop catalog for personal Nintendo Switch game files. It scans user-selected `.nsp`, `.nsz`, and `.xci` folders, stores records in SQLite, matches update files from a separate updates folder, and displays the collection in a two-pane library interface. Let's you also install games wired and wirelessly through DBI.

<img width="2560" height="1390" alt="image" src="https://github.com/user-attachments/assets/8d5eeac6-0ef0-4e85-aa37-c6a1e8628962" />
<img width="2560" height="1390" alt="image" src="https://github.com/user-attachments/assets/0bc17ebb-97d3-4332-b305-c76c36747506" />

## Features

- Recursive base-game scan for `.nsp`, `.nsz`, and `.xci`
- Fuzzy update-to-game matching
- SQLite catalog at `~/.switch_library_catalog/library.sqlite3`
- Settings stored at `~/.switch_library_catalog/settings.json`
- Cover grid view with adjustable art size and double-click navigation back to the library/details view
- Favorites with a heart in the list and a highlighted frame in grid view
- Right-click option to move a mistaken game entry into Unmatched Updates for DLC/update matching
- Larger screenshot browser with Previous/Next controls
- Unmatched updates view
- Optional IGDB metadata lookup for real cover art and trailers
- Manual metadata rematching from the game list right-click menu
- Install button that moves the base game first, then selected updates/DLC, into a configured install folder
- Right-click install for selected update/DLC files when the base game is already installed
- Details view compares local update versions against the cached titledb `versions.json` latest release data
- Titledb version lists refresh automatically when the cached files are older than 24 hours
- Right-click deletion for duplicate game files and old update/DLC files
- Built-in HTTP server for installing cataloged games and updates over Wi-Fi with DBI

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py

**or just run the exe**
```

Open **Settings**, choose a base games folder and updates folder, then run **Rescan**.

IGDB metadata requires a Twitch/IGDB client ID and client secret. Without API credentials, scanning and cataloging still work.

## DBI Wi-Fi Install

The app can run a small built-in HTTP server that exposes only files already present in your catalog. Open **Settings**, enable **HTTP server**, choose a port if needed, and optionally set a username and password. The default port is `8000`.

Settings shows the exact URL to enter in DBI. It will look like:

```text
http://192.168.1.213:8000/dir/
```

Use the `/dir/` URL as DBI's Apache-style HTTP directory source. Downloads support HTTP range requests, so interrupted transfers can resume when the installer supports it.

For best stability, launch DBI in full-RAM/application mode. To do this, hold R while opening an installed game, then start DBI from the Homebrew Menu. Heavy installers are more likely to crash if DBI is launched from the Album in applet mode.

Also make sure you’re using a DBI version that matches your Switch firmware. Older DBI builds can crash or behave unpredictably on newer firmware versions.

Accessing your catalog from outside your home network

Basic Auth over plain HTTP is acceptable on a trusted local network, but the username and password are not encrypted. Because of that, you should not port-forward the catalog directly to the public internet.

For secure access from anywhere, use a private VPN-style network such as Tailscale or WireGuard. Install it on both your PC and your phone or laptop, then open the catalog using the PC’s Tailscale/WireGuard IP address. This keeps the connection encrypted, avoids exposing any ports publicly, and your password protection still remains in place.


## Project Structure

```text
main.py
switch_catalog/
  app.py
  db.py
  filename.py
  metadata.py
  paths.py
  scanner.py
  settings.py
  ui.py
```
