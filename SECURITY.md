# Security status

This is a local development prototype, NOT a publicly hosted production service.

- Bind the API and development MySQL to loopback only.
- Demo authentication uses a shared local code; it is not a customer identity platform.
- Never use real customer/order/payment data. All bundled fixtures are synthetic.
- No HTTP endpoint executes an approved action in M1. The worker, fencing and approval-event integration are still pending.
- .env contains local credentials and is gitignored. Do not upload it or put tokens in screenshots.
- Database tests reset only a disposable schema named shopresolve_test; never point them at a real database.
- Do not expose the Docker daemon or grant the API access to its socket. Code sandbox functionality is not implemented yet.
- CI test credentials are deliberately public, ephemeral service credentials, not production secrets.
- Report suspected flaws to the repository owner privately; a reporting address will be added before public release.

Remaining controls before any public deployment include authentication hardening, rate limiting, full authorization review, worker reliability tests, dependency/security scanning, and a deployment-specific threat model. Passing current tests does not establish universal safety.
