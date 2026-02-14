#!/usr/bin/env python3
"""
Intervals.icu → GitHub/Local JSON Export
Exports training data for LLM access.
Supports both automated GitHub sync and manual local export.

Version 3.3.0 - Alerts, History & Notifications
  - Graduated alerts array with flag/alarm severity and persistence tracking
  - Monotony deload context detection (suppresses false positives)
  - Auto-generated history.json with tiered granularity (90d daily, 180d weekly, 1-3y monthly)
  - History confidence scoring in latest.json output
  - Upstream update notifications via GitHub Issues
  - Smart fitness metrics: CTL/ATL/TSB/ramp_rate all use API values when today's
    workouts are completed, decayed yesterday values when planned but not yet done
  - Uses API data for eFTP, W', P-max, VO2max (from wellness endpoint)
  - Tracks indoor and outdoor FTP separately for Benchmark Index
  - Calculates ACWR, Monotony, Strain, Recovery Index locally
"""

import requests
import json
import os
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import base64
import math
import statistics
from collections import defaultdict
from pathlib import Path


class IntervalsSync:
    """Sync Intervals.icu data to GitHub repository or local file"""
    
    INTERVALS_BASE_URL = "https://intervals.icu/api/v1"
    GITHUB_API_URL = "https://api.github.com"
    FTP_HISTORY_FILE = "ftp_history.json"
    HISTORY_FILE = "history.json"
    UPSTREAM_REPO = "CrankAddict/section-11"
    CHANGELOG_FILE = "changelog.json"
    VERSION = "3.3.2"
    
    def __init__(self, athlete_id: str, intervals_api_key: str, github_token: str = None, 
                 github_repo: str = None, debug: bool = False):
        self.athlete_id = athlete_id
        self.intervals_auth = base64.b64encode(f"API_KEY:{intervals_api_key}".encode()).decode()
        self.github_token = github_token
        self.github_repo = github_repo
        self.debug = debug
        self.script_dir = Path(__file__).parent
    
    def _intervals_get(self, endpoint: str, params: Dict = None) -> Dict:
        """Fetch from Intervals.icu API"""
        if endpoint:
            url = f"{self.INTERVALS_BASE_URL}/athlete/{self.athlete_id}/{endpoint}"
        else:
            url = f"{self.INTERVALS_BASE_URL}/athlete/{self.athlete_id}"
        headers = {
            "Authorization": f"Basic {self.intervals_auth}",
            "Accept": "application/json"
        }
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    
    def _fetch_today_wellness(self) -> Dict:
        """
        Fetch today's wellness data which contains:
        - CTL, ATL, rampRate (but these include planned workouts!)
        - sportInfo with eFTP, W', P-max (accurate live estimates)
        - VO2max, sleep quality/hours, etc.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            data = self._intervals_get(f"wellness/{today}")
            return data
        except Exception as e:
            if self.debug:
                print(f"  Could not fetch today's wellness: {e}")
            return {}
    
    def _extract_power_model_from_wellness(self, wellness_data: Dict) -> Dict:
        """
        Extract eFTP, W', P-max from wellness.sportInfo.
        These are the accurate live estimates that match the Intervals.icu UI.
        """
        sport_info = wellness_data.get("sportInfo", [])
        
        # Find cycling sport info
        cycling_info = None
        for sport in sport_info:
            if sport.get("type") == "Ride":
                cycling_info = sport
                break
        
        if not cycling_info:
            return {
                "eftp": None,
                "w_prime": None,
                "w_prime_kj": None,
                "p_max": None,
                "source": "unavailable"
            }
        
        eftp = cycling_info.get("eftp")
        w_prime = cycling_info.get("wPrime")
        p_max = cycling_info.get("pMax")
        
        if self.debug and eftp:
            print(f"  eFTP: {round(eftp)}W, W': {round(w_prime) if w_prime else 'N/A'}J, P-max: {round(p_max) if p_max else 'N/A'}W")
        
        return {
            "eftp": round(eftp, 1) if eftp else None,
            "w_prime": round(w_prime) if w_prime else None,
            "w_prime_kj": round(w_prime / 1000, 1) if w_prime else None,
            "p_max": round(p_max) if p_max else None,
            "source": "wellness.sportInfo"
        }
    
    def _load_ftp_history(self) -> Dict[str, Dict[str, int]]:
        """
        Load FTP history from local JSON file.
        
        Returns dict with structure:
        {
            "indoor": {"2026-01-01": 270, "2026-02-01": 275},
            "outdoor": {"2026-01-01": 280, "2026-02-01": 287}
        }
        """
        ftp_history_path = self.script_dir / self.FTP_HISTORY_FILE
        
        if ftp_history_path.exists():
            try:
                with open(ftp_history_path, 'r') as f:
                    data = json.load(f)
                    # Handle legacy format (flat dict) -> convert to new format
                    if data and not ("indoor" in data or "outdoor" in data):
                        if self.debug:
                            print(f"  Converting legacy FTP history format...")
                        return {"indoor": {}, "outdoor": data}
                    return data
            except Exception as e:
                if self.debug:
                    print(f"  Could not load FTP history: {e}")
                return {"indoor": {}, "outdoor": {}}
        return {"indoor": {}, "outdoor": {}}
    
    def _save_ftp_history(self, history: Dict[str, Dict[str, int]], 
                          current_ftp_indoor: int, current_ftp_outdoor: int) -> Dict[str, Dict[str, int]]:
        """
        Save current FTPs to history file.
        Tracks indoor and outdoor FTP separately.
        Only adds entry if FTP changed from most recent entry.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Ensure structure exists
        if "indoor" not in history:
            history["indoor"] = {}
        if "outdoor" not in history:
            history["outdoor"] = {}
        
        # Update indoor FTP if changed
        if current_ftp_indoor:
            indoor_history = history["indoor"]
            if indoor_history:
                sorted_dates = sorted(indoor_history.keys(), reverse=True)
                most_recent = indoor_history[sorted_dates[0]]
                if current_ftp_indoor != most_recent:
                    history["indoor"][today] = current_ftp_indoor
                    if self.debug:
                        print(f"  Indoor FTP changed: {most_recent} → {current_ftp_indoor}")
            else:
                history["indoor"][today] = current_ftp_indoor
                if self.debug:
                    print(f"  Indoor FTP recorded: {current_ftp_indoor}")
        
        # Update outdoor FTP if changed
        if current_ftp_outdoor:
            outdoor_history = history["outdoor"]
            if outdoor_history:
                sorted_dates = sorted(outdoor_history.keys(), reverse=True)
                most_recent = outdoor_history[sorted_dates[0]]
                if current_ftp_outdoor != most_recent:
                    history["outdoor"][today] = current_ftp_outdoor
                    if self.debug:
                        print(f"  Outdoor FTP changed: {most_recent} → {current_ftp_outdoor}")
            else:
                history["outdoor"][today] = current_ftp_outdoor
                if self.debug:
                    print(f"  Outdoor FTP recorded: {current_ftp_outdoor}")
        
        # Save to file
        ftp_history_path = self.script_dir / self.FTP_HISTORY_FILE
        try:
            with open(ftp_history_path, 'w') as f:
                json.dump(history, f, indent=2, sort_keys=True)
            if self.debug:
                print(f"  FTP history saved to {ftp_history_path}")
        except Exception as e:
            if self.debug:
                print(f"  Could not save FTP history: {e}")
        
        return history
    
    def _calculate_benchmark_index(self, current_ftp: int, ftp_history: Dict[str, int], 
                                    ftp_type: str = "indoor") -> Tuple[Optional[float], Optional[int]]:
        """
        Calculate Benchmark Index = (FTP_current / FTP_8_weeks_ago) - 1
        
        Returns (benchmark_index, ftp_8_weeks_ago)
        """
        if not current_ftp or not ftp_history:
            return None, None
        
        # Find FTP from ~8 weeks ago (56 days, with ±7 day tolerance)
        target_date = datetime.now() - timedelta(days=56)
        earliest_acceptable = target_date - timedelta(days=7)
        latest_acceptable = target_date + timedelta(days=7)
        
        # Find the closest FTP entry to 8 weeks ago
        best_match_date = None
        best_match_diff = float('inf')
        
        for date_str, ftp in ftp_history.items():
            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d")
                
                if earliest_acceptable <= entry_date <= latest_acceptable:
                    diff = abs((entry_date - target_date).days)
                    if diff < best_match_diff:
                        best_match_diff = diff
                        best_match_date = date_str
            except:
                continue
        
        if best_match_date:
            ftp_8_weeks_ago = ftp_history[best_match_date]
            benchmark_index = round((current_ftp / ftp_8_weeks_ago) - 1, 3)
            
            if self.debug:
                print(f"  Benchmark Index ({ftp_type}): {benchmark_index:+.1%} (FTP {ftp_8_weeks_ago} → {current_ftp})")
            
            return benchmark_index, ftp_8_weeks_ago
        
        # No data from 8 weeks ago
        if self.debug:
            sorted_dates = sorted(ftp_history.keys())
            if sorted_dates:
                oldest_date = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
                days_of_history = (datetime.now() - oldest_date).days
                print(f"  Benchmark Index ({ftp_type}) unavailable: only {days_of_history} days of history (need ~56)")
        
        return None, None
    
    def collect_training_data(self, days_back: int = 7, anonymize: bool = False) -> Dict:
        """Collect all training data for LLM analysis"""
        # Extended range for ACWR calculation (need 28 days minimum)
        days_for_acwr = 28
        oldest_extended = (datetime.now() - timedelta(days=days_for_acwr - 1)).strftime("%Y-%m-%d")
        oldest_display = (datetime.now() - timedelta(days=days_back - 1)).strftime("%Y-%m-%d")
        newest = datetime.now().strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        
        print("Fetching athlete data...")
        athlete = self._intervals_get("")
        
        cycling_settings = None
        if athlete.get("sportSettings"):
            for sport in athlete["sportSettings"]:
                if "Ride" in sport.get("types", []) or "VirtualRide" in sport.get("types", []):
                    cycling_settings = sport
                    break
        
        # Fetch extended activity range for ACWR
        print(f"Fetching activities (extended {days_for_acwr} days for ACWR)...")
        activities_extended = self._intervals_get("activities", {"oldest": oldest_extended, "newest": newest})
        
        # Filter to display range for recent_activities
        activities_display = [a for a in activities_extended 
                             if a.get("start_date_local", "")[:10] >= oldest_display]
        
        print("Fetching wellness data...")
        wellness = self._intervals_get("wellness", {"oldest": oldest_display, "newest": newest})
        
        # Extended wellness for baselines (use full 28 days if available)
        wellness_extended = self._intervals_get("wellness", {"oldest": oldest_extended, "newest": newest})
        
        # Fetch today's wellness for live estimates (eFTP, W', P-max, VO2max, etc.)
        print("Fetching today's wellness (eFTP, W', P-max, VO2max)...")
        today_wellness = self._fetch_today_wellness()
        
        # Extract power model from wellness (accurate live estimates)
        power_model = self._extract_power_model_from_wellness(today_wellness)
        
        # Extract additional metrics from today's wellness
        vo2max = today_wellness.get("vo2max")
        
        # Get API values for fitness metrics (these include planned workouts!)
        api_ctl = today_wellness.get("ctl")
        api_atl = today_wellness.get("atl")
        api_ramp_rate = today_wellness.get("rampRate")
        
        # Fetch yesterday's wellness for decay fallback
        print("Fetching fitness metrics...")
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday_wellness = self._intervals_get("wellness", {"oldest": yesterday, "newest": yesterday})
            yesterday_data = yesterday_wellness[0] if yesterday_wellness else {}
            
            # PMC decay constants
            ctl_decay = math.exp(-1/42)  # ~0.9765
            atl_decay = math.exp(-1/7)   # ~0.8668
            
            yesterday_ctl = yesterday_data.get("ctl")
            yesterday_atl = yesterday_data.get("atl")
            yesterday_ramp = yesterday_data.get("rampRate")
            
            # Decayed values = what fitness looks like with zero training today
            decayed_ctl = round(yesterday_ctl * ctl_decay, 2) if yesterday_ctl else None
            decayed_atl = round(yesterday_atl * atl_decay, 2) if yesterday_atl else None
            decayed_ramp = round(yesterday_ramp * ctl_decay, 2) if yesterday_ramp else None
        except:
            decayed_ctl = None
            decayed_atl = None
            decayed_ramp = None
            yesterday_ramp = None
        
        latest_wellness = wellness[-1] if wellness else {}
        
        # Fetch planned workouts (EXTENDED: include past 7 days for Consistency Index)
        print("Fetching planned workouts (past + future for Consistency Index)...")
        oldest_events = (datetime.now() - timedelta(days=days_back - 1)).strftime("%Y-%m-%d")
        newest_ahead = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")
        events = self._intervals_get("events", {"oldest": oldest_events, "newest": newest_ahead})
        
        # Split events into past (for consistency) and future (for display)
        past_events = [e for e in events if e.get("start_date_local", "")[:10] <= today]
        future_events = [e for e in events if e.get("start_date_local", "")[:10] >= today]
        
        # Smart fitness metrics: same logic for CTL, ATL, TSB, and ramp rate
        # API values include planned workouts → inflated if not yet completed
        # Decayed values = yesterday × decay → accurate baseline before any training today
        todays_planned = [e for e in events if e.get("start_date_local", "")[:10] == today]
        todays_activities = [a for a in activities_display if a.get("start_date_local", "")[:10] == today]
        
        if todays_planned and not todays_activities:
            # Planned workouts exist but nothing completed → decay (API values are inflated)
            ctl = decayed_ctl
            atl = decayed_atl
            smart_ramp_rate = decayed_ramp if decayed_ramp else api_ramp_rate
            fitness_source = "Decayed from yesterday (today's planned workouts not yet completed)"
        else:
            # No planned workouts OR workouts completed → API values are accurate
            ctl = round(api_ctl, 2) if api_ctl else decayed_ctl
            atl = round(api_atl, 2) if api_atl else decayed_atl
            smart_ramp_rate = round(api_ramp_rate, 2) if api_ramp_rate else decayed_ramp
            fitness_source = "From Intervals.icu API (reflects completed workouts)"
        
        tsb = round(ctl - atl, 2) if (ctl is not None and atl is not None) else None
        
        # Get both FTP values (user-set, not estimated)
        current_ftp_indoor = cycling_settings.get("indoor_ftp") if cycling_settings else None
        current_ftp_outdoor = cycling_settings.get("ftp") if cycling_settings else None
        
        # Load and update FTP history (tracks both indoor and outdoor)
        print("Updating FTP history...")
        ftp_history = self._load_ftp_history()
        ftp_history = self._save_ftp_history(ftp_history, current_ftp_indoor, current_ftp_outdoor)
        
        # Calculate Benchmark Index for both
        benchmark_index_indoor, ftp_8_weeks_ago_indoor = self._calculate_benchmark_index(
            current_ftp_indoor, ftp_history.get("indoor", {}), "indoor"
        )
        benchmark_index_outdoor, ftp_8_weeks_ago_outdoor = self._calculate_benchmark_index(
            current_ftp_outdoor, ftp_history.get("outdoor", {}), "outdoor"
        )
        
        # Calculate derived metrics for Section 11 compliance
        print("Calculating derived metrics...")
        derived_metrics = self._calculate_derived_metrics(
            activities_7d=activities_display,
            activities_28d=activities_extended,
            wellness_7d=wellness,
            wellness_extended=wellness_extended,
            current_ctl=ctl,
            current_atl=atl,
            current_tsb=tsb,
            past_events=past_events,
            activities_for_consistency=activities_display,
            power_model=power_model,
            benchmark_indoor=(benchmark_index_indoor, ftp_8_weeks_ago_indoor, current_ftp_indoor),
            benchmark_outdoor=(benchmark_index_outdoor, ftp_8_weeks_ago_outdoor, current_ftp_outdoor),
            vo2max=vo2max
        )
        
        # Generate alerts array (v3.3.0)
        print("Evaluating alert thresholds...")
        alerts = self._generate_alerts(
            derived_metrics=derived_metrics,
            wellness_7d=wellness,
            tss_7d_total=derived_metrics.get("tss_7d_total", 0),
            tss_28d_total=derived_metrics.get("tss_28d_total", 0)
        )
        
        if alerts:
            alarm_count = sum(1 for a in alerts if a["severity"] == "alarm")
            warning_count = sum(1 for a in alerts if a["severity"] == "warning")
            print(f"  ⚠️  {len(alerts)} alerts: {alarm_count} alarm, {warning_count} warning")
        else:
            print("  ✅ No alerts — green light")
        
        # History confidence (v3.3.0)
        history_info = self._get_history_confidence()
        
        data = {
            "READ_THIS_FIRST": {
                "instruction_for_ai": "DO NOT calculate totals from individual activities. Use the pre-calculated values in 'summary', 'weekly_summary', and 'derived_metrics' sections below. These are already computed accurately from the API data.",
                "data_period": f"Last {days_back} days (including today)",
                "extended_data_note": f"ACWR and baselines calculated from {days_for_acwr} days of data",
                "quick_stats": {
                    "total_training_hours": round(sum(act.get("moving_time", 0) for act in activities_display) / 3600, 2),
                    "total_activities": len(activities_display),
                    "total_tss": round(sum(act.get("icu_training_load", 0) for act in activities_display if act.get("icu_training_load")), 0)
                }
            },
            "metadata": {
                "athlete_id": "REDACTED" if anonymize else self.athlete_id,
                "last_updated": datetime.now().isoformat(),
                "data_range_days": days_back,
                "extended_range_days": days_for_acwr,
                "version": self.VERSION
            },
            "alerts": alerts,
            "history": history_info,
            "summary": self._compute_activity_summary(activities_display, days_back),
            "current_status": {
                "fitness": {
                    "ctl": ctl,
                    "atl": atl,
                    "tsb": tsb,
                    "ramp_rate": smart_ramp_rate,
                    "fitness_source": fitness_source
                },
                "thresholds": {
                    "ftp_outdoor": current_ftp_outdoor,
                    "ftp_indoor": current_ftp_indoor,
                    "eftp": power_model.get("eftp"),
                    "lthr": cycling_settings.get("lthr") if cycling_settings else None,
                    "max_hr": cycling_settings.get("max_hr") if cycling_settings else None,
                    "w_prime": power_model.get("w_prime"),
                    "w_prime_kj": power_model.get("w_prime_kj"),
                    "p_max": power_model.get("p_max"),
                    "vo2max": vo2max
                },
                "current_metrics": {
                    "weight_kg": latest_wellness.get("weight") or athlete.get("icu_weight"),
                    "resting_hr": latest_wellness.get("restingHR") or athlete.get("icu_resting_hr"),
                    "hrv": latest_wellness.get("hrv"),
                    "sleep_quality": latest_wellness.get("sleepQuality"),
                    "sleep_hours": round(latest_wellness.get("sleepSecs", 0) / 3600, 2) if latest_wellness.get("sleepSecs") else None
                }
            },
            "derived_metrics": derived_metrics,
            "recent_activities": self._format_activities(activities_display, anonymize),
            "wellness_data": self._format_wellness(wellness),
            "planned_workouts": self._format_events(future_events, anonymize),
            "weekly_summary": self._compute_weekly_summary(activities_display, wellness)
        }
        
        return data
    
    def _calculate_derived_metrics(self, activities_7d: List[Dict], activities_28d: List[Dict],
                                    wellness_7d: List[Dict], wellness_extended: List[Dict],
                                    current_ctl: float, current_atl: float, current_tsb: float,
                                    past_events: List[Dict], activities_for_consistency: List[Dict],
                                    power_model: Dict,
                                    benchmark_indoor: Tuple[Optional[float], Optional[int], Optional[int]],
                                    benchmark_outdoor: Tuple[Optional[float], Optional[int], Optional[int]],
                                    vo2max: float) -> Dict:
        """
        Calculate Section 11 derived metrics.
        
        Tier 1 (Primary): RI, baselines
        Tier 2 (Secondary): ACWR, Monotony, Strain, Stress Tolerance, Load-Recovery Ratio
        Tier 3 (Tertiary): Zone distribution, Polarisation, Phase Detection, Consistency, Benchmark
        
        Args:
            benchmark_indoor: (benchmark_index, ftp_8_weeks_ago, current_ftp) for indoor
            benchmark_outdoor: (benchmark_index, ftp_8_weeks_ago, current_ftp) for outdoor
        """
        
        # Unpack benchmark tuples
        benchmark_index_indoor, ftp_8_weeks_ago_indoor, current_ftp_indoor = benchmark_indoor
        benchmark_index_outdoor, ftp_8_weeks_ago_outdoor, current_ftp_outdoor = benchmark_outdoor
        
        # === DAILY TSS AGGREGATION ===
        daily_tss_7d = self._get_daily_tss(activities_7d, days=7)
        daily_tss_28d = self._get_daily_tss(activities_28d, days=28)
        
        tss_7d_total = sum(daily_tss_7d)
        tss_28d_total = sum(daily_tss_28d)
        
        # === ACWR (Acute:Chronic Workload Ratio) ===
        # Formula: (7-day avg TSS) / (28-day avg TSS)
        # Reference: Gabbett (2016) - "sweet spot" is 0.8-1.3
        acute_load = tss_7d_total / 7 if tss_7d_total else 0
        chronic_load = tss_28d_total / 28 if tss_28d_total else 0
        acwr = round(acute_load / chronic_load, 2) if chronic_load > 0 else None
        
        # === MONOTONY ===
        # Formula: mean(daily_tss) / stdev(daily_tss)
        # Reference: Foster (1998) - values >2.0 indicate increased illness risk
        if len(daily_tss_7d) > 1 and any(daily_tss_7d):
            mean_tss = statistics.mean(daily_tss_7d)
            try:
                stdev_tss = statistics.stdev(daily_tss_7d)
                monotony = round(mean_tss / stdev_tss, 2) if stdev_tss > 0 else None
            except:
                monotony = None
        else:
            monotony = None
            mean_tss = 0
        
        # === STRAIN ===
        # Formula: 7-day total TSS × Monotony
        # Reference: Foster (1998) - values >3500-4000 associated with overtraining
        strain = round(tss_7d_total * monotony, 0) if monotony else None
        
        # === BASELINES (7-day and extended) ===
        hrv_values_7d = [w.get("hrv") for w in wellness_7d if w.get("hrv")]
        rhr_values_7d = [w.get("restingHR") for w in wellness_7d if w.get("restingHR")]
        
        hrv_baseline_7d = round(statistics.mean(hrv_values_7d), 1) if hrv_values_7d else None
        rhr_baseline_7d = round(statistics.mean(rhr_values_7d), 1) if rhr_values_7d else None
        
        # Extended baselines (for more stable reference)
        hrv_values_ext = [w.get("hrv") for w in wellness_extended if w.get("hrv")]
        rhr_values_ext = [w.get("restingHR") for w in wellness_extended if w.get("restingHR")]
        
        hrv_baseline_28d = round(statistics.mean(hrv_values_ext), 1) if hrv_values_ext else None
        rhr_baseline_28d = round(statistics.mean(rhr_values_ext), 1) if rhr_values_ext else None
        
        # === RECOVERY INDEX (RI) ===
        # Formula: (HRV_today / HRV_baseline) ÷ (RHR_today / RHR_baseline)
        # Interpretation: >1.0 = good recovery, <1.0 = poor recovery
        latest_hrv = wellness_7d[-1].get("hrv") if wellness_7d else None
        latest_rhr = wellness_7d[-1].get("restingHR") if wellness_7d else None
        
        if latest_hrv and latest_rhr and hrv_baseline_7d and rhr_baseline_7d:
            hrv_ratio = latest_hrv / hrv_baseline_7d
            rhr_ratio = latest_rhr / rhr_baseline_7d
            ri = round(hrv_ratio / rhr_ratio, 2) if rhr_ratio > 0 else None
        else:
            ri = None
        
        # === STRESS TOLERANCE ===
        # Formula: (Strain ÷ Monotony) ÷ 100
        stress_tolerance = round((strain / monotony) / 100, 1) if strain and monotony else None
        
        # === LOAD-RECOVERY RATIO ===
        # Formula: 7-day Load ÷ (RI × 100)
        load_recovery_ratio = round(tss_7d_total / (ri * 100), 1) if ri and ri > 0 else None
        
        # === ZONE AGGREGATION ===
        zone_totals = self._aggregate_zones(activities_7d)
        
        total_zone_time = zone_totals["total_time"]
        z1_time = zone_totals["z1_time"]
        z2_time = zone_totals["z2_time"]
        z3_time = zone_totals["z3_time"]
        z4_plus_time = zone_totals["z4_plus_time"]
        
        # === GREY ZONE PERCENTAGE (Z3 - to be minimized in polarized training) ===
        # Reference: Seiler - "too much pain for too little gain"
        grey_zone_percentage = round((z3_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None
        
        # === QUALITY INTENSITY PERCENTAGE (Z4+ per Seiler's model) ===
        # Reference: Seiler's Zone 3 = above LT2 = Z4+ in 7-zone model
        # This is the "hard" work that should be ~20% in polarized training
        quality_intensity_percentage = round((z4_plus_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None
        
        # === POLARISATION INDEX ===
        # Formula: (Z1 + Z2) / Total - measures how much time is "easy"
        # Target: ~80% for polarized training
        polarisation_index = round((z1_time + z2_time) / total_zone_time, 2) if total_zone_time > 0 else None
        
        # === CONSISTENCY INDEX ===
        consistency_index, consistency_details = self._calculate_consistency_index(
            activities_for_consistency, past_events
        )
        
        # === HARD DAYS THIS WEEK ===
        # Zone ladder with cumulative thresholds (z+ = zone + all above)
        # z3+ >= 1800s, z4+ >= 600s, z5+ >= 300s, z6+ >= 120s, z7 >= 60s
        # Per Seiler's polarized model + Foster's session RPE scaling
        hard_days_this_week = 0
        activities_by_date_7d = {}
        for a in activities_7d:
            a_date = a.get("start_date_local", "")[:10]
            if a_date not in activities_by_date_7d:
                activities_by_date_7d[a_date] = []
            activities_by_date_7d[a_date].append(a)
        
        for date_str, day_acts in activities_by_date_7d.items():
            day_z3 = 0
            day_z4 = 0
            day_z5 = 0
            day_z6 = 0
            day_z7 = 0
            for a in day_acts:
                icu_zone_times = a.get("icu_zone_times", [])
                if icu_zone_times:
                    for zone in icu_zone_times:
                        zid = zone.get("id", "").lower()
                        secs = zone.get("secs", 0)
                        if zid == "z3":
                            day_z3 += secs
                        elif zid == "z4":
                            day_z4 += secs
                        elif zid == "z5":
                            day_z5 += secs
                        elif zid == "z6":
                            day_z6 += secs
                        elif zid == "z7":
                            day_z7 += secs
            # Zone ladder: cumulative thresholds (z+ = zone + all above)
            # Per Seiler's polarized model + Foster's session RPE scaling
            is_hard = (
                (day_z3 + day_z4 + day_z5 + day_z6 + day_z7) >= 1800 or  # z3+: 30 min tempo+
                (day_z4 + day_z5 + day_z6 + day_z7) >= 600 or            # z4+: 10 min threshold+
                (day_z5 + day_z6 + day_z7) >= 300 or                      # z5+: 5 min VO2max+
                (day_z6 + day_z7) >= 120 or                                # z6+: 2 min anaerobic+
                day_z7 >= 60                                                # z7:  1 min neuromuscular
            )
            if is_hard:
                hard_days_this_week += 1
        
        # === PHASE DETECTION ===
        phase_detected, phase_triggers = self._detect_phase(
            acwr=acwr,
            ri=ri,
            quality_intensity_pct=quality_intensity_percentage,
            hard_days_per_week=hard_days_this_week,
            strain=strain,
            monotony=monotony,
            tsb=current_tsb,
            ctl=current_ctl
        )
        
        # === SEASONAL CONTEXT ===
        seasonal_context = self._determine_seasonal_context()
        
        # === BENCHMARK SEASONAL EXPECTATION ===
        benchmark_expected_indoor = self._is_benchmark_expected(benchmark_index_indoor, seasonal_context)
        benchmark_expected_outdoor = self._is_benchmark_expected(benchmark_index_outdoor, seasonal_context)
        
        return {
            # Tier 1: Primary Readiness
            "recovery_index": ri,
            "hrv_baseline_7d": hrv_baseline_7d,
            "rhr_baseline_7d": rhr_baseline_7d,
            "hrv_baseline_28d": hrv_baseline_28d,
            "rhr_baseline_28d": rhr_baseline_28d,
            "latest_hrv": latest_hrv,
            "latest_rhr": latest_rhr,
            
            # Tier 2: Secondary Load Metrics
            "acwr": acwr,
            "acwr_interpretation": self._interpret_acwr(acwr),
            "monotony": monotony,
            "monotony_interpretation": "elevated" if monotony and monotony > 2.0 else "normal" if monotony else None,
            "strain": strain,
            "stress_tolerance": stress_tolerance,
            "load_recovery_ratio": load_recovery_ratio,
            "tss_7d_total": round(tss_7d_total, 0),
            "tss_28d_total": round(tss_28d_total, 0),
            
            # Tier 3: Zone Distribution (Seiler's Polarized Model)
            "zone_distribution_7d": {
                "z1_hours": round(z1_time / 3600, 2),
                "z2_hours": round(z2_time / 3600, 2),
                "z3_hours": round(z3_time / 3600, 2),
                "z4_plus_hours": round(z4_plus_time / 3600, 2),
                "total_hours": round(total_zone_time / 3600, 2)
            },
            "grey_zone_percentage": grey_zone_percentage,
            "grey_zone_note": "Gray Zone % (Z3/tempo) - minimize in polarized training",
            "quality_intensity_percentage": quality_intensity_percentage,
            "quality_intensity_note": "Quality Intensity % (Z4+/threshold+) - target ~20% in polarized training",
            "polarisation_index": polarisation_index,
            "polarisation_note": "Easy time (Z1+Z2) / Total - target ~80% in polarized training",
            "hard_days_this_week": hard_days_this_week,
            "hard_days_note": "Zone ladder: z3+ >= 30min, z4+ >= 10min, z5+ >= 5min, z6+ >= 2min, z7 >= 1min. Cumulative thresholds per Seiler/Foster — higher zones need less time to qualify as hard",
            
            # Tier 3: Consistency & Compliance
            "consistency_index": consistency_index,
            "consistency_details": consistency_details,
            
            # Phase & Context
            "phase_detected": phase_detected,
            "phase_triggers": phase_triggers,
            "seasonal_context": seasonal_context,
            
            # Benchmark & FTP Progression (Indoor)
            "benchmark_indoor": {
                "current_ftp": current_ftp_indoor,
                "ftp_8_weeks_ago": ftp_8_weeks_ago_indoor,
                "benchmark_index": benchmark_index_indoor,
                "benchmark_percentage": f"{benchmark_index_indoor:+.1%}" if benchmark_index_indoor is not None else None,
                "seasonal_expected": benchmark_expected_indoor
            },
            # Benchmark & FTP Progression (Outdoor)
            "benchmark_outdoor": {
                "current_ftp": current_ftp_outdoor,
                "ftp_8_weeks_ago": ftp_8_weeks_ago_outdoor,
                "benchmark_index": benchmark_index_outdoor,
                "benchmark_percentage": f"{benchmark_index_outdoor:+.1%}" if benchmark_index_outdoor is not None else None,
                "seasonal_expected": benchmark_expected_outdoor
            },
            
            # Power Model (from API - accurate live estimates)
            "eftp": power_model.get("eftp"),
            "w_prime": power_model.get("w_prime"),
            "w_prime_kj": power_model.get("w_prime_kj"),
            "p_max": power_model.get("p_max"),
            "power_model_source": power_model.get("source"),
            
            # Additional wellness metrics (from API)
            "vo2max": vo2max,
            
            # Validation metadata
            "calculation_timestamp": datetime.now().isoformat(),
            "data_quality": {
                "hrv_data_points": len(hrv_values_7d),
                "rhr_data_points": len(rhr_values_7d),
                "activities_7d": len(activities_7d),
                "activities_28d": len(activities_28d),
                "planned_workouts_7d": len(past_events),
                "ftp_history_days": self._get_ftp_history_span()
            }
        }
    
    def _interpret_acwr(self, acwr: float) -> Optional[str]:
        """Interpret ACWR value per Gabbett guidelines"""
        if acwr is None:
            return None
        if acwr < 0.8:
            return "undertraining"
        elif acwr <= 1.3:
            return "optimal"
        elif acwr <= 1.5:
            return "caution"
        else:
            return "danger"
    
    def _calculate_consistency_index(self, activities: List[Dict], 
                                      past_events: List[Dict]) -> Tuple[Optional[float], Dict]:
        """
        Calculate Consistency Index = Completed Workout Days / Planned Workout Days
        
        Matches by date (not individual workouts) since multiple workouts can be planned per day.
        """
        # Get unique dates with planned workouts (only WORKOUT type)
        planned_dates = set()
        for event in past_events:
            if event.get("category") == "WORKOUT":
                date_str = event.get("start_date_local", "")[:10]
                if date_str:
                    planned_dates.add(date_str)
        
        # Get unique dates with completed activities (cycling only for fair comparison)
        completed_dates = set()
        cycling_types = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"}
        
        for activity in activities:
            if activity.get("type") in cycling_types:
                date_str = activity.get("start_date_local", "")[:10]
                if date_str:
                    completed_dates.add(date_str)
        
        # Calculate overlap
        matched_dates = planned_dates & completed_dates
        
        if not planned_dates:
            return None, {
                "planned_days": 0,
                "completed_days": len(completed_dates),
                "matched_days": 0,
                "note": "No planned workouts in period"
            }
        
        consistency_index = round(len(matched_dates) / len(planned_dates), 2)
        
        return consistency_index, {
            "planned_days": len(planned_dates),
            "completed_days": len(completed_dates),
            "matched_days": len(matched_dates),
            "planned_dates": sorted(list(planned_dates)),
            "completed_dates": sorted(list(completed_dates))
        }
    
    def _is_benchmark_expected(self, benchmark_index: Optional[float], 
                                seasonal_context: str) -> Optional[bool]:
        """
        Determine if the benchmark index is within expected range for the season.
        """
        if benchmark_index is None:
            return None
        
        expectations = {
            "Off-season / Transition": (-0.05, -0.02),
            "Early Base": (-0.02, 0.01),
            "Late Base / Build": (0.02, 0.05),
            "Build / Early Race Season": (0.01, 0.04),
            "Peak Race Season": (0.01, 0.03),
            "Late Season / Transition": (-0.03, 0.00),
        }
        
        if seasonal_context in expectations:
            low, high = expectations[seasonal_context]
            return low <= benchmark_index <= high
        
        return None
    
    def _get_ftp_history_span(self) -> Dict[str, int]:
        """Get the number of days of FTP history available for indoor and outdoor"""
        ftp_history = self._load_ftp_history()
        
        result = {"indoor": 0, "outdoor": 0}
        
        for ftp_type in ["indoor", "outdoor"]:
            history = ftp_history.get(ftp_type, {})
            if not history:
                continue
            
            sorted_dates = sorted(history.keys())
            if len(sorted_dates) < 2:
                continue
            
            try:
                oldest = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
                newest = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
                result[ftp_type] = (newest - oldest).days
            except:
                continue
        
        return result
    
    def _get_daily_tss(self, activities: List[Dict], days: int) -> List[float]:
        """Aggregate TSS by day for the specified number of days"""
        daily_tss = defaultdict(float)
        
        for act in activities:
            date_str = act.get("start_date_local", "")[:10]
            tss = act.get("icu_training_load") or 0
            daily_tss[date_str] += tss
        
        # Create array for last N days (including days with 0 TSS)
        result = []
        for i in range(days - 1, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            result.append(daily_tss.get(date, 0))
        
        return result
    
    def _aggregate_zones(self, activities: List[Dict]) -> Dict:
        """
        Aggregate zone times across all activities.
        
        Returns separate Z1, Z2, Z3, and Z4+ times for proper polarization analysis.
        Per Seiler's model:
        - Z1-Z2: Easy (below LT1)
        - Z3: Grey zone / Tempo (between LT1 and LT2) - to be minimized
        - Z4+: Hard / Quality (above LT2) - ~20% target
        """
        z1_time = 0
        z2_time = 0
        z3_time = 0
        z4_plus_time = 0
        total_time = 0
        
        for act in activities:
            zones = None
            
            # Check for zone data in raw activity
            icu_zone_times = act.get("icu_zone_times", [])
            icu_hr_zone_times = act.get("icu_hr_zone_times", [])
            
            # Power zones (preferred for cycling)
            if icu_zone_times:
                pz = {}
                for zone in icu_zone_times:
                    zone_id = zone.get("id", "").lower()
                    secs = zone.get("secs", 0)
                    if zone_id in ["z1", "z2", "z3", "z4", "z5", "z6", "z7"]:
                        pz[zone_id] = secs
                if pz:
                    zones = pz
            
            # HR zones (fallback)
            if not zones and icu_hr_zone_times:
                zone_labels = ["z1", "z2", "z3", "z4", "z5", "z6", "z7"]
                hz = {}
                for idx, secs in enumerate(icu_hr_zone_times):
                    if idx < len(zone_labels) and secs:
                        hz[zone_labels[idx]] = secs
                if hz:
                    zones = hz
            
            if zones:
                z1_time += zones.get("z1", 0)
                z2_time += zones.get("z2", 0)
                z3_time += zones.get("z3", 0)
                z4_plus_time += (zones.get("z4", 0) + zones.get("z5", 0) + 
                               zones.get("z6", 0) + zones.get("z7", 0))
                total_time += sum(zones.values())
        
        return {
            "z1_time": z1_time,
            "z2_time": z2_time,
            "z3_time": z3_time,
            "z4_plus_time": z4_plus_time,
            "total_time": total_time
        }
    
    def _detect_phase(self, acwr: float, ri: float, quality_intensity_pct: float,
                      hard_days_per_week: int,
                      strain: float, monotony: float, tsb: float, ctl: float) -> Tuple[str, List[str]]:
        """
        Detect current training phase based on Section 11 Phase Detection Criteria
        
        Uses both time-based quality intensity % AND session-based hard days/week.
        For high-volume athletes (10+ hrs/week), time-based metrics undercount intensity
        because hard sessions are diluted by volume. Session count provides the correction.
        """
        triggers = []
        
        # Check for Overreached first (safety)
        if acwr and acwr > 1.3:
            triggers.append(f"ACWR {acwr} > 1.3")
        if strain and strain > 3500:
            triggers.append(f"Strain {strain} > 3500")
        if ri and ri < 0.6:
            triggers.append(f"RI {ri} < 0.6")
        if monotony and monotony > 2.5:
            triggers.append(f"Monotony {monotony} > 2.5")
        
        if len(triggers) >= 2 or (ri and ri < 0.6):
            return "Overreached", triggers
        
        # Recovery phase
        if tsb and tsb > 10:
            triggers = [f"TSB {tsb} > +10"]
            return "Recovery", triggers
        
        # Taper phase
        if tsb and tsb > 0 and ctl:
            if 0 < tsb <= 10:
                triggers = [f"TSB {tsb} positive", "CTL stable/declining"]
                return "Taper", triggers
        
        # Build phase — by time OR by session count
        # High-volume athletes may show low quality % but still train 2+ hard days/week
        build_by_time = (quality_intensity_pct and 15 <= quality_intensity_pct <= 25)
        build_by_sessions = (hard_days_per_week >= 2)
        
        if acwr and 0.8 <= acwr <= 1.3:
            if build_by_time or build_by_sessions:
                triggers = [f"ACWR {acwr} in 0.8-1.3"]
                if build_by_time:
                    triggers.append(f"Quality Intensity {quality_intensity_pct}% in 15-25%")
                if build_by_sessions:
                    triggers.append(f"Hard days {hard_days_per_week}/week >= 2")
                return "Build", triggers
        
        # Base phase — low intensity by BOTH time and session count
        if acwr and 0.8 <= acwr < 1.0:
            triggers = [f"ACWR {acwr} in 0.8-1.0"]
            if quality_intensity_pct is not None:
                triggers.append(f"Quality Intensity {quality_intensity_pct}% < 15%")
            if hard_days_per_week is not None:
                triggers.append(f"Hard days {hard_days_per_week}/week <= 1")
            return "Base", triggers
        
        # Peak phase — high intensity with controlled load
        peak_by_time = (quality_intensity_pct and quality_intensity_pct > 20)
        peak_by_sessions = (hard_days_per_week >= 3)
        
        if acwr and acwr >= 1.0 and (peak_by_time or peak_by_sessions):
            triggers = [f"ACWR {acwr} >= 1.0"]
            if peak_by_time:
                triggers.append(f"Quality Intensity {quality_intensity_pct}% > 20%")
            if peak_by_sessions:
                triggers.append(f"Hard days {hard_days_per_week}/week >= 3")
            return "Peak", triggers
        
        return "Indeterminate", ["Insufficient data for phase detection"]
    
    def _determine_seasonal_context(self) -> str:
        """
        Determine seasonal context based on current month.
        Assumes Northern Hemisphere cycling calendar.
        """
        month = datetime.now().month
        
        if month in [11, 12]:
            return "Off-season / Transition"
        elif month in [1, 2]:
            return "Early Base"
        elif month in [3, 4]:
            return "Late Base / Build"
        elif month in [5, 6]:
            return "Build / Early Race Season"
        elif month in [7, 8]:
            return "Peak Race Season"
        elif month in [9, 10]:
            return "Late Season / Transition"
        else:
            return "Unknown"
    
    # === ALERTS SYSTEM (v3.3.0) ===
    
    def _generate_alerts(self, derived_metrics: Dict, wellness_7d: List[Dict],
                         tss_7d_total: float, tss_28d_total: float) -> List[Dict]:
        """
        Generate graduated alerts array based on Section 11 v11.4 thresholds.
        
        Severity levels: "info" → "warning" → "alarm"
        Empty array = green light.
        """
        alerts = []
        
        acwr = derived_metrics.get("acwr")
        monotony = derived_metrics.get("monotony")
        strain = derived_metrics.get("strain")
        ri = derived_metrics.get("recovery_index")
        latest_hrv = derived_metrics.get("latest_hrv")
        latest_rhr = derived_metrics.get("latest_rhr")
        hrv_baseline_7d = derived_metrics.get("hrv_baseline_7d")
        rhr_baseline_7d = derived_metrics.get("rhr_baseline_7d")
        
        # --- ACWR Alerts ---
        if acwr is not None:
            if acwr <= 0.75 or acwr >= 1.35:
                alerts.append({
                    "metric": "acwr",
                    "value": acwr,
                    "severity": "alarm",
                    "threshold": "0.75 / 1.35",
                    "context": f"ACWR {acwr} outside safe range. Injury/overreach risk elevated.",
                    "persistence_days": None,
                    "tier": 2
                })
            elif acwr <= 0.8 or acwr >= 1.3:
                alerts.append({
                    "metric": "acwr",
                    "value": acwr,
                    "severity": "warning",
                    "threshold": "0.8 / 1.3",
                    "context": f"ACWR {acwr} at edge of optimal range. Monitor closely. Alarm at 0.75/1.35.",
                    "persistence_days": None,
                    "tier": 2
                })
        
        # --- Monotony Alerts (with deload context) ---
        if monotony is not None:
            deload_context = self._detect_deload_context(tss_7d_total, tss_28d_total)
            
            if monotony >= 2.5:
                if deload_context:
                    alerts.append({
                        "metric": "monotony",
                        "value": monotony,
                        "severity": "info",
                        "threshold": 2.5,
                        "context": f"Monotony {monotony} ≥ 2.5 but {deload_context}. Structural artifact, not overuse risk. Will normalize as 7-day window rolls forward.",
                        "persistence_days": None,
                        "tier": 2
                    })
                else:
                    alerts.append({
                        "metric": "monotony",
                        "value": monotony,
                        "severity": "alarm",
                        "threshold": 2.5,
                        "context": f"Monotony {monotony} ≥ 2.5. Overuse risk elevated. Vary training load.",
                        "persistence_days": None,
                        "tier": 2
                    })
            elif monotony >= 2.3:
                if deload_context:
                    alerts.append({
                        "metric": "monotony",
                        "value": monotony,
                        "severity": "info",
                        "threshold": 2.3,
                        "context": f"Monotony {monotony} approaching threshold but {deload_context}. Expected, not actionable.",
                        "persistence_days": None,
                        "tier": 2
                    })
                else:
                    alerts.append({
                        "metric": "monotony",
                        "value": monotony,
                        "severity": "warning",
                        "threshold": 2.3,
                        "context": f"Monotony {monotony} approaching overuse threshold. Alarm at 2.5.",
                        "persistence_days": None,
                        "tier": 2
                    })
        
        # --- Strain Alerts ---
        if strain is not None and strain > 3500:
            alerts.append({
                "metric": "strain",
                "value": strain,
                "severity": "alarm",
                "threshold": 3500,
                "context": f"Strain {strain} > 3500. High cumulative stress. Consider load reduction.",
                "persistence_days": None,
                "tier": 2
            })
        
        # --- Recovery Index Alerts ---
        if ri is not None:
            if ri < 0.6:
                alerts.append({
                    "metric": "recovery_index",
                    "value": ri,
                    "severity": "alarm",
                    "threshold": 0.6,
                    "context": f"RI {ri} < 0.6. Immediate deload required.",
                    "persistence_days": None,
                    "tier": 1
                })
            elif ri < 0.7:
                alerts.append({
                    "metric": "recovery_index",
                    "value": ri,
                    "severity": "warning",
                    "threshold": 0.7,
                    "context": f"RI {ri} < 0.7. Monitor — if persists >3 days, deload review required.",
                    "persistence_days": None,
                    "tier": 1
                })
        
        # --- HRV Alerts ---
        if latest_hrv and hrv_baseline_7d and hrv_baseline_7d > 0:
            hrv_change_pct = ((latest_hrv - hrv_baseline_7d) / hrv_baseline_7d) * 100
            if hrv_change_pct <= -20:
                # Check persistence: count consecutive days with HRV ↓>20%
                hrv_low_days = self._count_hrv_low_days(wellness_7d, hrv_baseline_7d)
                
                if hrv_low_days > 2:
                    alerts.append({
                        "metric": "hrv",
                        "value": round(latest_hrv, 1),
                        "severity": "alarm",
                        "threshold": f"↓>20% vs baseline ({round(hrv_baseline_7d, 1)})",
                        "context": f"HRV {round(latest_hrv, 1)} is {round(abs(hrv_change_pct), 1)}% below baseline, persisting {hrv_low_days} days.",
                        "persistence_days": hrv_low_days,
                        "tier": 1
                    })
                else:
                    alerts.append({
                        "metric": "hrv",
                        "value": round(latest_hrv, 1),
                        "severity": "warning",
                        "threshold": f"↓>20% vs baseline ({round(hrv_baseline_7d, 1)})",
                        "context": f"HRV {round(latest_hrv, 1)} is {round(abs(hrv_change_pct), 1)}% below baseline. Monitor — alarm if persists >2 days.",
                        "persistence_days": hrv_low_days,
                        "tier": 1
                    })
        
        # --- RHR Alerts ---
        if latest_rhr and rhr_baseline_7d and rhr_baseline_7d > 0:
            rhr_change = latest_rhr - rhr_baseline_7d
            if rhr_change >= 5:
                # Check persistence
                rhr_high_days = self._count_rhr_high_days(wellness_7d, rhr_baseline_7d)
                
                if rhr_high_days > 2:
                    alerts.append({
                        "metric": "rhr",
                        "value": round(latest_rhr, 1),
                        "severity": "alarm",
                        "threshold": f"↑≥5bpm vs baseline ({round(rhr_baseline_7d, 1)})",
                        "context": f"RHR {round(latest_rhr, 1)} is {round(rhr_change, 1)}bpm above baseline, persisting {rhr_high_days} days.",
                        "persistence_days": rhr_high_days,
                        "tier": 1
                    })
                else:
                    alerts.append({
                        "metric": "rhr",
                        "value": round(latest_rhr, 1),
                        "severity": "warning",
                        "threshold": f"↑≥5bpm vs baseline ({round(rhr_baseline_7d, 1)})",
                        "context": f"RHR {round(latest_rhr, 1)} is {round(rhr_change, 1)}bpm above baseline. Monitor — alarm if persists >2 days.",
                        "persistence_days": rhr_high_days,
                        "tier": 1
                    })
        
        # Sort by tier (lower = more important), then severity
        severity_order = {"alarm": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: (a["tier"], severity_order.get(a["severity"], 3)))
        
        return alerts
    
    def _detect_deload_context(self, tss_7d_total: float, tss_28d_total: float) -> Optional[str]:
        """
        Detect if current period is a deload or post-deload transition.
        
        A deload is detected when trailing 7-day TSS is ≥20% below the 28-day weekly average.
        Returns context string if deload detected, None otherwise.
        """
        if not tss_28d_total or tss_28d_total == 0:
            return None
        
        weekly_avg_28d = tss_28d_total / 4  # 4 weeks
        
        if weekly_avg_28d == 0:
            return None
        
        deficit_pct = ((weekly_avg_28d - tss_7d_total) / weekly_avg_28d) * 100
        
        if deficit_pct >= 20:
            return f"deload pattern detected (7-day TSS {round(tss_7d_total)} is {round(deficit_pct)}% below 28-day weekly avg {round(weekly_avg_28d)})"
        
        return None
    
    def _count_hrv_low_days(self, wellness_7d: List[Dict], baseline: float) -> int:
        """Count consecutive days (from most recent) where HRV is ↓>20% below baseline"""
        threshold = baseline * 0.8
        count = 0
        for w in reversed(wellness_7d):
            hrv = w.get("hrv")
            if hrv is not None and hrv < threshold:
                count += 1
            else:
                break
        return count
    
    def _count_rhr_high_days(self, wellness_7d: List[Dict], baseline: float) -> int:
        """Count consecutive days (from most recent) where RHR is ↑≥5bpm above baseline"""
        threshold = baseline + 5
        count = 0
        for w in reversed(wellness_7d):
            rhr = w.get("restingHR")
            if rhr is not None and rhr >= threshold:
                count += 1
            else:
                break
        return count
    
    # === HISTORY GENERATION (v3.3.0) ===
    
    def _get_history_confidence(self) -> Dict:
        """
        Check history.json availability and return confidence metadata.
        """
        history_path = self.script_dir / self.HISTORY_FILE
        
        if history_path.exists():
            try:
                with open(history_path, 'r') as f:
                    history_data = json.load(f)
                generated_at = history_data.get("generated_at", "")
                
                # Calculate age
                try:
                    gen_date = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                    age_days = (datetime.now() - gen_date.replace(tzinfo=None)).days
                except:
                    age_days = None
                
                # Determine confidence from data range
                total_months = history_data.get("data_range", {}).get("total_months", 0)
                if total_months >= 12:
                    confidence = "high"
                elif total_months >= 3:
                    confidence = "medium"
                else:
                    confidence = "low"
                
                return {
                    "available": True,
                    "last_generated": generated_at[:10] if generated_at else None,
                    "age_days": age_days,
                    "total_months": total_months,
                    "history_confidence": confidence
                }
            except Exception as e:
                if self.debug:
                    print(f"  Could not read history.json: {e}")
        
        return {
            "available": False,
            "history_confidence": "low",
            "note": "No history.json available. Longitudinal analysis limited to current 28-day window."
        }
    
    def should_generate_history(self) -> bool:
        """
        Determine if history.json needs to be (re)generated.
        
        Triggers:
        - history.json missing → ALWAYS generate (bypass time gate, first-run scenario)
        - history.json >28 days old → regenerate (time-gated to Sun/Mon midnight)
        
        Refresh runs only on Sundays (6) or Mondays (0), in the first two runs
        after midnight (00:00 and 00:15 UTC).
        """
        history_path = self.script_dir / self.HISTORY_FILE
        
        # If history.json doesn't exist, ALWAYS generate (bypass time gate)
        if not history_path.exists():
            if self.debug:
                print("  history.json missing — will generate (first run)")
            return True
        
        # For REFRESH of existing history, apply the time gate
        now = datetime.now()
        
        # Only on Sundays (6) or Mondays (0)
        if now.weekday() not in [0, 6]:
            return False
        
        # Only in the first two runs after midnight (00:00-00:30)
        if now.hour > 0 or (now.hour == 0 and now.minute > 30):
            return False
        
        # Check age of existing file
        try:
            with open(history_path, 'r') as f:
                history_data = json.load(f)
            generated_at = history_data.get("generated_at", "")
            gen_date = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_days = (datetime.now() - gen_date.replace(tzinfo=None)).days
            
            if age_days > 28:
                if self.debug:
                    print(f"  history.json is {age_days} days old — will regenerate")
                return True
            else:
                if self.debug:
                    print(f"  history.json is {age_days} days old — fresh enough")
                return False
        except Exception as e:
            if self.debug:
                print(f"  Could not parse history.json age: {e} — will regenerate")
            return True
    
    def generate_history(self) -> Dict:
        """
        Generate history.json with tiered granularity.
        
        Pulls fresh from Intervals.icu API:
        - 90-day tier: daily rows (15 fields)
        - 180-day tier: weekly aggregates (18 fields)
        - 1/2/3-year tiers: monthly aggregates (17 fields)
        - FTP timeline from API
        - Data gaps flagged factually
        """
        print("\n📊 Generating history.json...")
        
        now = datetime.now()
        
        # Determine how far back we can go (up to 3 years)
        earliest_3y = (now - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        newest = now.strftime("%Y-%m-%d")
        
        # Fetch all activities for full range
        print("  Fetching full activity history (up to 3 years)...")
        try:
            all_activities = self._intervals_get("activities", {
                "oldest": earliest_3y, "newest": newest
            })
        except Exception as e:
            print(f"  ⚠️ Could not fetch full history: {e}")
            all_activities = []
        
        # Fetch all wellness for full range
        print("  Fetching full wellness history...")
        try:
            all_wellness = self._intervals_get("wellness", {
                "oldest": earliest_3y, "newest": newest
            })
        except Exception as e:
            print(f"  ⚠️ Could not fetch wellness history: {e}")
            all_wellness = []
        
        # Fetch athlete data for FTP history from API
        print("  Fetching athlete settings...")
        athlete = self._intervals_get("")
        
        # Determine actual data range
        activity_dates = sorted([a.get("start_date_local", "")[:10] for a in all_activities if a.get("start_date_local")])
        
        if activity_dates:
            earliest_date = activity_dates[0]
            latest_date = activity_dates[-1]
        else:
            earliest_date = newest
            latest_date = newest
        
        try:
            earliest_dt = datetime.strptime(earliest_date, "%Y-%m-%d")
            total_months = max(1, int((now - earliest_dt).days / 30.44))
        except:
            total_months = 0
        
        # Build wellness lookup by date
        wellness_by_date = {}
        for w in all_wellness:
            date_str = w.get("id", "")
            if date_str:
                wellness_by_date[date_str] = w
        
        # Build activity lookup by date
        activities_by_date = defaultdict(list)
        for a in all_activities:
            date_str = a.get("start_date_local", "")[:10]
            if date_str:
                activities_by_date[date_str].append(a)
        
        # === FTP TIMELINE (from wellness sportInfo history or settings) ===
        ftp_timeline = self._build_ftp_timeline(all_wellness, athlete)
        
        # === DATA GAPS ===
        data_gaps = self._find_data_gaps(activity_dates, earliest_date, latest_date)
        
        # === 90-DAY DAILY ===
        print("  Building 90-day daily tier...")
        daily_90d = self._build_daily_tier(activities_by_date, wellness_by_date, days=90)
        
        # === 180-DAY WEEKLY ===
        print("  Building 180-day weekly tier...")
        weekly_180d = self._build_weekly_tier(activities_by_date, wellness_by_date, days=180)
        
        # === MONTHLY TIERS ===
        monthly_tiers = {}
        for years in [1, 2, 3]:
            label = f"{years}y"
            days_back = years * 365
            if total_months >= years * 12 * 0.5:  # Only generate if enough data
                print(f"  Building {label} monthly tier...")
                monthly_tiers[f"monthly_{label}"] = self._build_monthly_tier(
                    activities_by_date, wellness_by_date, days=days_back
                )
            else:
                monthly_tiers[f"monthly_{label}"] = []
        
        # === SUMMARIES ===
        summaries = self._build_history_summaries(daily_90d, weekly_180d, monthly_tiers)
        
        history = {
            "generated_at": now.isoformat(),
            "source": "Intervals.icu API",
            "sync_version": self.VERSION,
            "data_range": {
                "earliest": earliest_date,
                "latest": latest_date,
                "total_months": total_months
            },
            "ftp_timeline": ftp_timeline,
            "data_gaps": data_gaps,
            "summaries": summaries,
            "daily_90d": daily_90d,
            "weekly_180d": weekly_180d,
            **monthly_tiers
        }
        
        # Save locally
        history_path = self.script_dir / self.HISTORY_FILE
        with open(history_path, 'w') as f:
            json.dump(history, f, indent=2, default=str)
        print(f"  ✅ history.json saved ({len(daily_90d)} daily, {len(weekly_180d)} weekly rows)")
        
        return history
    
    def _build_daily_tier(self, activities_by_date: Dict, wellness_by_date: Dict, 
                          days: int) -> List[Dict]:
        """Build daily resolution rows for the 90-day tier."""
        rows = []
        now = datetime.now()
        
        for i in range(days - 1, -1, -1):
            date = (now - timedelta(days=i))
            date_str = date.strftime("%Y-%m-%d")
            
            day_activities = activities_by_date.get(date_str, [])
            wellness = wellness_by_date.get(date_str, {})
            
            total_tss = sum(a.get("icu_training_load", 0) or 0 for a in day_activities)
            total_seconds = sum(a.get("moving_time", 0) or 0 for a in day_activities)
            activity_types = list(set(a.get("type", "Unknown") for a in day_activities)) if day_activities else ["Rest"]
            
            # Zone ladder for hard day detection
            # Cumulative thresholds: z3+ / z4+ / z5+ / z6+ / z7
            # Per Seiler's polarized model + Foster's session RPE scaling
            day_z3 = 0
            day_z4 = 0
            day_z5 = 0
            day_z6 = 0
            day_z7 = 0
            for a in day_activities:
                icu_zone_times = a.get("icu_zone_times", [])
                if icu_zone_times:
                    for zone in icu_zone_times:
                        zid = zone.get("id", "").lower()
                        secs = zone.get("secs", 0)
                        if zid == "z3":
                            day_z3 += secs
                        elif zid == "z4":
                            day_z4 += secs
                        elif zid == "z5":
                            day_z5 += secs
                        elif zid == "z6":
                            day_z6 += secs
                        elif zid == "z7":
                            day_z7 += secs
            is_hard = (
                (day_z3 + day_z4 + day_z5 + day_z6 + day_z7) >= 1800 or
                (day_z4 + day_z5 + day_z6 + day_z7) >= 600 or
                (day_z5 + day_z6 + day_z7) >= 300 or
                (day_z6 + day_z7) >= 120 or
                day_z7 >= 60
            )
            
            rows.append({
                "date": date_str,
                "total_hours": round(total_seconds / 3600, 2),
                "total_tss": round(total_tss, 0),
                "activity_count": len(day_activities),
                "activity_types": ", ".join(activity_types),
                "ctl": wellness.get("ctl"),
                "atl": wellness.get("atl"),
                "tsb": round(wellness.get("ctl", 0) - wellness.get("atl", 0), 1) if wellness.get("ctl") and wellness.get("atl") else None,
                "hrv": wellness.get("hrv"),
                "rhr": wellness.get("restingHR"),
                "sleep_hours": round(wellness.get("sleepSecs", 0) / 3600, 2) if wellness.get("sleepSecs") else None,
                "sleep_quality": wellness.get("sleepQuality"),
                "feel": None,  # Not available in wellness, only in activities
                "weight_kg": wellness.get("weight"),
                "is_hard_day": is_hard
            })
            
            # Check feel from activities
            for a in day_activities:
                feel = a.get("feel")
                if feel:
                    rows[-1]["feel"] = feel
                    break
        
        return rows
    
    def _build_weekly_tier(self, activities_by_date: Dict, wellness_by_date: Dict,
                           days: int) -> List[Dict]:
        """Build weekly aggregate rows for the 180-day tier."""
        rows = []
        now = datetime.now()
        
        # Calculate weeks
        start_date = now - timedelta(days=days)
        # Align to Monday
        start_monday = start_date - timedelta(days=start_date.weekday())
        
        current = start_monday
        while current < now:
            week_end = current + timedelta(days=6)
            if week_end > now:
                week_end = now
            
            week_tss = 0
            week_seconds = 0
            week_activities = 0
            week_hrv = []
            week_rhr = []
            week_sleep = []
            week_feel = []
            week_weight = []
            hard_days = 0
            longest_ride = 0
            z1_z2_time = 0
            z3_time = 0
            z4_plus_time = 0
            total_zone_time = 0
            ctl_end = None
            atl_end = None
            tsb_end = None
            ramp_rate = None
            
            for d in range(7):
                date = current + timedelta(days=d)
                if date > now:
                    break
                date_str = date.strftime("%Y-%m-%d")
                
                day_activities = activities_by_date.get(date_str, [])
                wellness = wellness_by_date.get(date_str, {})
                
                day_tss = sum(a.get("icu_training_load", 0) or 0 for a in day_activities)
                day_seconds = sum(a.get("moving_time", 0) or 0 for a in day_activities)
                
                week_tss += day_tss
                week_seconds += day_seconds
                week_activities += len(day_activities)
                
                if wellness.get("hrv"):
                    week_hrv.append(wellness["hrv"])
                if wellness.get("restingHR"):
                    week_rhr.append(wellness["restingHR"])
                if wellness.get("sleepSecs"):
                    week_sleep.append(wellness["sleepSecs"] / 3600)
                if wellness.get("weight"):
                    week_weight.append(wellness["weight"])
                
                ctl_end = wellness.get("ctl") or ctl_end
                atl_end = wellness.get("atl") or atl_end
                ramp_rate = wellness.get("rampRate") or ramp_rate
                
                # Zone and hard day analysis
                day_z3 = 0
                day_z4 = 0
                day_z5 = 0
                day_z6 = 0
                day_z7 = 0
                for a in day_activities:
                    ride_seconds = a.get("moving_time", 0) or 0
                    if ride_seconds > longest_ride:
                        longest_ride = ride_seconds
                    
                    icu_zone_times = a.get("icu_zone_times", [])
                    if icu_zone_times:
                        for zone in icu_zone_times:
                            zid = zone.get("id", "").lower()
                            secs = zone.get("secs", 0)
                            if zid in ["z1", "z2"]:
                                z1_z2_time += secs
                            elif zid == "z3":
                                z3_time += secs
                                day_z3 += secs
                            elif zid == "z4":
                                z4_plus_time += secs
                                day_z4 += secs
                            elif zid == "z5":
                                z4_plus_time += secs
                                day_z5 += secs
                            elif zid == "z6":
                                z4_plus_time += secs
                                day_z6 += secs
                            elif zid == "z7":
                                z4_plus_time += secs
                                day_z7 += secs
                            total_zone_time += secs
                    
                    feel = a.get("feel")
                    if feel:
                        week_feel.append(feel)
                
                if (
                    (day_z3 + day_z4 + day_z5 + day_z6 + day_z7) >= 1800 or
                    (day_z4 + day_z5 + day_z6 + day_z7) >= 600 or
                    (day_z5 + day_z6 + day_z7) >= 300 or
                    (day_z6 + day_z7) >= 120 or
                    day_z7 >= 60
                ):
                    hard_days += 1
            
            if ctl_end and atl_end:
                tsb_end = round(ctl_end - atl_end, 1)
            
            rows.append({
                "week_start": current.strftime("%Y-%m-%d"),
                "total_hours": round(week_seconds / 3600, 2),
                "total_tss": round(week_tss, 0),
                "activity_count": week_activities,
                "ctl_end": round(ctl_end, 1) if ctl_end else None,
                "atl_end": round(atl_end, 1) if atl_end else None,
                "tsb_end": tsb_end,
                "ramp_rate": round(ramp_rate, 2) if ramp_rate else None,
                "avg_hrv": round(statistics.mean(week_hrv), 1) if week_hrv else None,
                "avg_rhr": round(statistics.mean(week_rhr), 1) if week_rhr else None,
                "avg_sleep_hours": round(statistics.mean(week_sleep), 2) if week_sleep else None,
                "z1_z2_pct": round((z1_z2_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "z3_pct": round((z3_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "z4_plus_pct": round((z4_plus_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "hard_days": hard_days,
                "longest_ride_hours": round(longest_ride / 3600, 2),
                "avg_feel": round(statistics.mean(week_feel), 1) if week_feel else None,
                "weight_kg": round(week_weight[-1], 1) if week_weight else None
            })
            
            current += timedelta(days=7)
        
        return rows
    
    def _build_monthly_tier(self, activities_by_date: Dict, wellness_by_date: Dict,
                            days: int) -> List[Dict]:
        """Build monthly aggregate rows for 1/2/3-year tiers."""
        rows = []
        now = datetime.now()
        start_date = now - timedelta(days=days)
        
        # Group by month
        current_month = datetime(start_date.year, start_date.month, 1)
        
        while current_month <= now:
            month_str = current_month.strftime("%Y-%m")
            
            # Determine days in this month
            if current_month.month == 12:
                next_month = datetime(current_month.year + 1, 1, 1)
            else:
                next_month = datetime(current_month.year, current_month.month + 1, 1)
            
            month_tss = 0
            month_seconds = 0
            month_activities = 0
            month_hrv = []
            month_rhr = []
            month_weight = []
            ctl_values = []
            hard_days_total = 0
            longest_ride = 0
            z1_z2_time = 0
            z3_time = 0
            z4_plus_time = 0
            total_zone_time = 0
            days_with_data = 0
            total_days_in_month = 0
            
            date = current_month
            while date < next_month and date <= now:
                date_str = date.strftime("%Y-%m-%d")
                total_days_in_month += 1
                
                day_activities = activities_by_date.get(date_str, [])
                wellness = wellness_by_date.get(date_str, {})
                
                if day_activities or wellness:
                    days_with_data += 1
                
                day_tss = sum(a.get("icu_training_load", 0) or 0 for a in day_activities)
                day_seconds = sum(a.get("moving_time", 0) or 0 for a in day_activities)
                
                month_tss += day_tss
                month_seconds += day_seconds
                month_activities += len(day_activities)
                
                if wellness.get("hrv"):
                    month_hrv.append(wellness["hrv"])
                if wellness.get("restingHR"):
                    month_rhr.append(wellness["restingHR"])
                if wellness.get("weight"):
                    month_weight.append(wellness["weight"])
                if wellness.get("ctl"):
                    ctl_values.append(wellness["ctl"])
                
                day_z3 = 0
                day_z4 = 0
                day_z5 = 0
                day_z6 = 0
                day_z7 = 0
                for a in day_activities:
                    ride_seconds = a.get("moving_time", 0) or 0
                    if ride_seconds > longest_ride:
                        longest_ride = ride_seconds
                    
                    icu_zone_times = a.get("icu_zone_times", [])
                    if icu_zone_times:
                        for zone in icu_zone_times:
                            zid = zone.get("id", "").lower()
                            secs = zone.get("secs", 0)
                            if zid in ["z1", "z2"]:
                                z1_z2_time += secs
                            elif zid == "z3":
                                z3_time += secs
                                day_z3 += secs
                            elif zid == "z4":
                                z4_plus_time += secs
                                day_z4 += secs
                            elif zid == "z5":
                                z4_plus_time += secs
                                day_z5 += secs
                            elif zid == "z6":
                                z4_plus_time += secs
                                day_z6 += secs
                            elif zid == "z7":
                                z4_plus_time += secs
                                day_z7 += secs
                            total_zone_time += secs
                
                if (
                    (day_z3 + day_z4 + day_z5 + day_z6 + day_z7) >= 1800 or
                    (day_z4 + day_z5 + day_z6 + day_z7) >= 600 or
                    (day_z5 + day_z6 + day_z7) >= 300 or
                    (day_z6 + day_z7) >= 120 or
                    day_z7 >= 60
                ):
                    hard_days_total += 1
                
                date += timedelta(days=1)
            
            # Calculate weeks in this month for per-week averages
            weeks_in_period = max(1, total_days_in_month / 7)
            
            # Determine dominant phase (simplified: based on CTL trend and zone distribution)
            dominant_phase = "Unknown"
            if ctl_values and len(ctl_values) >= 2:
                ctl_trend = ctl_values[-1] - ctl_values[0]
                qi_pct = (z4_plus_time / total_zone_time * 100) if total_zone_time > 0 else 0
                
                if ctl_trend > 3 and qi_pct > 15:
                    dominant_phase = "Build"
                elif ctl_trend > 1:
                    dominant_phase = "Base"
                elif ctl_trend < -3:
                    dominant_phase = "Recovery"
                else:
                    dominant_phase = "Maintenance"
            
            rows.append({
                "month": month_str,
                "total_hours": round(month_seconds / 3600, 2),
                "total_tss": round(month_tss, 0),
                "activity_count": month_activities,
                "ctl_peak": round(max(ctl_values), 1) if ctl_values else None,
                "ctl_low": round(min(ctl_values), 1) if ctl_values else None,
                "ctl_end": round(ctl_values[-1], 1) if ctl_values else None,
                "avg_hrv": round(statistics.mean(month_hrv), 1) if month_hrv else None,
                "avg_rhr": round(statistics.mean(month_rhr), 1) if month_rhr else None,
                "z1_z2_pct": round((z1_z2_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "z3_pct": round((z3_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "z4_plus_pct": round((z4_plus_time / total_zone_time) * 100, 1) if total_zone_time > 0 else None,
                "hard_days_avg_per_week": round(hard_days_total / weeks_in_period, 1),
                "longest_ride_hours": round(longest_ride / 3600, 2),
                "avg_weight_kg": round(statistics.mean(month_weight), 1) if month_weight else None,
                "dominant_phase": dominant_phase,
                "days_with_data": days_with_data
            })
            
            current_month = next_month
        
        return rows
    
    def _build_ftp_timeline(self, all_wellness: List[Dict], athlete: Dict) -> List[Dict]:
        """
        Build FTP timeline from ftp_history.json (actual user-set FTP values).
        Falls back to current sportSettings if no history file exists.
        """
        timeline = []
        
        # Primary source: ftp_history.json (tracked by sync.py on each run)
        ftp_history = self._load_ftp_history()
        
        for ftp_type in ["indoor", "outdoor"]:
            entries = ftp_history.get(ftp_type, {})
            for date_str, ftp_val in sorted(entries.items()):
                timeline.append({
                    "date": date_str,
                    "ftp": ftp_val,
                    "type": ftp_type,
                    "source": "FTP"
                })
        
        # Fallback: add current user-set FTP if not already in timeline
        cycling_settings = None
        if athlete.get("sportSettings"):
            for sport in athlete["sportSettings"]:
                if "Ride" in sport.get("types", []) or "VirtualRide" in sport.get("types", []):
                    cycling_settings = sport
                    break
        
        if cycling_settings:
            today = datetime.now().strftime("%Y-%m-%d")
            outdoor_ftp = cycling_settings.get("ftp")
            indoor_ftp = cycling_settings.get("indoor_ftp")
            
            # Check if current FTP is already the latest in timeline
            outdoor_dates = {e["date"]: e["ftp"] for e in timeline if e["type"] == "outdoor"}
            indoor_dates = {e["date"]: e["ftp"] for e in timeline if e["type"] == "indoor"}
            
            latest_outdoor = outdoor_dates.get(max(outdoor_dates.keys())) if outdoor_dates else None
            latest_indoor = indoor_dates.get(max(indoor_dates.keys())) if indoor_dates else None
            
            if outdoor_ftp and outdoor_ftp != latest_outdoor:
                timeline.append({"date": today, "ftp": outdoor_ftp, "type": "outdoor", "source": "user_set"})
            if indoor_ftp and indoor_ftp != latest_indoor:
                timeline.append({"date": today, "ftp": indoor_ftp, "type": "indoor", "source": "user_set"})
        
        # Sort chronologically
        timeline.sort(key=lambda x: (x["date"], x["type"]))
        
        return timeline
    
    def _find_data_gaps(self, activity_dates: List[str], earliest: str, latest: str) -> List[Dict]:
        """
        Find periods with no activity data (gaps ≥ 3 days).
        Flags factually without inference about reasons.
        """
        gaps = []
        if not activity_dates:
            return gaps
        
        date_set = set(activity_dates)
        
        try:
            start = datetime.strptime(earliest, "%Y-%m-%d")
            end = datetime.strptime(latest, "%Y-%m-%d")
        except:
            return gaps
        
        gap_start = None
        current = start
        
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            
            if date_str not in date_set:
                if gap_start is None:
                    gap_start = current
            else:
                if gap_start is not None:
                    gap_days = (current - gap_start).days
                    if gap_days >= 3:
                        gaps.append({
                            "period": f"{gap_start.strftime('%Y-%m-%d')} to {(current - timedelta(days=1)).strftime('%Y-%m-%d')}",
                            "days_missing": gap_days
                        })
                    gap_start = None
            
            current += timedelta(days=1)
        
        # Handle trailing gap
        if gap_start is not None:
            gap_days = (end - gap_start).days + 1
            if gap_days >= 3:
                gaps.append({
                    "period": f"{gap_start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
                    "days_missing": gap_days
                })
        
        return gaps
    
    def _build_history_summaries(self, daily_90d: List[Dict], weekly_180d: List[Dict],
                                  monthly_tiers: Dict) -> Dict:
        """Build pre-computed summaries for each tier."""
        summaries = {}
        
        # 90-day summary from daily data
        if daily_90d:
            tss_values = [d["total_tss"] for d in daily_90d if d["total_tss"]]
            hours_values = [d["total_hours"] for d in daily_90d if d["total_hours"]]
            ctl_values = [d["ctl"] for d in daily_90d if d["ctl"]]
            
            summaries["90d"] = {
                "avg_weekly_tss": round(sum(tss_values) / max(1, len(daily_90d) / 7), 0) if tss_values else None,
                "avg_weekly_hours": round(sum(hours_values) / max(1, len(daily_90d) / 7), 1) if hours_values else None,
                "ctl_start": round(ctl_values[0], 1) if ctl_values else None,
                "ctl_end": round(ctl_values[-1], 1) if ctl_values else None,
                "total_activities": sum(1 for d in daily_90d if d["activity_count"] > 0),
                "rest_days": sum(1 for d in daily_90d if d["activity_count"] == 0),
                "hard_days": sum(1 for d in daily_90d if d.get("is_hard_day"))
            }
        
        # 180-day summary from weekly data
        if weekly_180d:
            tss_values = [w["total_tss"] for w in weekly_180d if w["total_tss"]]
            hours_values = [w["total_hours"] for w in weekly_180d if w["total_hours"]]
            ctl_values = [w["ctl_end"] for w in weekly_180d if w["ctl_end"]]
            
            summaries["180d"] = {
                "avg_weekly_tss": round(statistics.mean(tss_values), 0) if tss_values else None,
                "avg_weekly_hours": round(statistics.mean(hours_values), 1) if hours_values else None,
                "ctl_start": round(ctl_values[0], 1) if ctl_values else None,
                "ctl_end": round(ctl_values[-1], 1) if ctl_values else None,
                "weeks_tracked": len(weekly_180d)
            }
        
        # Yearly summaries from monthly data
        for key in ["monthly_1y", "monthly_2y", "monthly_3y"]:
            monthly = monthly_tiers.get(key, [])
            if monthly:
                tss_values = [m["total_tss"] for m in monthly if m["total_tss"]]
                ctl_values = [m["ctl_end"] for m in monthly if m["ctl_end"]]
                
                label = key.replace("monthly_", "")
                summaries[label] = {
                    "avg_monthly_tss": round(statistics.mean(tss_values), 0) if tss_values else None,
                    "ctl_peak": round(max(ctl_values), 1) if ctl_values else None,
                    "ctl_low": round(min(ctl_values), 1) if ctl_values else None,
                    "months_tracked": len(monthly)
                }
        
        return summaries
    
    # === UPDATE NOTIFICATIONS (v3.3.0) ===
    
    def check_upstream_updates(self):
        """
        Check CrankAddict/section-11 for new releases and create a GitHub Issue
        if there's a new notification_id.
        
        Uses date-based changelog format:
        {
            "notification_id": "2026-02-11",
            "changes": [
                "SECTION_11.md - UPDATE - 2026-02-11 - Description",
                "sync.py - UPDATE - 2026-02-11 - Description"
            ]
        }
        """
        if not self.github_token or not self.github_repo:
            if self.debug:
                print("  Skipping update check — no GitHub credentials")
            return
        
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github+json"
        }
        
        # Fetch changelog.json from upstream
        try:
            url = f"https://raw.githubusercontent.com/{self.UPSTREAM_REPO}/main/{self.CHANGELOG_FILE}"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                if self.debug:
                    print(f"  No changelog.json found upstream (HTTP {response.status_code})")
                return
            
            changelog = response.json()
        except Exception as e:
            if self.debug:
                print(f"  Could not fetch upstream changelog: {e}")
            return
        
        notification_id = changelog.get("notification_id")
        if not notification_id:
            if self.debug:
                print("  No notification_id in changelog")
            return
        
        issue_title = f"Section 11 updates — {notification_id}"
        
        # Check if issue already exists (open or closed)
        try:
            search_url = f"{self.GITHUB_API_URL}/search/issues"
            search_params = {
                "q": f'repo:{self.github_repo} "{issue_title}" in:title'
            }
            response = requests.get(search_url, headers=headers, params=search_params, timeout=10)
            
            if response.status_code == 200:
                results = response.json()
                if results.get("total_count", 0) > 0:
                    if self.debug:
                        print(f"  Update notification already exists: {issue_title}")
                    return
        except Exception as e:
            if self.debug:
                print(f"  Could not search issues: {e}")
            return
        
        # Create new issue
        changes = changelog.get("changes", [])
        body = f"## Section 11 Update Available\n\n"
        body += f"**Notification ID:** {notification_id}\n\n"
        body += "### Changes:\n"
        for change in changes:
            body += f"- {change}\n"
        body += f"\n### Repository:\n"
        body += f"https://github.com/{self.UPSTREAM_REPO}\n"
        body += f"\n*This issue was auto-created by sync.py v{self.VERSION}*"
        
        try:
            issues_url = f"{self.GITHUB_API_URL}/repos/{self.github_repo}/issues"
            payload = {
                "title": issue_title,
                "body": body,
                "labels": ["update-notification"]
            }
            response = requests.post(issues_url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 201:
                print(f"  📢 Update notification created: {issue_title}")
            else:
                if self.debug:
                    print(f"  Could not create issue (HTTP {response.status_code}): {response.text}")
        except Exception as e:
            if self.debug:
                print(f"  Could not create update issue: {e}")
    
    def _format_activities(self, activities: List[Dict], anonymize: bool = False) -> List[Dict]:
        """Format activities for LLM analysis"""
        formatted = []
        for i, act in enumerate(activities):
            avg_power = (act.get("average_watts") or act.get("avg_watts") or 
                        act.get("average_power") or act.get("avgWatts") or
                        act.get("icu_average_watts"))
            norm_power = (act.get("weighted_average_watts") or act.get("np") or 
                         act.get("icu_pm_np") or act.get("normalizedPower") or
                         act.get("icu_weighted_avg_watts"))
            avg_hr = (act.get("average_heartrate") or act.get("avg_hr") or 
                     act.get("average_heart_rate") or act.get("avgHr") or
                     act.get("icu_average_hr"))
            max_hr = (act.get("max_heartrate") or act.get("max_hr") or 
                     act.get("max_heart_rate") or act.get("maxHr") or
                     act.get("icu_max_hr"))
            
            avg_cadence = (act.get("average_cadence") or act.get("avg_cadence") or
                          act.get("icu_average_cadence"))
            avg_temp = (act.get("average_weather_temp") or act.get("average_temp") or 
                       act.get("avg_temp") or act.get("average_temperature"))
            joules = act.get("icu_joules")
            work_kj = round(joules / 1000, 1) if joules else None
            calories = act.get("calories") or act.get("icu_calories")
            variability_index = act.get("icu_variability_index")
            decoupling = act.get("icu_hr_decoupling") or act.get("decoupling")
            
            avg_speed_ms = act.get("average_speed")
            max_speed_ms = act.get("max_speed")
            avg_speed = round(avg_speed_ms * 3.6, 1) if avg_speed_ms else None
            max_speed = round(max_speed_ms * 3.6, 1) if max_speed_ms else None
            avg_pace = act.get("average_pace") or act.get("icu_pace")
            
            weather = act.get("weather_description") or act.get("weather")
            humidity = act.get("humidity") or act.get("average_humidity")
            wind_speed = act.get("average_wind_speed") or act.get("wind_speed")
            
            carbs_used = act.get("carbs_used")
            carbs_ingested = act.get("carbs_ingested")
            
            hr_zones = {}
            power_zones = {}
            
            icu_hr_zone_times = act.get("icu_hr_zone_times", [])
            if icu_hr_zone_times and isinstance(icu_hr_zone_times, list):
                zone_labels = ["z1_time", "z2_time", "z3_time", "z4_time", "z5_time", "z6_time", "z7_time"]
                for idx, secs in enumerate(icu_hr_zone_times):
                    if idx < len(zone_labels):
                        hr_zones[zone_labels[idx]] = secs if secs is not None else 0
            
            icu_zone_times = act.get("icu_zone_times", [])
            if icu_zone_times:
                for zone in icu_zone_times:
                    zone_id = zone.get("id", "").lower()
                    secs = zone.get("secs", 0)
                    if zone_id in ["z1", "z2", "z3", "z4", "z5", "z6", "z7"]:
                        power_zones[f"{zone_id}_time"] = secs if secs is not None else 0
            
            zone_dist = {}
            if hr_zones:
                zone_dist["hr_zones"] = hr_zones
            if power_zones:
                zone_dist["power_zones"] = power_zones
            
            if not zone_dist:
                zone_dist = None
            
            activity_name = act.get("name", "")
            if anonymize:
                if "VirtualRide" in act.get("type", "") or "Indoor" in activity_name:
                    activity_name = activity_name
                else:
                    activity_name = "Training Session"
            
            activity = {
                "id": f"activity_{i+1}" if anonymize else act["id"],
                "date": act["start_date_local"],
                "type": act["type"],
                "name": activity_name,
                "duration_hours": round((act.get("moving_time") or 0) / 3600, 2),
                "distance_km": round((act.get("distance") or 0) / 1000, 2),
                "tss": act.get("icu_training_load"),
                "intensity_factor": act.get("icu_intensity"),
                "avg_power": avg_power,
                "normalized_power": norm_power,
                "avg_hr": avg_hr,
                "max_hr": max_hr,
                "avg_cadence": avg_cadence,
                "avg_speed": avg_speed,
                "max_speed": max_speed,
                "avg_pace": avg_pace,
                "avg_temp": avg_temp,
                "weather": weather,
                "humidity": humidity,
                "wind_speed": wind_speed,
                "work_kj": work_kj,
                "calories": calories,
                "carbs_used": carbs_used,
                "carbs_ingested": carbs_ingested,
                "variability_index": variability_index,
                "decoupling": decoupling,
                "elevation_m": act.get("total_elevation_gain"),
                "feel": act.get("feel"),
                "rpe": act.get("icu_rpe"),
                "zone_distribution": zone_dist
            }
            
            formatted.append(activity)
        
        return formatted
    
    def _format_wellness(self, wellness: List[Dict]) -> List[Dict]:
        """Format wellness data"""
        formatted = []
        for w in wellness:
            entry = {
                "date": w["id"],
                "weight_kg": w.get("weight"),
                "resting_hr": w.get("restingHR"),
                "hrv_rmssd": w.get("hrv"),
                "hrv_sdnn": w.get("hrvSdnn"),
                "sleep_hours": round(w["sleepSecs"] / 3600, 2) if w.get("sleepSecs") else None,
                "sleep_quality": w.get("sleepQuality"),
                "sleep_score": w.get("sleepScore"),
                "mental_energy": w.get("mentalEnergy"),
                "fatigue": w.get("fatigue"),
                "soreness": w.get("soreness"),
                "avg_sleeping_hr": w.get("avgSleepingHR"),
                "vo2max": w.get("vo2max")
            }
            
            formatted.append(entry)
        
        return formatted
    
    def _format_events(self, events: List[Dict], anonymize: bool = False) -> List[Dict]:
        """Format planned workouts"""
        return [{
            "id": f"event_{i+1}" if anonymize else evt["id"],
            "date": evt["start_date_local"],
            "name": "Planned Workout" if anonymize else evt.get("name", ""),
            "type": evt.get("category", ""),
            "description": evt.get("description", ""),
            "planned_tss": evt.get("icu_training_load"),
            "duration_hours": round(evt.get("duration", 0) / 3600, 2)
        } for i, evt in enumerate(events)]
    
    def _compute_weekly_summary(self, activities: List[Dict], wellness: List[Dict]) -> Dict:
        """Compute weekly training summary from actual activity data"""
        total_tss = sum(act.get("icu_training_load", 0) for act in activities if act.get("icu_training_load"))
        total_seconds = sum(act.get("moving_time", 0) for act in activities)
        total_hours = total_seconds / 3600
        
        avg_hrv = None
        avg_rhr = None
        if wellness:
            hrv_values = [w.get("hrv") for w in wellness if w.get("hrv")]
            rhr_values = [w.get("restingHR") for w in wellness if w.get("restingHR")]
            avg_hrv = round(sum(hrv_values) / len(hrv_values), 1) if hrv_values else None
            avg_rhr = round(sum(rhr_values) / len(rhr_values), 1) if rhr_values else None
        
        return {
            "total_training_hours": round(total_hours, 2),
            "total_tss": round(total_tss, 0),
            "activities_count": len(activities),
            "avg_hrv": avg_hrv,
            "avg_resting_hr": avg_rhr
        }
    
    def _compute_activity_summary(self, activities: List[Dict], days_back: int = 7) -> Dict:
        """Compute summary by activity type with human-readable format"""
        by_type = defaultdict(lambda: {"count": 0, "seconds": 0, "tss": 0, "distance_km": 0})
        
        for act in activities:
            activity_type = act.get("type", "Unknown")
            by_type[activity_type]["count"] += 1
            
            time_seconds = act.get("moving_time", 0)
            
            by_type[activity_type]["seconds"] += time_seconds
            by_type[activity_type]["tss"] += act.get("icu_training_load", 0) or 0
            by_type[activity_type]["distance_km"] += (act.get("distance", 0) or 0) / 1000
        
        activity_breakdown = {}
        total_seconds = 0
        
        for activity_type, data in sorted(by_type.items()):
            activity_breakdown[activity_type] = {
                "duration_decimal_hours": round(data["seconds"] / 3600, 2),
                "count": data["count"],
                "tss": round(data["tss"], 0),
                "distance_km": round(data["distance_km"], 1)
            }
            total_seconds += data["seconds"]
        
        return {
            "period_description": f"Last {days_back} days of training (including today)",
            "note": "Duration calculated from API moving_time field.",
            "total_duration_decimal_hours": round(total_seconds / 3600, 2),
            "total_activities": len(activities),
            "by_activity_type": activity_breakdown
        }
    
    def publish_to_github(self, data: Dict, filepath: str = "latest.json", 
                         commit_message: str = None) -> str:
        """Publish data to GitHub repository"""
        if not self.github_token or not self.github_repo:
            raise ValueError("GitHub token and repo required for publishing")
        
        if not commit_message:
            commit_message = f"Update training data - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github+json"
        }
        
        url = f"{self.GITHUB_API_URL}/repos/{self.github_repo}/contents/{filepath}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                current_file = response.json()
                current_sha = current_file["sha"]
                
                current_content = base64.b64decode(current_file["content"]).decode()
                new_content = json.dumps(data, indent=2, default=str)
                
                if current_content == new_content:
                    print("⏭️  No changes detected, skipping update")
                    raw_url = f"https://raw.githubusercontent.com/{self.github_repo}/main/{filepath}"
                    return raw_url
            else:
                current_sha = None
        except Exception as e:
            print(f"⚠️  Could not check existing file: {e}")
            current_sha = None
        
        content_json = json.dumps(data, indent=2, default=str)
        content_base64 = base64.b64encode(content_json.encode()).decode()
        
        payload = {
            "message": commit_message,
            "content": content_base64,
            "branch": "main"
        }
        
        if current_sha:
            payload["sha"] = current_sha
        
        response = requests.put(url, headers=headers, json=payload)
        response.raise_for_status()
        
        raw_url = f"https://raw.githubusercontent.com/{self.github_repo}/main/{filepath}"
        return raw_url
    
    def save_to_file(self, data: Dict, filepath: str = "latest.json"):
        """Save data to local JSON file"""
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"Data saved to {filepath}")
        return filepath


def main():
    parser = argparse.ArgumentParser(description="Sync Intervals.icu data to GitHub or local file")
    parser.add_argument("--setup", action="store_true", help="Initial setup wizard")
    parser.add_argument("--athlete-id", help="Intervals.icu athlete ID")
    parser.add_argument("--intervals-key", help="Intervals.icu API key")
    parser.add_argument("--github-token", help="GitHub Personal Access Token")
    parser.add_argument("--github-repo", help="GitHub repo (format: username/repo)")
    parser.add_argument("--days", type=int, default=7, help="Days of data to export (default: 7)")
    parser.add_argument("--output", help="Save to local file instead of GitHub")
    parser.add_argument("--anonymize", action="store_true", default=True, help="Remove identifying information (default: enabled)")
    parser.add_argument("--debug", action="store_true", help="Show debug output for API fields")
    parser.add_argument("--generate-history", action="store_true", help="Force generate history.json (pulls up to 3 years)")
    
    args = parser.parse_args()
    
    if args.setup:
        print("=== Intervals.icu Sync Setup ===\n")
        athlete_id = input("Intervals.icu Athlete ID (e.g., i123456): ")
        intervals_key = input("Intervals.icu API Key: ")
        github_token = input("GitHub Personal Access Token (or press Enter to skip): ")
        github_repo = input("GitHub Repository (username/repo, or press Enter to skip): ")
        
        config = {
            "athlete_id": athlete_id,
            "intervals_key": intervals_key,
        }
        if github_token:
            config["github_token"] = github_token
        if github_repo:
            config["github_repo"] = github_repo
            
        with open(".sync_config.json", "w") as f:
            json.dump(config, f, indent=2)
        print("\n✅ Config saved to .sync_config.json")
        print("\nUsage:")
        print("  Export locally:    python sync.py --output latest.json")
        print("  Push to GitHub:    python sync.py")
        print("  Generate history:  python sync.py --generate-history --output history.json")
        return
    
    config = {}
    if os.path.exists(".sync_config.json"):
        with open(".sync_config.json") as f:
            config = json.load(f)
    
    athlete_id = args.athlete_id or config.get("athlete_id") or os.getenv("ATHLETE_ID")
    intervals_key = args.intervals_key or config.get("intervals_key") or os.getenv("INTERVALS_KEY")
    github_token = args.github_token or config.get("github_token") or os.getenv("GITHUB_TOKEN")
    github_repo = args.github_repo or config.get("github_repo") or os.getenv("GITHUB_REPO")
    
    print(f"📋 Configuration:")
    print(f"   Athlete ID: {athlete_id[:5] + '...' if athlete_id else 'NOT SET'}")
    print(f"   Intervals Key: {intervals_key[:5] + '...' if intervals_key else 'NOT SET'}")
    print(f"   GitHub Repo: {github_repo or 'NOT SET'}")
    print(f"   GitHub Token: {'SET' if github_token else 'NOT SET'}")
    print(f"   Days: {args.days}")
    print(f"   Version: {IntervalsSync.VERSION}")
    
    if not athlete_id or not intervals_key:
        print("\n❌ Error: Missing credentials.")
        print("   Run: python sync.py --setup")
        return
    
    sync = IntervalsSync(athlete_id, intervals_key, github_token, github_repo, debug=args.debug)
    
    # Manual history generation
    if args.generate_history:
        print(f"\n📊 Generating history.json (up to 3 years)...")
        history = sync.generate_history()
        dr = history.get("data_range", {})
        print(f"\n✅ history.json generated")
        print(f"   Range: {dr.get('earliest')} → {dr.get('latest')} ({dr.get('total_months')} months)")
        print(f"   FTP changes tracked: {len(history.get('ftp_timeline', []))}")
        print(f"   Data gaps found: {len(history.get('data_gaps', []))}")
        
        # Also publish to GitHub if credentials available
        if github_token and github_repo and not args.output:
            print("\n📤 Publishing history.json to GitHub...")
            sync.publish_to_github(history, filepath="history.json",
                                   commit_message=f"Generate history.json - {datetime.now().strftime('%Y-%m-%d')}")
            print("   ✅ history.json pushed to GitHub")
        return
    
    if not args.output and (not github_token or not github_repo):
        print("\n❌ Error: Missing GitHub credentials for push.")
        print("   Either use --output to save locally, or configure GitHub in --setup")
        return
    
    print(f"\n🔄 Fetching {args.days} days of data (extended 28 days for ACWR)...")
    
    data = sync.collect_training_data(days_back=args.days, anonymize=args.anonymize)
    
    # Extract derived metrics for display
    dm = data.get("derived_metrics", {})
    alerts = data.get("alerts", [])
    
    # Common display function
    def print_summary():
        print(f"\n📊 Derived metrics:")
        print(f"   ACWR: {dm.get('acwr')} ({dm.get('acwr_interpretation')})")
        print(f"   Recovery Index: {dm.get('recovery_index')}")
        print(f"   Monotony: {dm.get('monotony')} ({dm.get('monotony_interpretation')})")
        print(f"   Strain: {dm.get('strain')}")
        print(f"   Gray Zone %: {dm.get('grey_zone_percentage')}%")
        print(f"   Quality Intensity %: {dm.get('quality_intensity_percentage')}%")
        print(f"   Polarisation: {dm.get('polarisation_index')} (target ~0.80)")
        print(f"   Consistency: {dm.get('consistency_index')}")
        print(f"   Phase: {dm.get('phase_detected')}")
        print(f"\n📈 Performance (from API):")
        print(f"   eFTP: {dm.get('eftp')}W")
        print(f"   W': {dm.get('w_prime_kj')}kJ")
        print(f"   P-max: {dm.get('p_max')}W")
        print(f"   VO2max: {dm.get('vo2max')}")
        bi_indoor = dm.get('benchmark_indoor', {})
        bi_outdoor = dm.get('benchmark_outdoor', {})
        print(f"   Indoor FTP:  {bi_indoor.get('current_ftp')}W → Benchmark: {bi_indoor.get('benchmark_percentage') or 'N/A (need 8 weeks)'}")
        print(f"   Outdoor FTP: {bi_outdoor.get('current_ftp')}W → Benchmark: {bi_outdoor.get('benchmark_percentage') or 'N/A (need 8 weeks)'}")
        
        # Display alerts
        if alerts:
            print(f"\n⚠️  Alerts ({len(alerts)}):")
            for alert in alerts:
                icon = "🔴" if alert["severity"] == "alarm" else "🟡" if alert["severity"] == "warning" else "ℹ️"
                print(f"   {icon} [{alert['severity'].upper()}] {alert['metric']}: {alert['context']}")
        else:
            print(f"\n✅ No alerts — green light")
        
        # Display history confidence
        history_info = data.get("history", {})
        if history_info.get("available"):
            print(f"\n📚 History: available ({history_info.get('history_confidence')} confidence, {history_info.get('total_months')}mo)")
        else:
            print(f"\n📚 History: not available (will auto-generate on this run)")
    
    if args.output:
        filepath = sync.save_to_file(data, args.output)
        if args.anonymize:
            print(f"   🔒 Anonymization: ENABLED")
        print(f"\n✅ Data saved to {filepath}")
        print_summary()
        print(f"\n💡 Tip: Paste contents to AI, or upload the file directly")
        
        # === AUTO HISTORY GENERATION (local mode) ===
        if sync.should_generate_history():
            try:
                print("\n📊 Auto-generating history.json...")
                history = sync.generate_history()
                history_path = sync.script_dir / sync.HISTORY_FILE
                with open(history_path, 'w') as f:
                    json.dump(history, f, indent=2, default=str)
                print(f"   ✅ history.json saved to {history_path}")
            except Exception as e:
                print(f"   ⚠️ History generation failed (non-critical): {e}")
    else:
        raw_url = sync.publish_to_github(data)
        
        print(f"\n✅ Data published to GitHub")
        if args.anonymize:
            print(f"   🔒 Anonymization: ENABLED")
        print_summary()
        print(f"\n📊 Static URL for LLMs:")
        print(f"   {raw_url}")
        print(f"\n💬 Example prompt:")
        print(f'   "Analyze my training data from {raw_url}"')
        
        # === AUTO HISTORY GENERATION (Sundays/Mondays, first two runs after midnight) ===
        if sync.should_generate_history():
            try:
                print("\n📊 Auto-generating history.json...")
                history = sync.generate_history()
                sync.publish_to_github(history, filepath="history.json",
                                       commit_message=f"Auto-generate history.json - {datetime.now().strftime('%Y-%m-%d')}")
                print("   ✅ history.json auto-generated and pushed to GitHub")
            except Exception as e:
                print(f"   ⚠️ History generation failed (non-critical): {e}")
        
        # === UPDATE NOTIFICATIONS ===
        try:
            print("\n🔔 Checking for upstream updates...")
            sync.check_upstream_updates()
        except Exception as e:
            if args.debug:
                print(f"   ⚠️ Update check failed (non-critical): {e}")


if __name__ == "__main__":
    main()
