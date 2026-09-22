# Build Prompts

There is one prompt per build step. The step order, dependencies and sizes are in `../IMPLEMENTATION_PLAN.md` §4, which is the only catalogue of steps.

## Using a prompt

1. Confirm the previous step is merged and has an entry in `../BUILD_LOG.md`.
2. Check the prompt's **Needs** line. If a business-track input is missing, the prompt states the fallback.
3. Open the repository in its Dev Container (from P0-02 onward), start a fresh Claude Code session at the repository root, and say: `Execute docs/plan/prompts/<file>.md`. You can also paste the file's contents.
4. The agent replies with a plan mapped to the prompt's **Done when** list. Approve it or correct it.
5. When the agent finishes, run `/code-review`, check `make req-coverage`, and confirm the build log entry.

## What every prompt contains

| Section | Purpose |
|---|---|
| Builds on / Needs | Earlier steps it depends on; business-track inputs and fallbacks |
| Read first | `project-context.md` (binding conventions) plus the exact requirement sections |
| Goal | The outcome the step delivers, in one paragraph |
| Scope | The requirement IDs the step owns |
| Build | Ordered, specific work items |
| Done when | Checkable completion criteria, each tagged with requirement IDs where they apply |

## Editing prompts

- Put conventions that apply to every step in `../project-context.md`, not in individual prompts.
- When a step's scope changes, update its row in the plan's catalogue and its **Scope** section together, then run `python docs/plan/check_coverage.py` from the repository root. It fails if any requirement ID is unassigned, or if a link is broken.
- At each phase gate, revise the next phase's prompts with what the build log and exit report show.
