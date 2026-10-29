# Development

Install dependencies in this checkout's own environments. Do not copy another workspace's virtual environment or node_modules into the release.

Use temporary directories for automated database tests. Never resolve the canonical private research root from a test. Use synthetic source names, neutral example domains and newly authored values rather than pseudonymizing a real family graph.

Backend checks use Python unittest with this checkout's src directory on PYTHONPATH. Interface tests use the package scripts. The maintainer should run targeted tests while changing modules, then one final release check against the exact candidate.

A local candidate passing selected tests is not evidence that all optional providers, operating systems, migrations, live AI runs or private-storage integrations work. Record the scope actually verified.

Keep source, reading, occurrence, identity, claim and review status separate. Review automation must not assign human-review status. Preserve historical alternatives rather than overwriting the decision trail.
