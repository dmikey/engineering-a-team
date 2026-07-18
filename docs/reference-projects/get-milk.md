# Get Milk Reference Project

Get Milk is the reference product for exercising this autonomous engineering
team. It is intentionally small, concrete, and easy to reason about so the
team can iterate quickly on planning, feature definition, QA review, and test
automation.

## Product Goal

Help a busy person remember to buy recurring grocery staples, starting with
milk, with the minimum possible friction.

## Target User

- A single user managing a household shopping list
- Often remembers groceries late and needs a fast capture flow
- Repeats the same purchases every week

## Core Release Scope

### Release 1

- Add a grocery item with name, quantity, and optional note
- Mark an item as purchased
- View active and completed items separately
- Re-add a completed item in one action
- Persist data between sessions

### Release 2

- Support due dates for planned purchases
- Highlight overdue items
- Provide a shortlist of recurring staples
- Filter to "Need this week"

## Acceptance Criteria

### Add Item

- User can create an item with a required name
- User can optionally set quantity and note
- New item appears immediately in the active list

### Complete Item

- User can mark an active item as purchased
- Completed item moves out of the active list
- User can still review completed items later

### Re-add Item

- User can re-add a completed item with one action
- Re-added item returns to the active list
- Re-added item keeps the original name and quantity by default

## Seed Backlog

1. Create item entry form with validation
2. Build active and completed list views
3. Persist list data locally
4. Add overdue state for dated items
5. Add one-click re-add for recurring staples
6. Add Playwright coverage for the main shopping flow

## How To Use This Project In This Repository

- Use the backlog above to seed GitHub issues in the reference app repository
- Point Playwright configuration at the running Get Milk app
- Let Morgan prioritise the backlog daily
- Let Alex suggest product refinements after each push or scheduled run
- Let Quinn review PRs and open tracking issues for risks

## Definition Of Success

The reference project is successful when the agent team can repeatedly do the
following without manual orchestration:

- generate and refine backlog items
- review pull requests with actionable QA feedback
- suggest product improvements grounded in shipped behavior
- run on a cadence fast enough to support day-to-day development