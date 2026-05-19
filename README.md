# Switch Game Catalog

A local Windows desktop catalog for personal Nintendo Switch game files. It scans user-selected `.nsp` and `.xci` folders, stores records in SQLite, matches update files from a separate updates folder, and displays the collection in a two-pane library interface.

## Features

- Recursive base-game scan for `.nsp` and `.xci`
- Separate recursive updates-folder scan
- Fuzzy update-to-game matching
- SQLite catalog at `~/.switch_library_catalog/library.sqlite3`
- Settings stored at `~/.switch_library_catalog/settings.json`
- Cover grid view with adjustable art size and double-click navigation back to the library/details view
- Favorites with a heart in the list and a highlighted frame in grid view
- Right-click option to move a mistaken game entry into Unmatched Updates for DLC/update matching
- Larger screenshot browser with Previous/Next controls
- Unmatched updates view
- Optional IGDB metadata lookup for real cover art
- Manual metadata rematching from the game list right-click menu
- Install button that moves the base game first, then selected updates/DLC, into a configured install folder
- Right-click install for selected update/DLC files when the base game is already installed
- Details view compares local update versions against the cached titledb `versions.json` latest release data
- Titledb version lists refresh automatically when the cached files are older than 24 hours
- Right-click deletion for duplicate game files and old update/DLC files

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
