# P1-08 · Verification workbench: estimator usability session

The workbench is where estimators spend their day, so it closes only after two or three estimators have used it on a real-looking floor plate (P1-08 "Done when"; NFR-12: "an estimator can verify a typical floor plate without leaving the workbench", onboarding in a day or less).

This guide lets anyone run the session. Findings and the changes made go into the P1-08 build log entry.

## Setup (30 minutes before)

1. `make up`. Sign in as `senior.estimator@firebid.test` (password `firebid-dev`).
2. Create a bid, upload the three synthetic sheets (general arrangement, enlarged plan, riser schematic), confirm the legend on the **Symbols** page, and wait for takeoff. `frontend/e2e/workbench.spec.ts` does the same through the API, if that is quicker.
3. Optional, closer to real work: add one of the company's own recent tenders (a floor plate of 300–800 heads), with a person's count to hand for comparison.
4. Use two monitors if the participant normally does. **Pop out the drawing** is at the top of the workbench.
5. Start a screen recording. Note the time at each task's start and end.

## Participants

Two or three estimators, at least one senior. Record for each: years of estimating, and the takeoff tools they use today.

## Script (about 45 minutes each)

Say only this before starting: "This is a screen for checking what the platform has counted and measured from the drawings. Please think aloud. We are testing the screen, not you."

| # | Task (read it out) | Watch for |
|---|---|---|
| 1 | "Find the riskiest item and check it against the drawing." | Do they understand the queue order? Do they find the evidence without help (NFR-10: two clicks or fewer)? |
| 2 | "Accept everything you are happy with on the first page." | Do they find **Select page** and bulk accept, or accept one by one? |
| 3 | "The upright heads should be white. Correct that." | Edit form, reason codes: are the reasons the ones they would give? |
| 4 | "One tee is not ours: it is by others. Take it out." | Reject with a reason. |
| 5 | "There are two flow switches the platform missed. Add them." | Finding **Add what was missed**; placing marks; the scale rule. |
| 6 | "The enlarged plan repeats the general arrangement. Sort that out." | Side-by-side duplicates: is the decision clear? |
| 7 | "Some symbols have not been named. Deal with them." | Naming grid bubbles as "not an object": is that obvious? |
| 8 | "Is the takeoff ready for approval? If so, approve it." (senior only) | Coverage and blockers: do they understand what stops G1? |
| 9 | "Undo the last thing you did." | Ctrl+Z or the Undo button. |
| 10 | Free use: "Check the rest as you normally would." | Keyboard shortcuts, layers, lasso, the pop-out. |

After the tasks, ask:
- What would stop you using this for your next tender?
- What did you expect to find that was not there?
- How long would it take you to verify this floor plate your usual way?
- On a scale of 1 to 5, how confident are you in the quantities you approved here?

## Findings (fill in during the session)

| # | Participant | Task | What happened | Severity (blocker / major / minor) | Change proposed |
|---|---|---|---|---|---|
| 1 | | | | | |

**Times:**

| Participant | Tasks 1–9 (min) | Floor plate verified (min) | Their usual way (min) |
|---|---|---|---|
| | | | |

## After the session

1. Group the findings. Fix the blockers and majors before P1-08 closes, or agree with the product owner to defer them.
2. Add to the P1-08 build log entry: the participants (roles only, no names), the findings, the changes made, and what was deferred.
