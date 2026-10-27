# Security status

This is a local development prototype, NOT a publicly hosted production service.

- Bind the API and development MySQL to loopback only.
- Demo authentication uses a shared local code; it is not a customer identity platform.
- Never use real customer/order/payment data. All bundled fixtures are synthetic.
- No arbitrary HTTP execute endpoint exists. Approved run-bound actions execute only through the worker's in-transaction lease/fencing and approval checks; detached M1 service calls remain internal test interfaces.
- .env contains local credentials and is gitignored. Do not upload it or put tokens in screenshots.
- Database tests reset only a disposable schema named shopresolve_test; never point them at a real database.
- Do not expose the Docker daemon or grant the API access to its socket. Only the separate local manager invokes Docker; its internal bearer token is not a customer token.
- The sandbox is non-root, network-none, read-only-root, resource-limited, with no business keys or Docker socket inside. Output tar is inspected rather than extracted to arbitrary paths. This is not a microVM or a guarantee against all kernel/container escapes.
- The trusted supervisor's completion marker is liveness information, not proof of computation correctness or business authorization.
- Development processes currently read a shared ignored .env. Split provider/DB/manager credentials into process-specific deployment configuration before any public deployment.
- CI test credentials are deliberately public, ephemeral service credentials, not production secrets.
- Report suspected flaws to the repository owner privately; a reporting address will be added before public release.

Remaining controls before any public deployment include authentication hardening, rate limiting, full authorization review, worker reliability tests, dependency/security scanning, and a deployment-specific threat model. Passing current tests does not establish universal safety.
