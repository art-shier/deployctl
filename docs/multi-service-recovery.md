# Recovering an interrupted multi-service deployment

The schema-3 runtime stores a pending transaction before changing containers or starting a one-shot job. Ordinary upgrades remain blocked while that transaction is pending. Use the original operation identity to reconcile its recorded outcome:

```sh
ctl status example --env prod
ctl recover example --env prod --operation ORIGINAL_TRANSACTION_ID
```

The operation ID is `transaction.journal.id` in the status output. Recovery runs under the same application lock as deployment and requires matching immutable snapshots, container ownership and retained volume identities.

For a recorded restore that is still running, recovery observes its original container using the persisted deadline. A completed container is acknowledged without running the import again. A timed-out writer is stopped and its termination verified. A disappeared container or a failed/unknown job remains pending; recovery never creates a replacement job or assumes partially restored data is valid.

After the recorded jobs are verified, recovery continues the application deployment from the frozen target snapshot. An incompatible data-change barrier remains in effect: failure of the application does not authorize automatic application rollback across that barrier.

The restore program must validate the backup checksum, empty target and restored data before exiting successfully. A process exit code alone does not implement application-specific data validation. Secret input files remain private on the host and read-only in the container; the declared container permissions must permit reading them. The PostgreSQL restore fixture explicitly uses the `database-init` permission profile.

This recovery command does not adopt unregistered containers, restore missing volumes, retry failed imports, or perform database version maintenance. Those cases require an isolated candidate and explicit verified recovery workflow; they must not be bypassed by deleting state, volumes or the pending transaction. Stop/restart interruption journals are not deployment operation IDs.

The Linux fixture `scripts/multiservice_docker_integration.py` exercises a real `pg_dump`, source checksum, empty target, SQL data verification, process loss before success acknowledgement, and subsequent upgrade after removing backup credentials.

## Persistent maintenance and isolated disaster restoration

`ctl maintain APP --env ENV --release PACKAGE --service postgres --backup-sha256 SHA --plan`
previews same-image, same-volume database configuration maintenance. Apply the
same arguments with `--plan-id ID` returned by the preview, without `--plan`.
Applications are stopped before the persistent change and restarted after its
health check. Keep the verified backup available: a SHA records operator input,
it does not itself prove an external backup can be restored.

For an established multi-service deployment whose data volume is lost, use a
release declaring the restoration jobs and required backup configuration:

```
ctl restore APP --env ENV --release PACKAGE --job-source restore=SHA --plan
ctl restore APP --env ENV --release PACKAGE --job-source restore=SHA --plan-id ID
```

Restoration allocates an isolated resource generation and preserves old volumes
and containers. Every application must depend on the restoration jobs. Those
jobs must validate the backup contents and restored data before succeeding.
The source is stopped before the candidate starts on the public binding. A
successful candidate becomes the current deployment; normal upgrades continue
using its volumes. Cross-generation rollback is blocked because the old data
may be stale. `ctl recover APP --env ENV --operation ID` reconciles an interrupted
restoration; failed or ambiguous import outcomes still require investigation.
These commands do not adopt legacy hook-managed containers or silently reuse an
existing volume.

## Explicit cutover from a legacy single-service installation

The same `restore --plan` / `restore --plan-id` flow can migrate an established
snapshot-based single-service installation while preserving its application,
environment and public binding. First quiesce all application-specific external
writers, take and verify the final backup, then `ctl stop APP --env ENV`. A running,
missing or changed legacy app container is rejected before planning. Ordinary
`upgrade` continues to reject cross-schema migration.

The reviewed plan binds the old snapshot, stopped container and mounted volume
identities. The source state is retained privately under the restoration audit;
a durable schema-3 parent operation precedes candidate creation. Recovery checks
that the old source remains stopped and unchanged. New resources use a separate
generation and never adopt the old hook-managed database or volumes. The old
container and data remain available for investigation. Successful promotion has
no previous deployment, preventing rollback onto potentially stale legacy data.
The next ordinary upgrade uses the same application/environment and new volumes.

External databases and hook-created sidecars are outside ctl's legacy inventory.
Their write freeze and backup correctness must be established by the operator
and application restore program; stopping the HTTP container alone is not proof
that every external writer has stopped.
