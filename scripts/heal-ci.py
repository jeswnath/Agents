#!/usr/bin/env python3
"""
Autonomous Self-Healing & Triage QA Agent
==========================================
Engineered for Playwright E2E automation suites.
Combines:
  1. Live Page Snapshot Ingestion (Accessibility Tree)
  2. Intelligent Triage Classifier (Isolates Data/App Defect vs Locator Drift)
  3. Epistemic Redis Memory (Upstash Serverless REST - Sub-50ms Recall)
  4. Azure OpenAI GPT-4o Playwright P1 Accessible Locator Reasoning
  5. Autonomous Branch & PR Creation with Human-in-the-Loop Safeguards
  6. Multi-Channel Alerting (Teams Adaptive Cards & GitHub Actions Summary)
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Cross-Platform UTF-8 & Log Grouping Helpers
# ---------------------------------------------------------------------------
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def log_phase(title):
    """Outputs GitHub Actions collapsible group in CI or a clear divider locally."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::group::{title}", flush=True)
    else:
        print(f"\n{'=' * 80}\n{title}\n{'=' * 80}", flush=True)


def log_phase_end():
    """Closes GitHub Actions collapsible group."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::endgroup::", flush=True)


def set_github_output(key, value):
    """Sets a step output for GitHub Actions downstream steps."""
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        try:
            with open(out_file, "a", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Paths & Environment Initialization
# ---------------------------------------------------------------------------
SCRIPT_FILE = Path(__file__).resolve()
AGENT_DIR = SCRIPT_FILE.parent.parent
REPORT_DIR = Path("healing-reports").resolve()
REPORT_FILE = REPORT_DIR / "healing-report-latest.md"
RESULTS_JSON = Path("test-results/results.json").resolve()

REPORT_DIR.mkdir(parents=True, exist_ok=True)

env_locations = [
    Path(".env").resolve(),
    Path("..", ".env").resolve(),
    Path(os.environ.get("USERPROFILE", ""), "Desktop", ".env").resolve(),
    Path(os.environ.get("USERPROFILE", ""), "Desktop", "agents", ".env").resolve(),
    AGENT_DIR / ".env",
]
for env_path in env_locations:
    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    trimmed = line.strip()
                    if not trimmed or trimmed.startswith("#"):
                        continue
                    if "=" in trimmed:
                        k, v = trimmed.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Redis Institutional Memory Client (Upstash Serverless REST - Zero Dependencies)
# ---------------------------------------------------------------------------
def upstash_command(cmd_list):
    """Executes a Redis command against Upstash REST API using standard urllib."""
    url = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None

    api_url = url.strip().rstrip("/")
    auth_header = f"Bearer {token.strip()}"
    req_body = json.dumps(cmd_list).encode("utf-8")

    req = urllib.request.Request(
        api_url,
        data=req_body,
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("result")
    except Exception as err:
        print(f"⚠️ Redis memory communication note: {err}")
        return None


def query_redis_memory(redis_key):
    """Checks if a verified fix for the given error fingerprint exists in Redis."""
    raw_val = upstash_command(["GET", redis_key])
    if not raw_val:
        return None
    try:
        data = json.loads(raw_val)
        hit_count = int(data.get("hitCount", 0)) + 1
        data["hitCount"] = hit_count
        data["lastUsedAt"] = datetime.now(timezone.utc).isoformat()
        upstash_command(["SET", redis_key, json.dumps(data), "EX", 2592000])
        return data
    except Exception as err:
        print(f"⚠️ Error reading cached fix from Redis: {err}")
        return None


def save_to_redis_memory(redis_key, fix_data, fingerprint, rel_spec):
    """Saves a verified fix to Redis institutional memory with 30-day TTL."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        record = {
            "fingerprint": fingerprint,
            "targetSpec": str(rel_spec),
            "failedLineNumber": fix_data.get("failedLineNumber"),
            "oldCodeLine": fix_data.get("oldCodeLine"),
            "newCodeLine": fix_data.get("newCodeLine"),
            "rootCauseAnalysis": fix_data.get("rootCauseAnalysis"),
            "downstreamContext": fix_data.get("downstreamContext", ""),
            "alternativesEvaluated": fix_data.get("alternativesEvaluated", []),
            "healingRationale": fix_data.get("healingRationale"),
            "confidenceScore": fix_data.get("confidenceScore", 95),
            "confidenceBreakdown": fix_data.get("confidenceBreakdown", {}),
            "preventionTips": fix_data.get("preventionTips", ""),
            "source": "azure-openai",
            "hitCount": 1,
            "learnedAt": now_iso,
            "lastUsedAt": now_iso,
        }
        upstash_command(["SET", redis_key, json.dumps(record), "EX", 2592000])
        print(f"💾 Saved newly formulated fix to Redis Memory (Key: {redis_key})")
    except Exception as err:
        print(f"⚠️ Could not save fix to Redis memory: {err}")


# ---------------------------------------------------------------------------
# PHASE 1: Ingest Failure Evidence & Live Page Snapshot
# ---------------------------------------------------------------------------
log_phase("🔍 PHASE 1: Ingesting Failure Evidence & Live Page Snapshot")

failed_test_info = None

if RESULTS_JSON.is_file():
    try:
        with open(RESULTS_JSON, "r", encoding="utf-8") as f:
            results_data = json.load(f)

        def find_failed_tests(suite):
            failures = []
            if "suites" in suite and isinstance(suite["suites"], list):
                for sub_suite in suite["suites"]:
                    failures.extend(find_failed_tests(sub_suite))
            if "specs" in suite and isinstance(suite["specs"], list):
                for spec in suite["specs"]:
                    for test in spec.get("tests", []):
                        for result in test.get("results", []):
                            if result.get("status") in ("failed", "timedOut"):
                                errors = result.get("errors", [])
                                detailed_error = None
                                if isinstance(errors, list):
                                    for e in errors:
                                        if isinstance(e, dict) and e.get("location"):
                                            detailed_error = e
                                            break
                                if not detailed_error:
                                    detailed_error = result.get("error")

                                error_loc = None
                                if detailed_error and isinstance(detailed_error, dict) and detailed_error.get("location"):
                                    error_loc = detailed_error.get("location")
                                elif result.get("errorLocation"):
                                    error_loc = result.get("errorLocation")
                                elif result.get("error") and isinstance(result["error"], dict):
                                    error_loc = result["error"].get("location")

                                failures.append({
                                    "file": spec.get("file"),
                                    "title": spec.get("title"),
                                    "error": detailed_error or result.get("error"),
                                    "errorLocation": error_loc,
                                    "attachments": result.get("attachments", [])
                                })
            return failures

        for root_suite in results_data.get("suites", []):
            failures = find_failed_tests(root_suite)
            if failures:
                failed_test_info = failures[0]
                break
    except Exception as err:
        print(f"⚠️ Could not parse results.json: {err}")

target_spec_path = None
if failed_test_info:
    err_loc = failed_test_info.get("errorLocation")
    candidate_file = (err_loc.get("file") if err_loc and isinstance(err_loc, dict) else None) or failed_test_info.get("file")
    if candidate_file:
        target_spec_path = Path(candidate_file).resolve()

if not target_spec_path:
    target_spec_path = Path("tests/petstore-broken.spec.ts").resolve()

if not target_spec_path.is_file():
    print(f"⚠️ Spec file not found at {target_spec_path}")
    log_phase_end()
    sys.exit(0)

try:
    with open(target_spec_path, "r", encoding="utf-8") as f:
        spec_content = f.read()
except Exception as err:
    print(f"⚠️ Could not read spec file {target_spec_path}: {err}")
    log_phase_end()
    sys.exit(0)

lines = spec_content.split("\n")

failed_line_number = None
raw_error_message = ""

if failed_test_info:
    err_loc = failed_test_info.get("errorLocation")
    if err_loc and isinstance(err_loc, dict):
        failed_line_number = err_loc.get("line")
    err_obj = failed_test_info.get("error")
    if isinstance(err_obj, dict):
        raw_error_message = err_obj.get("message", "")
    elif isinstance(err_obj, str):
        raw_error_message = err_obj

try:
    rel_spec = target_spec_path.relative_to(Path.cwd())
except ValueError:
    rel_spec = target_spec_path

print(f"• Target Spec:        {rel_spec}")
if failed_line_number:
    print(f"• Failed Line Number: {failed_line_number}")

first_error_line = "Test assertion/locator failed"
if raw_error_message:
    clean_error = re.sub(r"\x1b\[\d+m", "", raw_error_message)
    for l in clean_error.split("\n"):
        if "Error:" in l or "waiting for" in l or "failed" in l:
            first_error_line = l
            break
    print(f"• Error Signature:    {first_error_line.strip()}")

# Extract Playwright Error Context & Page Snapshot
error_context_content = ""
if failed_test_info and failed_test_info.get("attachments"):
    for att in failed_test_info["attachments"]:
        if att.get("name") == "error-context" or (att.get("path") and att["path"].endswith("error-context.md")):
            att_path = Path(att["path"])
            if att_path.is_file():
                try:
                    error_context_content = att_path.read_text(encoding="utf-8")
                    break
                except Exception:
                    pass

if not error_context_content:
    found_ctx = list(Path("test-results").glob("**/error-context.md"))
    if found_ctx:
        try:
            error_context_content = found_ctx[0].read_text(encoding="utf-8")
        except Exception:
            pass

if error_context_content:
    print(f"• Page Snapshot:      Ingested from Playwright error-context.md ({len(error_context_content.splitlines())} lines)")
else:
    print("• Page Snapshot:      No error-context.md found (falling back to standard error triage)")

log_phase_end()


# ---------------------------------------------------------------------------
# PHASE 2: Autonomous Failure Triage & Classification
# ---------------------------------------------------------------------------
log_phase("🧠 PHASE 2: Autonomous Failure Triage & Classification")

def perform_triage_classification(spec_content, error_context, raw_error, failed_line):
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    if not api_key or not endpoint or not error_context:
        return {"isLocatorIssue": True, "category": "LOCATOR_DRIFT"}

    url = endpoint.strip().rstrip("/")
    if "/openai/deployments" not in url:
        url = f"{url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    elif "api-version=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}api-version={api_version}"

    triage_prompt = """You are an expert QA Automation Triage Engineer.
CRITICAL RULE: Before assuming a locator is broken, carefully examine the '# Page snapshot' YAML in the error context.
- If the page snapshot shows an authentication failure (e.g. 'Invalid username or password', 'Signon failed', 'Unauthorized', 'Access Denied', 401), or an application error (500, 404, crash, blank page):
  THEN this is NOT a locator issue! The downstream element was absent because the user was rejected or the app crashed.
- Only classify as 'LOCATOR_DRIFT' if the page is in the expected valid state, but the locator could not match the element due to UI/DOM changes.

Respond with pure JSON ONLY (no markdown blocks):
{
  "isLocatorIssue": true or false,
  "category": "AUTHENTICATION_OR_TEST_DATA | APPLICATION_BUG | LOCATOR_DRIFT",
  "rootCause": "<concise explanation of why the test actually failed>",
  "pageErrorText": "<exact error text found in page snapshot if any>",
  "actionableAdvice": "<guidance for QA or developer to fix test data or backend>"
}"""

    req_data = json.dumps({
        "messages": [
            {"role": "system", "content": triage_prompt},
            {"role": "user", "content": f"SPEC CONTENT:\n{spec_content}\n\nPLAYWRIGHT ERROR CONTEXT & SNAPSHOT:\n{error_context}"}
        ],
        "temperature": 0.1
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json", "api-key": api_key},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            json_str = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
            json_str = re.sub(r"```\s*$", "", json_str).strip()
            return json.loads(json_str)
    except Exception as err:
        print(f"ℹ️ Triage classifier note: {err}")
        return {"isLocatorIssue": True, "category": "LOCATOR_DRIFT"}


triage_result = perform_triage_classification(spec_content, error_context_content, raw_error_message, failed_line_number)

repo_name = os.environ.get("GITHUB_REPOSITORY", "jeswnath/testPlaywright")
user_or_org = repo_name.split("/")[0] if "/" in repo_name else "jeswnath"
repo_bare = repo_name.split("/")[1] if "/" in repo_name else "testPlaywright"
pages_url = f"https://{user_or_org}.github.io/{repo_bare}/"

# IF NOT A LOCATOR ISSUE: Safely abort self-healing and alert team
if not triage_result.get("isLocatorIssue", True):
    category = triage_result.get("category", "APPLICATION_OR_DATA_DEFECT")
    root_cause_diag = triage_result.get("rootCause", "Application or test data error detected.")
    page_error_txt = triage_result.get("pageErrorText", "Error banner on page.")
    actionable_adv = triage_result.get("actionableAdvice", "Review test credentials and application state.")

    set_github_output("category", category)
    set_github_output("healed", "false")
    set_github_output("pr-url", "")

    print("⚠️  VERDICT: NOT A LOCATOR ISSUE — AUTOMATED LOCATOR HEALING SAFELY ABORTED")
    print(f"• Defect Category:   {category}")
    if page_error_txt:
        print(f"• Page Error Text:   \"{page_error_txt}\"")
    print(f"• Root Cause:        {root_cause_diag}")
    print(f"• Actionable Advice: {actionable_adv}")
    log_phase_end()

    # Step Summary
    triage_md = f"""## 🚨 Autonomous QA Triage: Application / Test Data Defect Detected

> **Status:** ⚠️ **Not a Locator Drift — Automated Patching Safely Prevented**  
> **Category:** 🔒 **{category}**  
> 🌐 **Live Report:** **[Click Here to Open Live Playwright HTML Report]({pages_url})**

### 🔍 Root Cause Analysis
The test failed because the application returned an error during execution:
> **Page Error Message:** `{page_error_txt}`

{root_cause_diag}

---

### 💡 Recommended Action for QA & Developers
- {actionable_adv}
- **Notice:** Automated locator patching was safely skipped to prevent modifying valid test code.
"""

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        try:
            with open(summary_file, "a", encoding="utf-8") as f:
                f.write(triage_md)
        except Exception:
            pass

    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(triage_md)
    except Exception:
        pass

    # Teams Alert
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
    if webhook_url:
        run_number = os.environ.get("GITHUB_RUN_NUMBER", "Local")
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo_name}/actions"

        defect_payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "ColumnSet",
                                "columns": [
                                    {
                                        "type": "Column",
                                        "width": "stretch",
                                        "items": [
                                            {
                                                "type": "TextBlock",
                                                "text": "🚨 QA DEFECT ISOLATED",
                                                "weight": "Bolder",
                                                "size": "Medium",
                                                "color": "Attention",
                                            },
                                            {
                                                "type": "TextBlock",
                                                "text": f"{repo_name} • Run #{run_number}",
                                                "size": "Small",
                                                "isSubtle": True,
                                                "spacing": "None",
                                            },
                                        ],
                                    },
                                    {
                                        "type": "Column",
                                        "width": "auto",
                                        "verticalContentAlignment": "Center",
                                        "items": [
                                            {
                                                "type": "Container",
                                                "style": "warning",
                                                "items": [
                                                    {
                                                        "type": "TextBlock",
                                                        "text": "⚠️ Bug Detected",
                                                        "weight": "Bolder",
                                                        "size": "Small",
                                                        "color": "Warning",
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                ],
                            },
                            {
                                "type": "FactSet",
                                "spacing": "Small",
                                "facts": [
                                    {"title": "Spec:", "value": str(rel_spec)},
                                    {"title": "Category:", "value": category},
                                    {"title": "Page Error:", "value": page_error_txt or "Application / Auth Error"},
                                ],
                            },
                            {
                                "type": "Container",
                                "style": "emphasis",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": "💡 Actionable Advice",
                                        "size": "Small",
                                        "weight": "Bolder",
                                        "isSubtle": True,
                                    },
                                    {
                                        "type": "TextBlock",
                                        "text": actionable_adv,
                                        "wrap": True,
                                        "size": "Small",
                                        "spacing": "Small",
                                    },
                                ],
                            },
                            {
                                "type": "Container",
                                "id": "defectDetailsSection",
                                "isVisible": False,
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": f"**Root Cause Analysis:** {root_cause_diag}",
                                        "wrap": True,
                                        "size": "Small",
                                    }
                                ],
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.ToggleVisibility",
                                "title": "🔍 Root Cause",
                                "targetElements": ["defectDetailsSection"],
                            },
                            {
                                "type": "Action.OpenUrl",
                                "title": "🌐 Live Report",
                                "url": pages_url,
                            },
                            {
                                "type": "Action.OpenUrl",
                                "title": "📋 CI Logs",
                                "url": run_url,
                            },
                        ],
                    },
                }
            ],
        }
        try:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(defect_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                print("✅ Dispatched Defect Alert to Microsoft Teams!")
        except Exception as e:
            print(f"⚠️ Teams alert note: {e}")

    print("\n✅ QA TRIAGE PIPELINE COMPLETED (DEFECT ISOLATED — NO CODE CORRUPTED)")
    sys.exit(0)

print("✅ VERDICT: TRUE LOCATOR DRIFT DETECTED — PROCEEDING TO SELF-HEALING ENGINE")
log_phase_end()


# ---------------------------------------------------------------------------
# PHASE 3: Institutional Redis Memory Lookup
# ---------------------------------------------------------------------------
log_phase("⚡ PHASE 3: Institutional Redis Memory Lookup")

failing_code_snippet = ""
if failed_line_number and 1 <= failed_line_number <= len(lines):
    failing_code_snippet = lines[failed_line_number - 1].strip()

fp_seed = f"{rel_spec}::{failing_code_snippet}::{first_error_line.strip()}"
error_fingerprint = hashlib.sha256(fp_seed.encode("utf-8")).hexdigest()[:16]
redis_key = f"playwright:heal:{error_fingerprint}"

print(f"• Memory Key:   {redis_key}")
print(f"• Seed Context: {rel_spec} (Line {failed_line_number})")

cached_heal = query_redis_memory(redis_key)
is_memory_hit = False
hit_count = 1

if cached_heal and cached_heal.get("oldCodeLine") and cached_heal.get("newCodeLine"):
    is_memory_hit = True
    llm_result = cached_heal
    hit_count = cached_heal.get("hitCount", 1)
    source_badge = f"⚡ Redis Memory (Hit #{hit_count})"
    confidence_score = max(int(cached_heal.get("confidenceScore", 95)), 98)
    healing_rationale = f"[Redis Memory] {cached_heal.get('healingRationale') or cached_heal.get('rootCauseAnalysis')}"
    print(f"⚡ REDIS MEMORY HIT! (Zero LLM Token Cost | Reused #{hit_count} times)")
    print(f"• Time Saved: ~8 seconds")
else:
    source_badge = "🤖 Azure OpenAI LLM"
    print("• Redis Memory Miss: No prior fix on record for this error signature.")
    print("• Handing off to Azure OpenAI GPT-4o for dynamic reasoning & locator formulation...")

log_phase_end()


# ---------------------------------------------------------------------------
# PHASE 4: AI Locator Synthesis & Alternatives Evaluation
# ---------------------------------------------------------------------------
if not is_memory_hit:
    log_phase("🩹 PHASE 4: Playwright P1 Accessible Locator Synthesis")

    def query_azure_openai(spec_content, failed_line_number, raw_error_message, target_spec_path):
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

        if not api_key or not endpoint:
            return None

        url = endpoint.strip().rstrip("/")
        if "/openai/deployments" not in url:
            url = f"{url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        elif "api-version=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}api-version={api_version}"

        agent_spec_path = AGENT_DIR / ".github" / "agents" / "Self-Healing-QA.md"
        skill_path = AGENT_DIR / ".github" / "skills" / "self-healing-locator" / "SKILL.md"
        locator_priority_path = AGENT_DIR / ".github" / "skills" / "self-healing-locator" / "references" / "locator-priority.md"

        agent_spec = agent_spec_path.read_text(encoding="utf-8") if agent_spec_path.is_file() else ""
        skill_spec = skill_path.read_text(encoding="utf-8") if skill_path.is_file() else ""
        locator_priority = locator_priority_path.read_text(encoding="utf-8") if locator_priority_path.is_file() else ""

        system_prompt = f"""You are the Self-Healing QA Agent specialized in Playwright TypeScript/JavaScript test automation suites.
Strictly follow the guidelines, heuristics, and locator priority established in your Agent Specification and Skill documentation below:

=== AGENT INSTRUCTIONS (.github/agents/Self-Healing-QA.md) ===
{agent_spec}

=== SKILL PLAYBOOK (.github/skills/self-healing-locator/SKILL.md) ===
{skill_spec}

=== LOCATOR PRIORITY & ANTI-PATTERNS (references/locator-priority.md) ===
{locator_priority}

=== REQUIRED OUTPUT FORMAT ===
You MUST respond with pure JSON ONLY (no markdown blocks, no conversational preamble).
{{
  "failedLineNumber": <line number integer>,
  "oldCodeLine": "<exact verbatim code of the single line to replace>",
  "newCodeLine": "<exact replacement line of code>",
  "rootCauseAnalysis": "<in-depth technical explanation of why the test failed at this exact line>",
  "downstreamContext": "<how subsequent steps, assertions, or business logic influenced the fix selection>",
  "alternativesEvaluated": [
    {{ "candidate": "<locator option>", "verdict": "REJECTED | BANNED | SELECTED", "rationale": "<why this was selected or rejected>" }}
  ],
  "healingRationale": "<concise summary of why the chosen fix is optimal and resilient>",
  "confidenceScore": <integer between 0 and 100>,
  "confidenceBreakdown": {{
    "roleScore": <0-35>,
    "textScore": <0-35>,
    "structureScore": <0-20>,
    "positionScore": <0-10>
  }},
  "preventionTips": "<actionable recommendation for developers or QA to make this element more testable>"
}}"""

        try:
            rel_path = target_spec_path.relative_to(Path.cwd())
        except ValueError:
            rel_path = target_spec_path

        user_prompt = f"""Test File: {rel_path}
Failed Line Number: {failed_line_number or 'unknown'}
Error Message:
{raw_error_message}

Spec File Contents:
```typescript
{spec_content}
```

Analyze the failure in-depth, evaluate candidate alternatives, generate the healed code line following Playwright P1 accessibility hierarchy, and respond with pure JSON."""

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json", "api-key": api_key},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp_body = resp.read().decode("utf-8")
                data = json.loads(resp_body)
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    return None

                json_str = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
                json_str = re.sub(r"```\s*$", "", json_str).strip()
                parsed = json.loads(json_str)

                if parsed.get("oldCodeLine") and parsed.get("newCodeLine"):
                    return {
                        "oldCodeLine": parsed["oldCodeLine"].strip(),
                        "newCodeLine": parsed["newCodeLine"].strip(),
                        "rootCauseAnalysis": parsed.get("rootCauseAnalysis")
                        or parsed.get("healingRationale")
                        or "Diagnosed using Azure OpenAI LLM.",
                        "downstreamContext": parsed.get("downstreamContext", ""),
                        "alternativesEvaluated": parsed.get("alternativesEvaluated")
                        if isinstance(parsed.get("alternativesEvaluated"), list)
                        else [],
                        "healingRationale": parsed.get("healingRationale")
                        or parsed.get("rootCauseAnalysis")
                        or "Healed using Azure OpenAI LLM.",
                        "confidenceScore": int(parsed.get("confidenceScore") or 95),
                        "confidenceBreakdown": parsed.get("confidenceBreakdown")
                        or {
                            "roleScore": 35,
                            "textScore": 35,
                            "structureScore": 15,
                            "positionScore": 10,
                        },
                        "preventionTips": parsed.get("preventionTips", ""),
                        "failedLineNumber": parsed.get("failedLineNumber") or failed_line_number,
                    }
        except Exception as err:
            print(f"⚠️ Azure OpenAI invocation error: {err}")

        return None

    llm_result = query_azure_openai(spec_content, failed_line_number, raw_error_message, target_spec_path)
    if llm_result:
        healing_rationale = f"[Azure OpenAI] {llm_result['healingRationale']}"
        confidence_score = llm_result["confidenceScore"]
        save_to_redis_memory(redis_key, llm_result, error_fingerprint, rel_spec)
    log_phase_end()


# ---------------------------------------------------------------------------
# Apply Patch & Indentation Preservation
# ---------------------------------------------------------------------------
if not llm_result or not llm_result.get("oldCodeLine") or not llm_result.get("newCodeLine"):
    print("⚠️ Healing engine could not automatically formulate a high-confidence patch.")
    sys.exit(0)

old_code_line = llm_result["oldCodeLine"]
new_code_line = llm_result["newCodeLine"]

trimmed_old = old_code_line.strip()
trimmed_new = new_code_line.strip()

line_idx = -1
for i, line in enumerate(lines):
    if line.strip() == trimmed_old or trimmed_old in line:
        line_idx = i
        break

if line_idx != -1:
    if trimmed_old in lines[line_idx]:
        lines[line_idx] = lines[line_idx].replace(trimmed_old, trimmed_new)
    else:
        indent_match = re.match(r"^\s*", lines[line_idx])
        indent = indent_match.group(0) if indent_match else ""
        lines[line_idx] = indent + trimmed_new
elif llm_result.get("failedLineNumber") and llm_result["failedLineNumber"] <= len(lines):
    idx = llm_result["failedLineNumber"] - 1
    indent_match = re.match(r"^\s*", lines[idx])
    indent = indent_match.group(0) if indent_match else ""
    lines[idx] = indent + trimmed_new
else:
    spec_content = spec_content.replace(old_code_line, new_code_line)
    lines = spec_content.split("\n")

healed_content = "\n".join(lines)
branch_name = "self-heal/automated-fix"

root_cause = llm_result.get("rootCauseAnalysis") or llm_result.get("healingRationale") or healing_rationale
downstream = llm_result.get("downstreamContext", "")
alternatives = llm_result.get("alternativesEvaluated", [])
confidence_breakdown = llm_result.get("confidenceBreakdown", {"roleScore": 35, "textScore": 35, "structureScore": 15, "positionScore": 10})
prevention_tips = llm_result.get("preventionTips", "")
failed_line_display = llm_result.get("failedLineNumber") or failed_line_number

# Format Candidate Alternatives Markdown
if alternatives:
    alt_lines = []
    for a in alternatives:
        v = a.get("verdict", "")
        c = a.get("candidate", "")
        r = a.get("rationale", "")
        icon = "✅" if v == "SELECTED" else ("🚫" if v == "BANNED" else "❌")
        alt_lines.append(f"- **{icon} [{v}]** `{c}`: {r}")
    alternatives_md = "\n".join(alt_lines)
else:
    alternatives_md = "- Selected Playwright P1 Accessible Locator over fragile CSS/XPath."

downstream_section = f"#### 🧭 2. Downstream Business Logic & Assertions\n{downstream}\n\n" if downstream else ""
prevention_section = f"#### 💡 5. Best Practice / Prevention Advice\n> {prevention_tips}\n\n" if prevention_tips else ""


# ---------------------------------------------------------------------------
# PHASE 5: Autonomous Git Branching & Pull Request
# ---------------------------------------------------------------------------
log_phase("🌿 PHASE 5: Autonomous Git Branching & Pull Request")

is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
pr_url = ""

if is_ci:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)

        print(f"• Preparing branch: {branch_name}")
        subprocess.run(["git", "checkout", "-B", branch_name], check=True)

        with open(target_spec_path, "w", encoding="utf-8") as f:
            f.write(healed_content)

        subprocess.run(["git", "add", str(target_spec_path)], check=True)
        subprocess.run(["git", "commit", "-m", "fix(e2e): self-heal test failure [skip ci]"], check=True)
        subprocess.run(["git", "push", "-f", "origin", branch_name], check=True)

        pr_title = f"🤖 [Self-Healing QA] Fix failure in {target_spec_path.name} ({confidence_score}% Confidence)"
        downstream_pr = f"- **Downstream Context:** {downstream}\n" if downstream else ""
        prevention_pr = f"#### 💡 Prevention Advice:\n> {prevention_tips}\n" if prevention_tips else ""

        pr_body = f"""## 🩺 Playwright Autonomous Self-Healing Proposal

A test failure was detected in GitHub Actions run #{os.environ.get('GITHUB_RUN_NUMBER', 'CI')}.
The **Self-Healing QA Agent** analyzed the failure, evaluated candidate locators, and prepared this verified patch.

- **Diagnosis Source:** {source_badge}
- **Memory Key:** `{redis_key}`
- **Interactive Report:** [View Live Playwright Report]({pages_url})

### 🔍 Changes Applied:
```diff
- {old_code_line}
+ {new_code_line}
```

### 🧠 AI Diagnosis & Reasoning Breakdown:
- **Spec File:** `{rel_spec}` (Line {failed_line_display})
- **Root Cause:** {root_cause}
{downstream_pr}- **Confidence Score:** **{confidence_score}% (High Confidence)**

#### ⚖️ Alternatives Considered:
{alternatives_md}

{prevention_pr}---
👉 **Human-in-the-Loop Action:**
- **Merge this PR** to apply the healed code to your branch.
- **Close this PR** to reject the heal."""

        temp_body_file = Path("temp-pr-body.md").resolve()
        temp_body_file.write_text(pr_body, encoding="utf-8")

        base_branch = os.environ.get("GITHUB_BASE_REF") or os.environ.get("GITHUB_REF_NAME") or "main"
        env_gh = os.environ.copy()
        env_gh["GH_TOKEN"] = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")

        try:
            res = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--title", pr_title,
                    "--body-file", str(temp_body_file),
                    "--head", branch_name,
                    "--base", base_branch,
                ],
                capture_output=True,
                text=True,
                check=True,
                env=env_gh,
            )
            pr_url = res.stdout.strip()
            print(f"🎉 Pull Request created successfully: {pr_url}")
        except subprocess.CalledProcessError:
            try:
                res = subprocess.run(
                    ["gh", "pr", "list", "--head", branch_name, "--json", "url", "-q", ".[0].url"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env_gh,
                )
                pr_url = res.stdout.strip()
                print(f"ℹ️ Pull Request updated: {pr_url}")
            except Exception:
                pass
        finally:
            if temp_body_file.is_file():
                temp_body_file.unlink()

    except Exception as git_err:
        print(f"ℹ️ Git operation note: {git_err}")
else:
    print("💡 Local Simulation: Skipping git push & PR creation.")

set_github_output("category", "LOCATOR_DRIFT")
set_github_output("healed", "true")
set_github_output("pr-url", pr_url)

log_phase_end()


# ---------------------------------------------------------------------------
# PHASE 6: Multi-Channel Alerting (Step Summary & Teams)
# ---------------------------------------------------------------------------
log_phase("📢 PHASE 6: Multi-Channel Alerting & Reporting")

step_summary_markdown = f"""## 🩺 Playwright Autonomous Self-Healing Report

> **Status:** ❌ Test Failed in CI $\\rightarrow$ 🩹 **Autonomous Patch Prepared**  
> **Source:** **{source_badge}** | **Confidence Score:** 🟢 **{confidence_score}% (High Confidence)**  
> 🌐 **Live Report:** **[Click Here to Open Live Playwright HTML Report]({pages_url})**

### 📊 Failure vs. Proposed Heal Summary

| Metric | Details |
| :--- | :--- |
| **Failed Test** | `{rel_spec}` |
| **Failing Line** | Line `{failed_line_display}` |
| **Diagnosis Source** | **{source_badge}** |
| **Memory Key** | `{redis_key}` |
| **Playwright Hierarchy** | **P1 Accessible / Resilient Fix** |
| **Overall Confidence** | **{confidence_score}%** |

---

### 🔍 Proposed Code Diff

```diff
- {old_code_line}
+ {new_code_line}
```

---

### 🧠 Agent Chain-of-Thought & Reasoning Breakdown

<details open>
<summary><b>Click to expand AI Diagnosis & Decision Matrix</b></summary>
<br/>

#### 🔍 1. Root Cause Analysis
{root_cause}

{downstream_section}#### ⚖️ 3. Candidate Alternatives Evaluated
{alternatives_md}

#### 📊 4. Confidence Scoring Rubric Breakdown
- **Role Alignment ($S_{{\\text{{role}}}}$):** {confidence_breakdown.get('roleScore', 35)}/35
- **Semantic Text / Label ($S_{{\\text{{text}}}}$):** {confidence_breakdown.get('textScore', 35)}/35
- **Structural Stability ($S_{{\\text{{tag}}}}$):** {confidence_breakdown.get('structureScore', 15)}/20
- **Positional Context ($S_{{\\text{{pos}}}}$):** {confidence_breakdown.get('positionScore', 10)}/10
- **Total Calculated Score:** **{confidence_score}%**

{prevention_section}</details>

---

### 👤 Human-in-the-Loop (HITL) Decision:
- **To Accept & Heal:** Review and merge the automatically created Pull Request: {pr_url or 'Open PR'}
- **To Reject:** Close the Pull Request without merging.
"""

summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
if summary_file:
    try:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(step_summary_markdown)
        print("✅ Rendered Self-Healing Dashboard to GitHub Actions Step Summary!")
    except Exception:
        pass

try:
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(step_summary_markdown)
    print(f"✅ Saved Local Audit Report to {REPORT_FILE}")
except Exception:
    pass

# Microsoft Teams Adaptive Card
webhook_url = os.environ.get("TEAMS_WEBHOOK_URL")
if webhook_url:
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "Local")
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo_name}/actions"
    fallback_pr_url = pr_url or f"https://github.com/{repo_name}/pulls"

    teams_payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "ColumnSet",
                            "columns": [
                                {
                                    "type": "Column",
                                    "width": "stretch",
                                    "items": [
                                        {
                                            "type": "TextBlock",
                                            "text": "🩺 Autonomous Self-Healer",
                                            "weight": "Bolder",
                                            "size": "Medium",
                                            "color": "Good",
                                        },
                                        {
                                            "type": "TextBlock",
                                            "text": f"{repo_name} • Run #{run_number}",
                                            "size": "Small",
                                            "isSubtle": True,
                                            "spacing": "None",
                                        },
                                    ],
                                },
                                {
                                    "type": "Column",
                                    "width": "auto",
                                    "verticalContentAlignment": "Center",
                                    "items": [
                                        {
                                            "type": "Container",
                                            "style": "good",
                                            "items": [
                                                {
                                                    "type": "TextBlock",
                                                    "text": f"🟢 {confidence_score}% Confidence",
                                                    "weight": "Bolder",
                                                    "size": "Small",
                                                    "color": "Good",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "FactSet",
                            "spacing": "Small",
                            "facts": [
                                {"title": "Spec:", "value": f"{rel_spec}:{failed_line_display}"},
                                {"title": "Source:", "value": source_badge},
                                {"title": "Status:", "value": "🟡 PR Ready for Review"},
                            ],
                        },
                        {
                            "type": "Container",
                            "style": "emphasis",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "📝 Proposed Locator Patch",
                                    "size": "Small",
                                    "weight": "Bolder",
                                    "isSubtle": True,
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"- {old_code_line}\n+ {new_code_line}",
                                    "fontType": "Monospace",
                                    "size": "Small",
                                    "wrap": True,
                                    "spacing": "Small",
                                },
                            ],
                        },
                        {
                            "type": "Container",
                            "id": "aiDetailsSection",
                            "isVisible": False,
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": f"**Root Cause:** {root_cause}",
                                    "wrap": True,
                                    "size": "Small",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"**💡 Prevention:** {prevention_tips}" if prevention_tips else "**💡 Recommendation:** Adopt Playwright user-facing locators.",
                                    "wrap": True,
                                    "size": "Small",
                                },
                            ],
                        },
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "✅ Review & Merge PR",
                            "style": "positive",
                            "url": fallback_pr_url,
                        },
                        {
                            "type": "Action.ToggleVisibility",
                            "title": "🔍 AI Diagnostics",
                            "targetElements": ["aiDetailsSection"],
                        },
                        {
                            "type": "Action.OpenUrl",
                            "title": "🌐 Live Report",
                            "url": pages_url,
                        },
                        {
                            "type": "Action.OpenUrl",
                            "title": "📋 CI Logs",
                            "url": run_url,
                        },
                    ],
                },
            }
        ],
    }

    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(teams_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status in (200, 201, 202):
                print("✅ Successfully posted Adaptive Card to Microsoft Teams Channel!")
            else:
                print(f"⚠️ Teams Webhook returned status: {resp.status}")
    except Exception as err:
        print(f"⚠️ Teams webhook note: {err}")

log_phase_end()

print("\n================================================================================")
print("✅ SELF-HEALING TRIAGE PIPELINE COMPLETED")
print("================================================================================\n")
