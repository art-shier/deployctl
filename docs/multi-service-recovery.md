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
