# Examples

Working implementations for Section 11 integrations.

## Available Methods

| Folder | Description | Status |
|--------|-------------|--------|
| [json-auto-sync](json-auto-sync/) | Automated GitHub Actions sync (every 15 min) | ✅ Ready |
| [json-manual](json-manual/) | Manual export from Mac/PC | ✅ Ready |
| custom-gpt | ChatGPT CustomGPT implementation | 🔜 Coming |
| mcp-server | MCP Server with direct API access | 🔜 Coming |

---

## Quick Start

### Option A: Automated Sync (Recommended)
Best for: Always-fresh data, zero maintenance after setup.

→ [json-auto-sync/SETUP.md](json-auto-sync/SETUP.md)

### Option B: Manual Export
Best for: One-off exports, different time ranges, no GitHub needed.

→ [json-manual/SETUP.md](json-manual/SETUP.md)

---

## Shared Script

Both methods use the same `sync.py` script:

```bash
# Manual local export
python sync.py --output latest.json

# Push to GitHub
python sync.py

# Different time range
python sync.py --days 90 --output 90days.json
```

See individual SETUP.md files for detailed instructions.

---

## Data Output

All methods produce the same JSON structure compatible with Section 11 protocol:

```
latest.json
├── READ_THIS_FIRST      → AI instructions + quick stats
├── metadata             → Timestamps, version
├── summary              → Activity breakdown by type
├── current_status       → FTP, CTL, ATL, TSB, HRV, weight
├── recent_activities    → Detailed activity data
├── wellness_data        → Daily HRV, RHR, sleep, fatigue
├── planned_workouts     → Upcoming scheduled sessions
└── weekly_summary       → Aggregated totals
```
