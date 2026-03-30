# Vrooem Gateway

## Local Location Refresh

When `data/unified_locations.json` changes, the running gateway process can still serve the old in-memory location snapshot until it reloads.

Use the explicit local refresh workflow:

```bash
bash scripts/local-refresh-locations.sh
```

What it does:
1. regenerates `unified_locations.json`
2. restarts the local gateway container
3. prints the loaded location metadata from `/api/v1/locations/status`

Expected result:
- `location_count` reflects the refreshed file
- `location_data_version` changes when the file content changes
- changed locations such as `Antwerp Downtown` appear immediately in local page searches
