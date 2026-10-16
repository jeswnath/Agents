# Playwright Locator Priority & Best Practices Reference

## Core Philosophy
Playwright locators must mimic how real users interact with the web application. When a test breaks, the self-healing algorithm always upgrades legacy brittle selectors (`CSS paths`, `XPath`, or generic `classNames`) into accessible, resilient locators.

---

## Priority Order & Heuristics

### 1. `page.getByRole(role, { name })`
- **When to use:** Buttons, links, headings, checkboxes, radio buttons, dialogs.
- **Why:** Immune to class changes, framework migrations (React $\leftrightarrow$ Angular), and structural div wrappers.
- **Example:**
  ```typescript
  // Old brittle:
  await page.locator('a.btn-signin').click();
  // Healed resilient:
  await page.getByRole('link', { name: 'Sign In' }).click();
  ```

### 2. `page.getByText(text, { exact: false })`
- **When to use:** Non-interactive or simple text containers, menu items, table cell values.
- **Example:**
  ```typescript
  // Old brittle:
  await page.locator('div.cat-name:nth-child(1)').click();
  // Healed resilient:
  await page.getByText('Angelfish').click();
  ```

### 3. `page.getByLabel(label)`
- **When to use:** Form fields with `<label>` tags or `aria-label`.
- **Example:**
  ```typescript
  // Old brittle:
  await page.locator('#txt-user-id').fill('perf_1');
  // Healed resilient:
  await page.getByLabel('Username').fill('perf_1');
  ```

### 4. `page.getByPlaceholder(text)`
- **When to use:** Input search fields with clear placeholder text.
- **Example:**
  ```typescript
  await page.getByPlaceholder('Search products').fill('Fish');
  ```

### 5. Semantic Attribute Locators (`name`, `data-testid`)
- **When to use:** When accessibility roles are not defined on old legacy enterprise apps (like JPetStore, SAP, or Oracle portals).
- **Format:**
  ```typescript
  // Stable form input:
  await page.locator('input[name="username"]').fill('perf_1');
  await page.locator('input[name="signon"]').click();
  ```

---

## Anti-Patterns (BANNED from Self-Healing Replacements)
❌ **Never heal to an absolute XPath:** `//div[2]/table/tbody/tr[3]/td[2]/a`  
❌ **Never heal to a styling class:** `button.bg-blue-500.rounded-md.px-4`  
❌ **Never heal to auto-generated hashes:** `button#btn-73892-xqa`  

