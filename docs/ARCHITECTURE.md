# Architecture

The Python package provides local source acquisition and storage, schema migrations, structured reading proposals, source occurrences, evidence and identity curation, research questions and controlled presentation exports. These are generic mechanisms; no canonical research state accompanies them.

The interface is built with Vue and TypeScript. The presentation layer consumes a controlled payload or local API. The blind-review entry is built separately and must not load the main presentation export. It uses a same-origin API for session, image, freeze, reveal and submission operations.

Code for encryption, private API sessions and archival services is reusable software, not a key or account configuration. Optional dependencies and service availability must be verified separately.

The private research pilot module and pilot CLI commands are absent from this edition. The private spreadsheet importer no longer attributes any source identifier to a named informant; unrecognized source values stay imported_unverified without an inferred informant.

No historical fixture, family graph, archive reproduction, cached export, map tile package, local service configuration or correspondence is included. An empty presentation payload is author-created scaffolding and is not evidence about any person.

PWA registration is disabled in this public edition; no service worker, installation manifest or map tile data is distributed. The empty scaffold selects the schematic map fallback.
