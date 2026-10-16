---
name: "self-healing-locator"
description: "Diagnoses Playwright test failures, detects broken or drifted locators, predicts the correct replacement using semantic hierarchy, heals test code, and verifies execution."
---

# Self-Healing Locator Skill

## 1. Purpose & Core Heuristic
When automated Playwright tests fail in CI or local runs, 70%+ of failures stem from **UI locator drift** (renamed buttons, updated classes, modified input names) rather than real product bugs.
This skill equips the agent to:
1. Parse the Playwright failure context (failing line, error message, locator string, page URL).
2. Distinguish **Automation Drift** vs. **Genuine App Regression**.
3. Re-scan the target page DOM to discover candidate elements.
4. Rank replacement candidates using the **Playwright Locator Priority Hierarchy**.
5. Patch the test spec file with the best-matching locator.
6. Re-run Playwright to verify the test passes.
7. Generate a Teams Adaptive Card and Markdown Audit Dossier.

---

## 2. Playwright Locator Priority Hierarchy
When suggesting or healing a locator, ALWAYS follow Playwright's official user-facing priority:

| Priority | Locator API | Example | Why? |
| :---: | :--- | :--- | :--- |
| **P1 (Highest)** | `page.getByRole()` | `page.getByRole('link', { name: 'Sign In' })` | Closest to user interaction, resilient to DOM structure changes |
| **P2** | `page.getByText()` | `page.getByText('Angelfish')` | Resilient content match |
| **P3** | `page.getByLabel()` | `page.getByLabel('User Name')` | Standard accessible form input |
| **P4** | `page.getByPlaceholder()` | `page.getByPlaceholder('Search...')` | Great for search & text boxes |
| **P5** | `page.getByTestId()` | `page.getByTestId('checkout-btn')` | Explicit QA hook |
| **P6 (Fallback)** | Semantic CSS / Tag | `page.locator('input[name="username"]')` | Only when accessible roles are unavailable |
| **BANNED** | Brittle XPath / Deep CSS | `div > div:nth-child(3) > a.btn-primary` | Strictly forbidden; guaranteed to break on refactors |

---

## 3. Decision Matrix: Bug vs. Locator Drift

```mermaid
flowchart TD
    Fail["❌ Test Failure Ingested"] --> ErrCheck{"What is the error type?"}
    
    ErrCheck -->|"Timeout waiting for locator"| DomCheck{"Does target element exist on page under different attribute/text?"}
    ErrCheck -->|"Assertion Failed (500, expected 'Paid' got 'Failed')"| RealBug["🚨 Genuine App Defect<br/>• File Jira ticket<br/>• Post alert to Teams"]
    
    DomCheck -->|Yes| Heal["🩹 Locator Drift Detected<br/>• Candidate Confidence >= 80%<br/>• Patch spec file<br/>• Re-run & verify test"]
    DomCheck -->|No (Page 404/blank/crash)| RealBug

    style Fail fill:#fee2e2,stroke:#ef4444,stroke-width:2px,color:#991b1b
    style RealBug fill:#fee2e2,stroke:#ef4444,stroke-width:2px,color:#991b1b
    style Heal fill:#ecfdf5,stroke:#10b981,stroke-width:2px,color:#065f46
```

---

## 4. Confidence Scoring Rubric for Candidate Locators

When selecting a replacement locator for element $E$, calculate the **Healing Confidence Score**:

$$\text{Confidence Score} = S_{\text{role}} (35\%) + S_{\text{text/label}} (35\%) + S_{\text{type/tag}} (20\%) + S_{\text{position}} (10\%)$$

- **>= 80%**: Safe to auto-heal. Automatically update file and re-verify.
- **50% - 79%**: Ambiguous match (multiple candidates). Present top 2 candidates in Teams Card with Human-in-the-Loop selection.
- **< 50%**: Element completely removed or missing. Flag as potential functional defect or missing feature.

---

## 5. Output Deliverables
Every healing run must generate:
1. **Patched Test File**: Clean, formatted TypeScript/JavaScript diff.
2. **Verification Log**: Successful re-execution confirmation (`1 passed`).
3. **Audit Dossier (`healing-reports/healing-report-<timestamp>.md`)**:
   - Executive Summary table
   - Before/After code diff block
   - Teams Adaptive Card preview payload

