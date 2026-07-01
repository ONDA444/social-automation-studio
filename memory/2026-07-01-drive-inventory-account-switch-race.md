# Drive inventory account switch race

## Symptom

After syncing Drive inventory and saving, switching to another channel and back could show `0` indexed videos even though the inventory was already imported and persisted.

## Root cause

The schedule page updated `driveCfg` for the selected account and immediately called `loadDrive()`. React state updates are asynchronous, so `loadDrive()` could use the previous account's `driveCfg.drive_niche`, filtering the current account inventory with the wrong niche.

## Fix

`loadDrive()` now accepts explicit config/account overrides. When selecting an account or saving/syncing, the page computes the selected account's Drive config and passes it directly to `loadDrive()` instead of relying on possibly stale React state.

## Evidence

`npm run build` in `frontend`

Result: Vite build OK.

## Status

DONE
