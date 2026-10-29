# Privacy boundary

The release candidate was assembled from explicitly selected code paths, not by copying a private checkout and deleting its data afterward. Its Git repository must have no inherited private commits, remote references or LFS objects.

Research data, original sources, SQLite and SQLCipher files, backups, browser profiles, local storage configuration, credentials, recovery material, generated research reports and real-family fixtures are excluded. Documentation was rewritten for this candidate rather than copied from the research wiki.

The application can create local research data when a user runs it. Runtime data belongs outside source control. Give CLI tests and demonstrations an explicit temporary or ignored workspace root. A configured private root is not a test database.

The blind-review UI stores literal draft readings in browser sessionStorage under the run identifier and sends them when submitted to its configured same-origin API. Do not represent it as a browser that retains no readings. The complete private-data workflow requires its own session, consent and storage review.

Public builds must not automatically pick up a local research export. Generated bundles are not part of the code candidate and must be reviewed separately before any deployment.

Pattern scans help detect known names, case markers, source IDs, account links and credential forms. They do not prove the absence of every possible personal detail. The maintainer must inspect the exact public file list, generated assets and final review findings before publication.
