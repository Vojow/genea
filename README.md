# Genea

Genea is an experimental local workspace for genealogy research with AI assistance. It separates source material, proposed readings, source occurrences, identity decisions and research conclusions.

This is a code-only edition. It contains no family database, archival originals, historical family case studies, private export, correspondence or original repository history. The included example under examples/synthetic is empty synthetic scaffolding, not an anonymized copy of real research. Runtime presentation exports belong in ignored frontend/data, separate from the tracked example.

## Status

Genea is experimental software under active development. Project code and documentation use the [MIT license](LICENSE); third-party dependencies retain their own licenses and notices. See [third-party notices](THIRD_PARTY_NOTICES.md).

The code includes a Python CLI, source/review and curation models, a Vue interface and an isolated blind-review interface. External archive acquisition and optional AI-provider workflows require separate setup. A static frontend does not supply the reading-verification backend API. The public subset has selected synthetic tests; it does not include the private workspace's complete regression suite. PWA installation, offline support and bundled map tiles are omitted from this candidate.

## Local CLI quick start

Use Python 3.10 or newer. From this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
./genea --root .local-workspace init
./genea --root .local-workspace migration-status
./genea --help
```

Always give an explicit workspace root when experimenting. The commands above create a new local database in an ignored directory. Do not point tests or demonstrations at your actual research workspace. The optional private-storage extra adds encryption/keyring dependencies; no encryption key or account session is distributed.

## Interface development

Use a supported Node version for the versions pinned in the lockfile, then install with `npm ci`. Use `npm run dev:ui` to view the empty synthetic scaffold. The demo tooling reads examples/synthetic/empty-presentation.json; it does not automatically select an existing runtime export. Use `npm run build:ui:demo` followed by `npm run preview:ui` for a static build containing only that empty scaffold. `npm run build:ui` is the data-free build for a separately supplied payload/API. See the other package scripts for isolated blind-review builds and unit tests. The normal public build does not embed a local research export by default. Live reading-verification actions require the corresponding same-origin API and a prepared session; the isolated UI alone is not an end-to-end research workflow.

The first publication must record the versions actually verified by the maintainer. Do not assume the entire optional-provider matrix or encrypted-storage stack has been tested on every platform.

## Research principles

- Preserve sources and their provenance.
- Keep literal readings separate from normalization and identity decisions.
- Record uncertainty, alternatives and review roles.
- Treat automated agreement as a review input, not human confirmation.
- Keep search coverage separate from a negative historical conclusion.

Read [the documentation](docs/README.md), [privacy boundary](docs/PRIVACY.md), [contribution guide](CONTRIBUTING.md) and [security reporting policy](SECURITY.md).

## Before sharing source changes

Stage only the reviewed source files, then run `python scripts/check_public_tree.py`. The offline guard reads the staged Git blobs, rejects runtime/private paths and obvious credentials, and fails on an empty or inherited parent index. It supplements manual review; it is not a guarantee that arbitrary content is free of personal information.

## Security

Security reports use [GitHub private vulnerability reporting](https://github.com/Vojow/genea/security/advisories/new). Do not include sensitive details in public issues. See [the security policy](SECURITY.md).
