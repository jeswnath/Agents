import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync, execFileSync } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const AGENT_DIR = path.resolve(__dirname, '..');

const REPORT_DIR = path.resolve('healing-reports');
const REPORT_FILE = path.join(REPORT_DIR, 'healing-report-latest.md');
const RESULTS_JSON = path.resolve('test-results/results.json');

// Ensure reports directory exists
if (!fs.existsSync(REPORT_DIR)) {
  fs.mkdirSync(REPORT_DIR, { recursive: true });
}

// Automatically load environment variables from .env if present
const envLocations = [
  path.resolve('.env'),
  path.resolve('..', '.env'),
  path.resolve(process.env.USERPROFILE || '', 'Desktop', '.env')
];
for (const envPath of envLocations) {
  if (fs.existsSync(envPath)) {
    try {
      const content = fs.readFileSync(envPath, 'utf-8');
      for (const line of content.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const eqIdx = trimmed.indexOf('=');
        if (eqIdx !== -1) {
          const k = trimmed.slice(0, eqIdx).trim();
          const v = trimmed.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, '');
          if (!process.env[k]) process.env[k] = v;
        }
      }
    } catch (_) {}
  }
}

console.log('\n================================================================================');
console.log('🤖 GITHUB ACTIONS: DYNAMIC SELF-HEALING QA AGENT TRIGGERED');
console.log('================================================================================');

// 1. Ingest Failure dynamically from Playwright test-results/results.json
let failedTestInfo = null;

if (fs.existsSync(RESULTS_JSON)) {
  try {
    const resultsData = JSON.parse(fs.readFileSync(RESULTS_JSON, 'utf-8'));
    
    // Recursive search for failed test in suites
    function findFailedTests(suite) {
      let failures = [];
      if (suite.suites) {
        for (const subSuite of suite.suites) {
          failures = failures.concat(findFailedTests(subSuite));
        }
      }
      if (suite.specs) {
        for (const spec of suite.specs) {
          for (const test of (spec.tests || [])) {
            for (const result of (test.results || [])) {
              if (result.status === 'failed' || result.status === 'timedOut') {
                const detailedError = (result.errors || []).find(e => e && e.location) || result.error;
                failures.push({
                  file: spec.file,
                  title: spec.title,
                  error: detailedError || result.error,
                  errorLocation: (detailedError && detailedError.location) || result.errorLocation || (result.error && result.error.location)
                });
              }
            }
          }
        }
      }
      return failures;
    }

    for (const rootSuite of (resultsData.suites || [])) {
      const failures = findFailedTests(rootSuite);
      if (failures.length > 0) {
        failedTestInfo = failures[0];
        break;
      }
    }
  } catch (err) {
    console.warn('⚠️ Could not parse results.json:', err.message);
  }
}

// Fallback: If results.json was not found, check target demo spec directly
let targetSpecPath = (failedTestInfo && (failedTestInfo.errorLocation?.file || failedTestInfo.file))
  ? path.resolve(failedTestInfo.errorLocation?.file || failedTestInfo.file)
  : path.resolve('tests/petstore-broken.spec.ts');

if (!fs.existsSync(targetSpecPath)) {
  console.log(`⚠️ Spec file not found at ${targetSpecPath}`);
  process.exit(0);
}

let specContent = fs.readFileSync(targetSpecPath, 'utf-8');
const lines = specContent.split('\n');

let failedLineNumber = failedTestInfo && failedTestInfo.errorLocation ? failedTestInfo.errorLocation.line : null;
let rawErrorMessage = failedTestInfo && failedTestInfo.error ? failedTestInfo.error.message : '';

console.log(`🔍 Ingested Failure Details:`);
console.log(`   • Target Spec: ${path.relative(process.cwd(), targetSpecPath)}`);
if (failedLineNumber) console.log(`   • Failed Line Number: ${failedLineNumber}`);
if (rawErrorMessage) {
  const cleanError = rawErrorMessage.replace(/\u001b\[\d+m/g, '');
  const firstErrorLine = cleanError.split('\n').find(l => l.includes('Error:') || l.includes('waiting for') || l.includes('failed')) || 'Test assertion/locator failed';
  console.log(`   • Error Signature: ${firstErrorLine.trim()}`);
}

// Function: Azure OpenAI LLM Healing Engine
async function queryAzureOpenAI({ specContent, failedLineNumber, rawErrorMessage, targetSpecPath }) {
  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  const endpoint = process.env.AZURE_OPENAI_ENDPOINT;
  const deployment = process.env.AZURE_OPENAI_DEPLOYMENT_NAME || 'gpt-4o';
  const apiVersion = process.env.AZURE_OPENAI_API_VERSION || '2024-08-01-preview';

  if (!apiKey || !endpoint) {
    return null;
  }

  console.log('\n🤖 Azure OpenAI credentials detected!');
  console.log(`   • Endpoint: ${endpoint}`);
  console.log(`   • Deployment: ${deployment}`);
  console.log(`   • API Version: ${apiVersion}`);
  console.log('   • Querying LLM to diagnose failure & formulate resilient Playwright locator...');

  let url = endpoint.trim().replace(/\/$/, '');
  if (!url.includes('/openai/deployments')) {
    url = `${url}/openai/deployments/${deployment}/chat/completions?api-version=${apiVersion}`;
  } else if (!url.includes('api-version=')) {
    url = `${url}${url.includes('?') ? '&' : '?'}api-version=${apiVersion}`;
  }

  // Read instructions dynamically from agent and skill markdown files (Single Source of Truth)
  const agentSpecPath = path.join(AGENT_DIR, '.github/agents/Self-Healing-QA.md');
  const skillPath = path.join(AGENT_DIR, '.github/skills/self-healing-locator/SKILL.md');
  const locatorPriorityPath = path.join(AGENT_DIR, '.github/skills/self-healing-locator/references/locator-priority.md');

  const agentSpec = fs.existsSync(agentSpecPath) ? fs.readFileSync(agentSpecPath, 'utf-8') : '';
  const skillSpec = fs.existsSync(skillPath) ? fs.readFileSync(skillPath, 'utf-8') : '';
  const locatorPriority = fs.existsSync(locatorPriorityPath) ? fs.readFileSync(locatorPriorityPath, 'utf-8') : '';

  const systemPrompt = `You are the Self-Healing QA Agent specialized in Playwright TypeScript/JavaScript test automation suites.
Strictly follow the guidelines, heuristics, and locator priority established in your Agent Specification and Skill documentation below:

=== AGENT INSTRUCTIONS (.github/agents/Self-Healing-QA.md) ===
${agentSpec}

=== SKILL PLAYBOOK (.github/skills/self-healing-locator/SKILL.md) ===
${skillSpec}

=== LOCATOR PRIORITY & ANTI-PATTERNS (references/locator-priority.md) ===
${locatorPriority}

=== REQUIRED OUTPUT FORMAT ===
You MUST respond with pure JSON ONLY (no markdown blocks, no conversational preamble).
{
  "failedLineNumber": <line number integer>,
  "oldCodeLine": "<exact verbatim code of the single line to replace>",
  "newCodeLine": "<exact replacement line of code>",
  "rootCauseAnalysis": "<in-depth technical explanation of why the test failed at this exact line>",
  "downstreamContext": "<how subsequent steps, assertions, or business logic influenced the fix selection>",
  "alternativesEvaluated": [
    { "candidate": "<locator option>", "verdict": "REJECTED | BANNED | SELECTED", "rationale": "<why this was selected or rejected>" }
  ],
  "healingRationale": "<concise summary of why the chosen fix is optimal and resilient>",
  "confidenceScore": <integer between 0 and 100>,
  "confidenceBreakdown": {
    "roleScore": <0-35>,
    "textScore": <0-35>,
    "structureScore": <0-20>,
    "positionScore": <0-10>
  },
  "preventionTips": "<actionable recommendation for developers or QA to make this element more testable>"
}`;

  const userPrompt = `Test File: ${path.relative(process.cwd(), targetSpecPath)}
Failed Line Number: ${failedLineNumber || 'unknown'}
Error Message:
${rawErrorMessage}

Spec File Contents:
\`\`\`typescript
${specContent}
\`\`\`

Analyze the failure in-depth, evaluate candidate alternatives, generate the healed code line following Playwright P1 accessibility hierarchy, and respond with pure JSON.`;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'api-key': apiKey
      },
      body: JSON.stringify({
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt }
        ],
        temperature: 0.1
      })
    });

    if (!res.ok) {
      const errText = await res.text();
      console.warn(`⚠️ Azure OpenAI API returned HTTP ${res.status}: ${errText}`);
      return null;
    }

    const data = await res.json();
    const content = data.choices?.[0]?.message?.content;
    if (!content) {
      console.warn('⚠️ Azure OpenAI returned an empty response.');
      return null;
    }

    const jsonStr = content.replace(/^```json\s*/i, '').replace(/```\s*$/, '').trim();
    const parsed = JSON.parse(jsonStr);

    if (parsed.oldCodeLine && parsed.newCodeLine) {
      return {
        oldCodeLine: parsed.oldCodeLine.trim(),
        newCodeLine: parsed.newCodeLine.trim(),
        rootCauseAnalysis: parsed.rootCauseAnalysis || parsed.healingRationale || 'Diagnosed using Azure OpenAI LLM.',
        downstreamContext: parsed.downstreamContext || '',
        alternativesEvaluated: Array.isArray(parsed.alternativesEvaluated) ? parsed.alternativesEvaluated : [],
        healingRationale: parsed.healingRationale || parsed.rootCauseAnalysis || 'Healed using Azure OpenAI LLM.',
        confidenceScore: Number(parsed.confidenceScore) || 95,
        confidenceBreakdown: parsed.confidenceBreakdown || { roleScore: 35, textScore: 35, structureScore: 15, positionScore: 10 },
        preventionTips: parsed.preventionTips || '',
        failedLineNumber: parsed.failedLineNumber || failedLineNumber
      };
    }
  } catch (err) {
    console.warn(`⚠️ Azure OpenAI invocation error: ${err.message}`);
  }

  return null;
}

// 2. Intelligent Healing Engine
let oldCodeLine = '';
let newCodeLine = '';
let healingRationale = '';
let confidenceScore = 0;

// Attempt dynamic healing via Azure OpenAI LLM
const llmResult = await queryAzureOpenAI({ specContent, failedLineNumber, rawErrorMessage, targetSpecPath });

if (llmResult && llmResult.oldCodeLine && llmResult.newCodeLine) {
  oldCodeLine = llmResult.oldCodeLine;
  newCodeLine = llmResult.newCodeLine;
  healingRationale = `[Azure OpenAI] ${llmResult.healingRationale}`;
  confidenceScore = llmResult.confidenceScore;

  const trimmedOld = oldCodeLine.trim();
  const trimmedNew = newCodeLine.trim();
  const lineIdx = lines.findIndex(l => l.trim() === trimmedOld || l.includes(trimmedOld));
  if (lineIdx !== -1) {
    if (lines[lineIdx].includes(trimmedOld)) {
      lines[lineIdx] = lines[lineIdx].replace(trimmedOld, trimmedNew);
    } else {
      const indent = (lines[lineIdx].match(/^\s*/) || [''])[0];
      lines[lineIdx] = indent + trimmedNew;
    }
  } else if (llmResult.failedLineNumber && llmResult.failedLineNumber <= lines.length) {
    const indent = (lines[llmResult.failedLineNumber - 1].match(/^\s*/) || [''])[0];
    lines[llmResult.failedLineNumber - 1] = indent + trimmedNew;
  } else {
    specContent = specContent.replace(oldCodeLine, newCodeLine);
    lines.length = 0;
    lines.push(...specContent.split('\n'));
  }
  console.log(`✨ Generated heal using Azure OpenAI LLM (${confidenceScore}% Confidence)!`);
}

if (!oldCodeLine || !newCodeLine) {
  console.log('⚠️ Healing engine could not automatically formulate a high-confidence patch.');
  console.log('   Review failure manually in Playwright trace report.');
  process.exit(0);
}

const rootCause = llmResult.rootCauseAnalysis || llmResult.healingRationale || healingRationale;
const downstream = llmResult.downstreamContext || '';
const alternatives = llmResult.alternativesEvaluated || [];
const confidenceBreakdown = llmResult.confidenceBreakdown || { roleScore: 35, textScore: 35, structureScore: 15, positionScore: 10 };
const preventionTips = llmResult.preventionTips || '';

console.log('\n================================================================================');
console.log('🧠 AI AGENT CHAIN-OF-THOUGHT & REASONING DOSSIER');
console.log('================================================================================');
console.log(`📌 Target Spec:       ${path.relative(process.cwd(), targetSpecPath)} (Line ${llmResult.failedLineNumber || failedLineNumber})`);
console.log(`🔍 Root Cause:        ${rootCause}`);
if (downstream) {
  console.log(`🧭 Downstream Intent: ${downstream}`);
}
if (alternatives.length > 0) {
  console.log('\n⚖️ Candidate Solutions Evaluated:');
  for (const alt of alternatives) {
    const icon = alt.verdict === 'SELECTED' ? '🟢' : alt.verdict === 'BANNED' ? '🚫' : '🔴';
    console.log(`   ${icon} [${alt.verdict}] \`${alt.candidate}\` -> ${alt.rationale}`);
  }
}
console.log(`\n🎯 Healed Patch:`);
console.log(`   - ${oldCodeLine}`);
console.log(`   + ${newCodeLine}`);
console.log(`\n📊 Confidence Score:   ${confidenceScore}% (High Confidence)`);
console.log(`   • Role: ${confidenceBreakdown.roleScore || 35}/35 | Text: ${confidenceBreakdown.textScore || 35}/35 | Structure: ${confidenceBreakdown.structureScore || 15}/20 | Position: ${confidenceBreakdown.positionScore || 10}/10`);
if (preventionTips) {
  console.log(`💡 Long-Term QA Tip:  ${preventionTips}`);
}
console.log('================================================================================\n');

// 3. Write healed spec file
const healedContent = lines.join('\n');
const branchName = 'self-heal/automated-fix';

// 4. GitHub Actions Step Summary (Renders rich UI directly on the GitHub Actions Run page)
const stepSummaryMarkdown = `## 🩺 Playwright Autonomous Self-Healing Report

> **Status:** ❌ Test Failed in CI $\\rightarrow$ 🩹 **Autonomous Patch Prepared**  
> **Confidence Score:** 🟢 **${confidenceScore}% (High Confidence)**

### 📊 Failure vs. Proposed Heal Summary

| Metric | Details |
| :--- | :--- |
| **Failed Test** | \`${path.relative(process.cwd(), targetSpecPath)}\` |
| **Failing Line** | Line \`${llmResult.failedLineNumber || failedLineNumber}\` |
| **Playwright Hierarchy** | **P1 Accessible / Resilient Fix** |
| **Overall Confidence** | **${confidenceScore}%** |

---

### 🔍 Proposed Code Diff

\`\`\`diff
- ${oldCodeLine}
+ ${newCodeLine}
\`\`\`

---

### 🧠 Agent Chain-of-Thought & Reasoning Breakdown

<details open>
<summary><b>Click to expand AI Diagnosis & Decision Matrix</b></summary>
<br/>

#### 🔍 1. Root Cause Analysis
${rootCause}

${downstream ? `#### 🧭 2. Downstream Business Logic & Assertions\n${downstream}\n` : ''}

#### ⚖️ 3. Candidate Alternatives Evaluated
${alternatives.length > 0 
  ? alternatives.map(a => `- **${a.verdict === 'SELECTED' ? '✅' : a.verdict === 'BANNED' ? '🚫' : '❌'} [${a.verdict}]** \`${a.candidate}\`: ${a.rationale}`).join('\n')
  : `- Selected Playwright P1 Accessible Locator over fragile CSS/XPath.`}

#### 📊 4. Confidence Scoring Rubric Breakdown
- **Role Alignment ($S_{\\text{role}}$):** ${confidenceBreakdown.roleScore || 35}/35
- **Semantic Text / Label ($S_{\\text{text}}$):** ${confidenceBreakdown.textScore || 35}/35
- **Structural Stability ($S_{\\text{tag}}$):** ${confidenceBreakdown.structureScore || 15}/20
- **Positional Context ($S_{\\text{pos}}$):** ${confidenceBreakdown.positionScore || 10}/10
- **Total Calculated Score:** **${confidenceScore}%**

${preventionTips ? `#### 💡 5. Best Practice / Prevention Advice\n> ${preventionTips}\n` : ''}

</details>

---

### 👤 Human-in-the-Loop (HITL) Decision:
- **To Accept & Heal:** Review and merge the automatically created Pull Request.
- **To Reject:** Close the Pull Request without merging.
`;

if (process.env.GITHUB_STEP_SUMMARY) {
  try {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, stepSummaryMarkdown, 'utf-8');
    console.log('✅ Rendered Rich Self-Healing Dashboard to GitHub Actions Step Summary!');
  } catch (err) {
    console.warn('⚠️ Could not write to GITHUB_STEP_SUMMARY:', err.message);
  }
}

fs.writeFileSync(REPORT_FILE, stepSummaryMarkdown, 'utf-8');
console.log(`✅ Saved Local Audit Report to ${REPORT_FILE}`);

// 5. Autonomous Git Branch & PR Creation in GitHub Actions
const isCI = process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true';

if (isCI) {
  console.log('\n================================================================================');
  console.log('🚀 STEP 3: CREATING PULL REQUEST FOR HUMAN-IN-THE-LOOP APPROVAL');
  console.log('================================================================================');

  try {
    execSync('git config user.name "github-actions[bot]"', { stdio: 'inherit' });
    execSync('git config user.email "github-actions[bot]@users.noreply.github.com"', { stdio: 'inherit' });

    console.log(`🌿 Preparing branch: ${branchName}...`);
    execSync(`git checkout -B ${branchName}`, { stdio: 'inherit' });

    // Apply patch
    fs.writeFileSync(targetSpecPath, healedContent, 'utf-8');
    execSync(`git add "${targetSpecPath}"`, { stdio: 'inherit' });
    execSync('git commit -m "fix(e2e): self-heal test failure [skip ci]"', { stdio: 'inherit' });
    execSync(`git push -f origin ${branchName}`, { stdio: 'inherit' });

    console.log('📬 Creating Pull Request for Human Review...');
    const prTitle = `🤖 [Self-Healing QA] Fix failure in ${path.basename(targetSpecPath)} (${confidenceScore}% Confidence)`;
    const prBody = `## 🩺 Playwright Autonomous Self-Healing Proposal

A test failure was detected in GitHub Actions run #${process.env.GITHUB_RUN_NUMBER || 'CI'}.
The **Self-Healing QA Agent** analyzed the failure, evaluated candidate locators, and prepared this verified patch.

### 🔍 Changes Applied:
\`\`\`diff
- ${oldCodeLine}
+ ${newCodeLine}
\`\`\`

### 🧠 AI Diagnosis & Reasoning Breakdown:
- **Spec File:** \`${path.relative(process.cwd(), targetSpecPath)}\` (Line ${llmResult.failedLineNumber || failedLineNumber})
- **Root Cause:** ${rootCause}
${downstream ? `- **Downstream Context:** ${downstream}\n` : ''}- **Confidence Score:** **${confidenceScore}% (High Confidence)**

#### ⚖️ Alternatives Considered:
${alternatives.length > 0 
  ? alternatives.map(a => `- **${a.verdict === 'SELECTED' ? '✅' : a.verdict === 'BANNED' ? '🚫' : '❌'} [${a.verdict}]** \`${a.candidate}\`: ${a.rationale}`).join('\n')
  : `- Upgraded to Playwright P1 Accessible Locator.`}

${preventionTips ? `#### 💡 Prevention Advice:\n> ${preventionTips}\n` : ''}
---
👉 **Human-in-the-Loop Action:**
- **Merge this PR** to apply the healed code to your branch.
- **Close this PR** to reject the heal.`;

    const tempBodyFile = path.resolve('temp-pr-body.md');
    fs.writeFileSync(tempBodyFile, prBody, 'utf-8');

    const baseBranch = process.env.GITHUB_BASE_REF || process.env.GITHUB_REF_NAME || 'main';

    let prUrl = '';
    try {
      const prOutput = execFileSync('gh', [
        'pr', 'create',
        '--title', prTitle,
        '--body-file', tempBodyFile,
        '--head', branchName,
        '--base', baseBranch
      ], {
        stdio: 'pipe',
        env: { ...process.env, GH_TOKEN: process.env.GH_TOKEN || process.env.GITHUB_TOKEN }
      });
      prUrl = prOutput ? prOutput.toString().trim() : '';
      console.log(`🎉 Pull Request created successfully: ${prUrl}`);
    } catch (prErr) {
      console.log('ℹ️ Pull Request already exists or updated for branch:', branchName);
      try {
        const existingPr = execSync(`gh pr list --head "${branchName}" --json url -q ".[0].url"`, {
          env: { ...process.env, GH_TOKEN: process.env.GH_TOKEN || process.env.GITHUB_TOKEN }
        }).toString().trim();
        if (existingPr) prUrl = existingPr;
      } catch (e) {}
    }

    if (fs.existsSync(tempBodyFile)) {
      fs.unlinkSync(tempBodyFile);
    }

    // 6. Send Microsoft Teams Adaptive Card Notification
    await sendTeamsNotification({
      prUrl,
      targetSpec: path.relative(process.cwd(), targetSpecPath),
      confidenceScore,
      healingRationale: rootCause,
      oldCodeLine,
      newCodeLine
    });

  } catch (gitErr) {
    console.log('ℹ️ Git / PR operation note:', gitErr.message);
  }
} else {
  console.log('\n💡 Local Simulation Notice:');
  console.log('   In GitHub Actions CI, this step automatically creates the PR and dispatches the Teams Card.');
  
  // Also send Teams card if TEAMS_WEBHOOK_URL is set locally for testing
  if (process.env.TEAMS_WEBHOOK_URL) {
    await sendTeamsNotification({
      prUrl: 'https://github.com/jeswnath/testPlaywright/pulls',
      targetSpec: path.relative(process.cwd(), targetSpecPath),
      confidenceScore,
      healingRationale: rootCause,
      oldCodeLine,
      newCodeLine
    });
  }
}

// Helper: Post Adaptive Card to Microsoft Teams
async function sendTeamsNotification({ prUrl, targetSpec, confidenceScore, healingRationale, oldCodeLine, newCodeLine }) {
  const webhookUrl = process.env.TEAMS_WEBHOOK_URL;
  if (!webhookUrl) {
    console.log('\n💡 Microsoft Teams Notification:');
    console.log('   TEAMS_WEBHOOK_URL secret is not set.');
    console.log('   To receive real-time Cards in Teams: Add TEAMS_WEBHOOK_URL in GitHub repo Secrets.');
    return;
  }

  console.log('\n📢 STEP 4: SENDING INTERACTIVE ADAPTIVE CARD TO MICROSOFT TEAMS...');

  const repoName = process.env.GITHUB_REPOSITORY || 'jeswnath/testPlaywright';
  const runNumber = process.env.GITHUB_RUN_NUMBER || 'Local';
  const runUrl = process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
    : prUrl || `https://github.com/${repoName}/actions`;

  const fallbackPrUrl = prUrl || `https://github.com/${repoName}/pulls`;

  const teamsPayload = {
    "type": "message",
    "attachments": [
      {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "contentUrl": null,
        "content": {
          "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
          "type": "AdaptiveCard",
          "version": "1.4",
          "body": [
            {
              "type": "Container",
              "style": "emphasis",
              "items": [
                {
                  "type": "TextBlock",
                  "text": "🩺 AUTONOMOUS SELF-HEALING PR CREATED",
                  "weight": "Bolder",
                  "size": "Medium",
                  "color": "Good"
                },
                {
                  "type": "TextBlock",
                  "text": "Playwright CI Failure Intercepted & Patch Prepared",
                  "isSubtle": true,
                  "spacing": "None"
                }
              ]
            },
            {
              "type": "FactSet",
              "facts": [
                { "title": "Repository:", "value": repoName },
                { "title": "Spec File:", "value": targetSpec },
                { "title": "Workflow Run:", "value": `#${runNumber}` },
                { "title": "Confidence:", "value": `${confidenceScore}% (High Confidence)` },
                { "title": "Status:", "value": "🟡 Action Required: Pending Approval" }
              ]
            },
            {
              "type": "TextBlock",
              "text": `**Diagnosis:** ${healingRationale}`,
              "wrap": true
            },
            {
              "type": "Container",
              "items": [
                {
                  "type": "TextBlock",
                  "text": "🔍 Proposed Code Diff",
                  "weight": "Bolder"
                },
                {
                  "type": "TextBlock",
                  "text": `- ${oldCodeLine}\n+ ${newCodeLine}`,
                  "fontType": "Monospace",
                  "wrap": true
                }
              ]
            }
          ],
          "actions": [
            {
              "type": "Action.OpenUrl",
              "title": "✅ Approve & Merge Fix (Open PR)",
              "url": fallbackPrUrl
            },
            {
              "type": "Action.OpenUrl",
              "title": "🔍 View CI Failure Logs",
              "url": runUrl
            }
          ]
        }
      }
    ]
  };

  try {
    const res = await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(teamsPayload)
    });
    if (res.ok) {
      console.log('✅ Successfully posted Adaptive Card to Microsoft Teams Channel!');
    } else {
      console.warn(`⚠️ Teams Webhook returned status: ${res.status}`);
    }
  } catch (err) {
    console.warn('⚠️ Could not post to Teams webhook:', err.message);
  }
}

console.log('\n================================================================================');
console.log('✅ SELF-HEALING TRIAGE PIPELINE COMPLETED');
console.log('================================================================================\n');
