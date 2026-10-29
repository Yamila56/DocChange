# ShopResolve implementation instructions

- Active application is DocChange (versioned document workspace); see docs/DOCCHANGE.md and docs/design/16-两项目定案与实施路线-2026-10-29.md. Commerce remains preserved legacy. Report document-domain evidence separately from old commerce results.

- Read PROGRESS.md first. Read docs/design 10, 11, 13, 14 completely before changing architecture.
- Single Agent, custom Runtime/Harness, FastAPI, MySQL, Redis. No unapproved framework switch.
- Work ONLY inside this repository. The parent folder contains private application documents; never add or upload it.
- Preserve .env, databases, other Docker containers/volumes and user files. No destructive reset outside the validated shopresolve_test database.
- Use project .venv. Do not alter global Python or existing Conda environments.
- All orders/policies/payments are synthetic. No real payment or paid model calls without explicit configuration/authority.
- Public HTTP execution endpoints are forbidden until run/fencing/approval integration is implemented and tested.
- Record tests actually run, limitations, and next steps in PROGRESS.md at each milestone. Never treat planned fault tests as passed.
- GitHub must remain private unless the user explicitly authorizes public visibility.
