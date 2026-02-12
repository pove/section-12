# Data Mirror Setup Guide

This guide explains how to create an automated JSON data mirror of your Intervals.icu training data for use with AI coaching systems.

---

## Overview

The data mirror automatically syncs your Intervals.icu metrics to a GitHub repository every 15 minutes, producing a static JSON URL that AI systems can read.

**Result:**
- `https://raw.githubusercontent.com/[you]/[repo]/main/latest.json`
- `https://raw.githubusercontent.com/[you]/[repo]/main/history.json`

---

## Prerequisites

- [Intervals.icu](https://intervals.icu) account with training data
- [GitHub](https://github.com) account
- 10 minutes for setup

---

## Step 1: Get Your Intervals.icu Credentials

1. Log in to [Intervals.icu](https://intervals.icu)
2. Go to **Settings** (gear icon)
3. Find your **Athlete ID** (shown in URL, e.g., `i123456`)
4. Scroll to **API Key** section → Generate or copy your API key

Save both values securely.

---

## Step 2: Create GitHub Repository

1. Go to [github.com/new](https://github.com/new)
2. Name it something like `training-data` or `t1-data`
3. Set to **Public** (required for AI access) or Private (requires token sharing)
4. Check **Add a README file**
5. Click **Create repository**

---

## Step 3: Add Files to Repository

Upload these files to your repository:

| File | Location | Description |
|------|----------|-------------|
| `sync.py` | Root | The sync script |
| `auto-sync.yml` | `.github/workflows/` | GitHub Actions workflow |
| `DATA_REPO_README.md` | Root (rename to `README.md`) | README template with sync badge |

**To create the workflow folder:**
1. Click "Add file" → "Create new file"
2. Name it: `.github/workflows/auto-sync.yml`
3. Paste the workflow content
4. Commit

---

## Step 4: Add Repository Secrets

1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `ATHLETE_ID` | Your Intervals.icu athlete ID (e.g., `i123456`) |
| `INTERVALS_KEY` | Your Intervals.icu API key |

**Note:** `GITHUB_TOKEN` is provided automatically by GitHub Actions.

---

## Step 5: Enable GitHub Actions

1. Go to your repo → **Actions** tab
2. If prompted, enable workflows
3. Click on "Auto-Sync Intervals.icu Data" workflow
4. Click **Run workflow** → **Run workflow** (green button)

Wait 30-60 seconds, then check if `latest.json` has been updated.

---

## Step 6: Verify

Your data should now be accessible at:
```
https://raw.githubusercontent.com/[your-username]/[repo-name]/main/latest.json
https://raw.githubusercontent.com/[your-username]/[repo-name]/main/history.json
```

Test by opening the latest.json URL in your browser — you should see your training data as JSON. `history.json` is generated automatically on first run with tiered granularity: daily (90 days), weekly (180 days), and monthly (up to 3 years).

---

## FTP History Tracking

The sync script automatically creates and maintains `ftp_history.json` to track FTP changes over time. This enables **Benchmark Index** calculation.

### File Structure

```json
{
  "indoor": {
    "2025-10-15": 255,
    "2025-11-03": 260,
    "2025-12-01": 265
  },
  "outdoor": {
    "2025-10-15": 265,
    "2025-11-03": 270,
    "2025-12-01": 278
  }
}
```

### How It Works

- Created automatically on first run of `sync.py`
- New entries added only when FTP **changes** (not on every run)
- Indoor and outdoor FTP tracked separately
- GitHub Actions workflow commits updates automatically

### Benchmark Index

The Benchmark Index measures FTP progression over 8 weeks:

```
Benchmark Index = (Current FTP - FTP 8 weeks ago) / FTP 8 weeks ago
```

| Value | Interpretation |
|-------|----------------|
| +5% | Strong progression |
| +2% to +5% | Normal build phase gains |
| 0% to +2% | Maintenance |
| -2% to 0% | Minor regression (may be normal in recovery) |
| < -2% | Significant regression — investigate |

**Note:** Requires ~8 weeks of data before Benchmark Index becomes available.

---

## Usage with AI

Once set up, configure your AI platform using the instructions in the main [README](../README.md#quick-start).

Your JSON URLs:
```
https://raw.githubusercontent.com/[your-username]/[repo-name]/main/latest.json
https://raw.githubusercontent.com/[your-username]/[repo-name]/main/history.json
```

Provide both URLs to your AI coach — `latest.json` has the current 7-day snapshot and `history.json` provides longitudinal context for trend analysis.

---

## Troubleshooting

### Workflow fails with "secret not set"
- Check secret names match exactly: `ATHLETE_ID`, `INTERVALS_KEY`
- Secrets are case-sensitive

### Workflow fails with "untracked working tree files would be overwritten"
- This happens when generated files (like `history.json`) conflict during `git pull`
- Use the latest `auto-sync.yml` from this folder — it stages all generated files before pulling

### No data in latest.json
- Verify your Intervals.icu API key is valid
- Check you have activities in the last 7 days
- Look at workflow logs for specific errors

### ftp_history.json not updating on GitHub
- Ensure your `auto-sync.yml` includes `git add ftp_history.json`
- Check workflow logs for git errors

### history.json not generated
- History generates automatically on first run, then regenerates when outdated
- Delete `history.json` from your repo and re-run to force regeneration
- Check workflow logs — history generation is non-critical and won't fail the sync

### 404 error on JSON URL
- Ensure `latest.json` exists in repo root
- Check repo is public
- Verify URL format (use `main` not `master`)

---

## Customization

**Change sync frequency:**
Edit the cron schedule in `auto-sync.yml`:
- Every 15 min: `*/15 * * * *`
- Every hour: `0 * * * *`
- Every 6 hours: `0 */6 * * *`

**Change data range:**
Edit `sync.py` call in workflow:
```yaml
run: python sync.py --days 14
```

**Disable anonymization:**
```yaml
run: python sync.py --no-anonymize
```

---

## Privacy Notes

By default, the script anonymizes your data:
- Athlete ID → "REDACTED"
- Outdoor activity names → "Training Session"
- Activity IDs → Generic `activity_1`, `activity_2`, etc.

Indoor/virtual ride names are preserved for workout identification.

For additional privacy, use a separate GitHub account for your data repository.

---

## Files Reference

| File | Purpose | Auto-created |
|------|---------|--------------|
| `latest.json` | Current 7-day training data for AI consumption | Yes |
| `history.json` | Longitudinal data — daily (90d), weekly (180d), monthly (3y) | Yes |
| `ftp_history.json` | FTP progression tracking for Benchmark Index | Yes |
| `archive/` | Timestamped snapshots of each sync | Yes |

## Update Notifications

The sync script automatically checks for upstream protocol updates from the [Section 11 repository](https://github.com/CrankAddict/section-11). When a new version is available, a GitHub Issue is created in your data repo with a summary of changes. No action is needed — just watch your Issues tab.
