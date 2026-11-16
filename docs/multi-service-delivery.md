# Multi-service image delivery

The existing reusable `build-release.yml` workflow accepts deployment schema 2. Install, publish and upgrade command names remain unchanged. The generated release uses schema 3 and contains an immutable image reference for every declared service and job.

Each component declares exactly one source:

- `build`: a Dockerfile and context within the checked-out project, with optional component-specific `build.args`.
- `image`: an existing SHA256-pinned image. Set `mirror: project` when its repository differs from the project's destination repository.

All resulting images reside in the same project repository, preserving the platform's existing repository-scoped registry credentials. Components use distinct deterministic delivery tags; the release stores digests, not tags. Identical sources are built or mirrored once and reused in the keyed inventory.

Schema-1 deployments keep the existing Docker build action and `image` workflow output. Schema-2 deployments expose the `images` JSON output. Their build arguments belong in each component's `build.args`; a nonempty global `build-args` override is rejected because its intended component would be ambiguous. The workflow `platforms` input must match the declared deployment platform.

External mirrors preserve the source manifest digest, including complete indexes. Preflight checks validate raw manifest SHA256, descriptor sizes, permitted manifest types, requested architecture and a budget of 4 GiB of unique declared blobs. Manifest trees are limited to 32 manifests and depth 2, with at most 256 layers per image. External blob URLs are rejected. The copier verifies blobs and preserves digests; the destination is inspected again before packaging.

Builds use Docker Buildx. Registry copies use Skopeo with TLS verification and the job's existing Docker credential file. Source and destination credentials are resolved for their own registries; passwords are not command arguments. The reusable workflow logs into the destination registry. External sources must be public or accessible with credentials already present for that registry. Only the disposable test fixture explicitly permits HTTP for loopback registries.

The package and GitHub release are generated only after every image succeeds. A failed operation may leave uploaded images in the project repository, but does not publish a partial release. No runtime secrets or install parameters are used as build arguments.

Linux CI exercises a real application build, two registries with different credentials, literal build argument transport, digest-preserving image copy, exported blob hashes and extraction of the complete schema-3 package. Separate runtime fixtures verify PostgreSQL retention and interrupted project-job reconciliation.

Reusable workflows can provide ordinary dynamic values (for example the release
version) with `service-build-args`, a JSON object keyed by declared build service
or job name, whose values are argument maps. These override that component's
`build.args`; unknown components, image-only components, duplicate JSON keys and
invalid arguments fail before any build. Values are passed literally, never as
shell fragments. Do not use this input for credentials. Legacy `build-args`
continues to target the single-image build unchanged.
