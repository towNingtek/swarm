# Issue acceptance criteria

Every issue states, in a form anyone can run, what "fixed" looks like and what "not fixed yet" looks like.

## Why

Checks that only confirm "something is on the screen" pass even when the feature is broken. In one review of 29 screenshot checks, 14 only tested that a page showed its own title. All were green; two of those pages were broken. Replacing the checks with strings that only appear when the feature works turned those two red at once, and both were real backend bugs.

The same thing happens with issues: they get closed because "it looks fixed", not because "this command now gives this output".

## Format

Put this section at the end of every issue:

```markdown
## Acceptance

### Fixed: all of these hold
| # | How to check | Expected |
|---|---|---|
| 1 | `curl -s "$BASE/api/admin/faqs" -H "Authorization: Bearer $TOKEN"` | `"success": true`, `faqs` has at least 1 entry |
| 2 | `node e2e/capture.mjs admin-faq` | exit 0 |

### Not fixed: any of these holds (with current output)
| # | How to check | Now (measured YYYY-MM-DD) |
|---|---|---|
| 1 | Same as above | `{"error": "Admin access required"}` HTTP 403 |
```

The "not fixed" column holds **output you actually got**, not a description. Whoever picks up the issue reruns it first and knows immediately whether the problem is still there, and whether they are testing the right environment.

## Rules

| Rule | Bad example | What goes wrong |
|---|---|---|
| 1. Criteria come from a real run | Expecting a file name with a dash when the real one uses an underscore | The check always fails and nobody knows why |
| 2. The success signal must not appear on the failure path | Using a word from the page title or the navigation bar as the signal | It passes on an empty page |
| 3. No tautologies | "The FAQ page shows 'FAQ'" | Tests nothing. A table header is the same: it is there with zero rows |
| 4. Others can rerun it | "Log in and click around" | Cannot be handed over or put in CI |
| 5. It tells "all done" from "partly done" | "The attribute appears at least once" | Two of three branches fixed still passes, and the missing one may be the only one with data |
| 6. It tells "mechanism fixed" from "fixed once by hand" | "The deployed commit equals the main branch" | One manual sync turns it green; it drifts again next week |

In one sentence: **the check must be part of what the feature produces, and impossible to see when it fails.**

## Rule 5: all done versus partly done

This kind of failure is hard to spot: everything is green, exit codes are 0, no errors. Examples:

| Check | Why it passed | Reality |
|---|---|---|
| "Migrate prints *No migrations to apply*" | A silently skipped app prints the same | The app's migrations folder lacked `__init__.py` and was never processed |
| "Marker attribute appears at least once" | Two of three code branches were fixed | The missing branch was the only one with data |
| "No broken image on the page" | The fallback was never triggered | Both pages had their own banner; the fallback branch never ran |
| "The bundle compiles" | An undefined variable is a runtime error | The component threw on first render |

The question each time:

> Can this check tell "done" from "not done at all"?

If not, it only confirms that someone touched the file.

How to write checks that catch it:

- **Count, do not test for existence.** Replace "at least 1" with the exact number, and add a cross-check:

  ```bash
  # one marker per branch
  n=$(grep -c "L.divIcon" "$F")
  [ "$n" = "3" ] || echo "a branch was missed"
  ```

- **Check the result, not "nothing to do".** Instead of "No migrations to apply", query the migrations table for the expected record.
- **"Not triggered" is not "passed".** When the environment cannot reach a branch, record the check as *not verified* with the reason. Do not tick it because nothing broke.
- **The reverse check is often the only red one.** Five positive checks can be green while "markers with a value equals records from the API" is 0 versus 9, and that one check locates the bug.

## Front-end checks are easy to fake

| Layer | Fake check | Why it fools you |
|---|---|---|
| Source | `grep` finds the fix | Proves someone wrote the line, not that a user sees it |
| Build | The file is on disk | On disk is not in the deployed bundle. Restarting a container does not rebuild the front end |
| Runtime | Click around in your own browser | Your environment can hide the bug (below) |

Grepping the source for the fix is the code version of a tautology. The check must be on **what the user sees**: rendered DOM text, not element properties, not source, not the existence of a commit.

Example: a form's `validationMessage` property has a value whether or not native validation was disabled, so it always reads "Please fill out this field." What tells the cases apart is whether the **application's own message** appears in the DOM. If native validation still blocks the form, the submit handler never runs and no application message appears (the browser's bubble is not in `innerText`).

### Run in an environment that exposes the bug

Native validation messages follow the **browser locale**, not the site's language setting. Test in a browser whose locale matches the site's language and fixed and unfixed look the same. Run with a different locale, and make the two signals distinguishable:

| | Text | Source |
|---|---|---|
| Fixed | `Please enter a contact person` | Application i18n |
| Not fixed | `Please fill out this field.` | Browser native |

Both are English, but they differ. **That difference is the whole value of the check.** If both cases look the same, it is not a check.

### Minimal front-end check

```js
const ctx = await browser.newContext({ locale: 'en-US' });   // environment that exposes the bug
const page = await ctx.newPage();
await page.goto(url);
await page.click('button[type=submit]');
const body = await page.innerText('body');                   // what the user sees
assert(body.includes('Please enter a contact person'));      // success signal
assert(!body.includes('Please fill out this field'));        // failure signal must be absent
```

All three are needed: a controlled environment, the rendered DOM, and two distinguishable signals.

If the fix involves build output (bundle, injected environment variables), add one more check: the behaviour changed on the **live site**, not only in local files.

## Rule 6: correct state is not a correct mechanism

Example: a scheduler container's working copy had not updated for weeks and was dozens of commits behind. Agents kept running old skills. No error, no alert, exit 0.

The first idea for a check:

```bash
docker exec scheduler git -C /srv/app rev-parse HEAD   # should equal origin/main
```

This goes green but does not prove a fix. Anyone running `git pull` once turns it green, and it drifts again the next time the main branch moves, which was the original bug. Every check of current state has this blind spot: it measures the result, not what produces it.

Make the state break again and see whether it repairs itself:

| Instead of | Use |
|---|---|
| Deployed commit equals the main branch | **Push a new commit, wait one sync period, check again**: still equal |
| The cache exists | Delete it, run again, it rebuilds itself |
| The data was backfilled | Add a new tenant, do nothing by hand, the data appears |
| The alert is configured | Cause one failure on purpose and confirm the alert **arrives** |

The same question again:

> Does this check prove "someone did it once", or "it will happen again by itself"?

If the former, it starts failing the moment you close the issue.

### The automation can break too

Leave a check for the repair mechanism itself. If the sync is a timer, a stopped timer is just as silent. Besides "syncs automatically", check "complains when sync fails": make it fail once and confirm the alert reaches the operators.

**Automation without an alert just moves the silent failure one layer down.**

## What criteria look like per issue type

| Type | Shape of the criteria |
|---|---|
| Bug | The **same command** before and after, with both outputs |
| Feature / front end | Observable output of the new behaviour: API response, DOM selector, e2e script exit code |
| e2e / infrastructure | Script or CI exit code, with the script path |
| ADR / discussion | The decision is written down and linked. Success: the document exists and is referenced. Failure: it is still only spoken |
| Evaluation / follow-up | An artefact exists: a conclusion document, a decision issue, or an explicit "won't do" with the reason |
| Milestone | Every child issue is closed and each one's criteria pass |

Non-code issues **also need a failure column**. "This document does not exist yet" is fine. What matters is having something to compare against when closing.

## When to write them

- **When opening the issue.** Not being able to write criteria usually means the problem is not described clearly yet; that is a signal in itself.
- If they cannot be written yet (requirements still under discussion), label the issue `needs-criteria`. It does not go into implementation.
- **When closing**, post the actual output of the criteria in a comment. No output posted means not closed.
