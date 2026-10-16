# 🩺 Playwright Test Self-Healing Audit Dossier

**Execution Date:** `{{timestamp}}`  
**Target Suite:** `{{suite_name}}`  
**Spec File:** `{{spec_path}}`  
**Overall Verdict:** `{{status_badge}}`

---

## 1. 📊 Executive Summary Table

| Metric | Before Self-Healing | After Self-Healing |
| :--- | :--- | :--- |
| **Test Status** | ❌ Failed (`Timeout / LocatorNotFound`) | ✅ Passed (`1 passed`) |
| **Root Cause** | Automation Locator Drift | Healed to accessible locator |
| **Confidence Score** | — | **{{confidence_score}}%** |
| **Manual QA Effort Saved** | — | **~45 minutes** |

---

## 2. 🔍 Healed Locator Diff

```diff
- {{old_code_line}}
+ {{new_code_line}}
```

**Rationale:**  
{{healing_rationale}}

---

## 3. 🧪 Re-Run Verification Output

```text
{{playwright_rerun_logs}}
```

---

## 4. 💬 Microsoft Teams Notification Payload (HITL)

```json
{{teams_payload}}
```

