# Portal brand flag

`patch_flag.py` adds a decorative 18 × 12 px Ukrainian SVG flag directly after
the portal eyebrow text. It has no tooltip, caption or action. The blue stripe
is above the yellow stripe. User context: development in Czechia by a Ukrainian;
the current request explicitly authorizes the flag only.

Installed on M02 active USB `/dev/sda2`, release `2026-10-16-1155`, firmware
manifest `2026-10-16-115550`. The complete bundle retains the paired core and
engine paths from `1055`; their bytes and the running core PID are preserved.
Only portal copies and release metadata are activated. The installer keeps a
rollback copy and verifies settings, acquisition modules and live stream health.

Verified server rendering and the live Chrome view: one flag, 18 × 12 px,
no tooltip attributes or tooltip-bearing ancestor. The live check preserved
23 acquisition Python components, core PID `1333461`, all saved settings and
continued processing (478 new worker results without stream-error increments).
The version CSV and existing Google Sheet journal are synchronized separately.

Bundle SHA-256:
`470f98e4b469708a0ed95d8a6cf05e0f86df16f9e917c5d0dc5a9ac2622bdc7c`.
Portal SHA-256:
`736a075bf17666879881d6a3fee2978e8682a3635655cd055465f978eb6ef8f7`.

Evidence: `.tmp/flag-20261016/{inspect,render,build,install,registry}.json`.
Backup: `/home/techaid/Documents/.tmp/flag-20261016/backup-2026-10-16-1155`.
