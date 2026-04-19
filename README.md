# iPodSyncer

macOS CLI for syncing music, podcasts, and audiobooks to an iPod Classic 6G running factory Apple firmware.

Status: design phase. See [FEASIBILITY.md](./FEASIBILITY.md) and [plans/](./plans/).

## Scope (v0.1)

- **Target:** iPod Classic 6G / 6.5G only, factory firmware, USB (Apple Silicon Mac on Sequoia).
- **Media:** music, podcasts, audiobooks.
- **Input:** local source tree (`music/`, `podcasts/<show>/`, `audiobooks/<author>/*.m4b`).
- **Transcoding:** auto FLAC/Opus/Ogg → AAC ~256k VBR via ffmpeg. Originals untouched.
- **DB:** libgpod (gerion0 fork) handles iTunesDB / iTunesCDB / ArtworkDB + hash58.

Out of scope: other iPod models, iPod Touch, Rockbox, RSS fetching, audiobook assembly from MP3 folders, Apple Music / DRM.

## Requirements

- macOS Sequoia (Apple Silicon or Intel)
- Python 3.12+
- MacPorts (for libgpod + dependencies)
- ffmpeg
- Full Disk Access granted to the terminal/CLI
