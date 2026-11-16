# Deployment reconciliation and application data ownership

ctl manages containers, service dependencies, configuration, health, logs and
retained resource identity. Backup selection, download, database restore, data
validation and the decision to enable business writes belong to project code.
There is no `ctl restore` or database backup input in the CLI/task protocol.

## Interrupted deployments

An operation journal precedes each container or job side effect. Inspect status
and reconcile the original deployment transaction with:

```sh
ctl status example --env prod
ctl recover example --env prod --operation ORIGINAL_TRANSACTION_ID
```

This is deployment reconciliation, not data recovery. It verifies immutable
snapshots and owned container/resource identities. It checks the original
one-shot container rather than launching a replacement attempt. Failed/missing/
unknown job outcomes remain pending for project-specific investigation.
Completed bootstrap jobs are bound to the same volume identities and do not run
again on ordinary releases. Jobs marked incompatible prevent automatic app
rollback after side effects. ctl does not interpret their application data.

Missing managed storage blocks an ordinary deployment rather than silently
creating an empty replacement. Project operators determine how to recover data.
Never erase state or a pending transaction to bypass resource identity checks.

## Persistent service maintenance

```sh
ctl maintain example --env prod --version VERSION --service postgres --plan
ctl maintain example --env prod --version VERSION --service postgres --plan-id PLAN_ID
```

The plan binds resource/configuration changes and current observed identities.
Maintenance quiesces dependent applications and waits for changed service health.
This initial implementation permits same-image/same-volume configuration changes;
image or mount replacement needs a separately prepared service replacement.
There is no backup checksum attestation. Projects own backup and compatibility
requirements independently of ctl's deployment plan.

## Single-service to multi-service layout upgrade

A project may initialize from backup in its bootstrap job. Ordinary upgrade stops the
old application, then invoke the ordinary upgrade entry point with a multi-service
release. The existing app/environment/public binding are preserved:

```sh
ctl upgrade example --env prod --version VERSION --plan
ctl upgrade example --env prod --version VERSION
```

ctl verifies the legacy snapshot/container, journals the transition, and stops the exact old API container as part of upgrade. It retains the old
container and mounts, records a durable layout operation, and allocates new owned
resources in a separate namespace. It executes only the jobs/dependencies declared
by the project. Success requires completed jobs and healthy declared services.
There is no adoption of old hook sidecars, volume deletion, or automatic rollback
to the previous layout. Subsequent ordinary upgrades retain the new resources.

External databases and hook-created sidecars are outside the old deployment
inventory. The project script must fence their writers and own its data-cutover
checks. A successful container exit is only the project's execution result;
ctl does not prove backup freshness, logical data consistency or recoverability.
