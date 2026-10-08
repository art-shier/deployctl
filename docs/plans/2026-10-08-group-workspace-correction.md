# Group workspace correction

User feedback: groups were not navigable, their members were invisible, group authorization was buried in global token settings, and the sparse card layout was unsuitable. User explicitly removed project-level credentials and requested a visual redesign.

Accepted correction: group-first home → group projects / group authorization / group settings. Register projects in context, join existing projects or move back to default. New credentials authorize groups only; existing legacy project tokens retain their original scope for running CI/deployments and remain revocable globally. Do not silently expand or revoke old credentials. No production host upgrade is performed here.

- [x] Add regression coverage and reproduce missing navigation and accepted project-token issuance before fixes.
- [x] Atomic membership endpoint: expected old group, row lock, metadata preservation, owner restriction and audit.
- [x] Group detail, in-context group credential preview, remove project credential tab/form/API creation.
- [x] Sidebar, compact lists, coherent neutral/blue controls and responsive project/credential rows.
- [x] Real PostgreSQL Go tests/vet, Web build/unit tests, 3 actual API/Registry browser workflows. Checked desktop screenshots; mobile token screenshot waits for loaded data.
- [x] Independent review found no P1/P2 blockers; CLI1.8.1 / UI0.2.1 metadata, skill assets and upgrade instructions prepared.
- [ ] CI, merge and publication of v1.8.1.

Browser verifies membership expansion/removal, environment restrictions, revocation401, deep-link reload and return context. Existing images/upload/delete, secrets/conflicts, global group credential and stale-form tests remain covered. A revocation assertion initially ran before the DELETE completed; it now waits for the visible revoked state before checking denial.
