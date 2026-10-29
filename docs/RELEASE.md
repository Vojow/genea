# Release checklist

This candidate is not authorized for external publication until the maintainer reviews the exact contents and chooses the publication destination.

- MIT was selected by the maintainer; preserve LICENSE and check dependency and asset notices.
- Create the approved public repository, enable GitHub private vulnerability reporting and verify it before uploading code. SECURITY.md contains the target reporting link.
- Verify installation from an independent copy using its own environments and safe workspace.
- Run the selected backend/interface tests and main/blind builds, then inspect generated artifacts separately.
- Confirm the candidate includes no private data, credentials, local configuration, research originals, personal examples, inherited Git history or private LFS objects.
- Review omitted tests and document the resulting coverage limits.
- Review runtime defaults, optional-provider setup and platform support statements.
- Keep the empty/synthetic UI example clearly labelled.
- Select the repository owner/name and review the exact publication preview before creating any remote repository.

The source package and a future deployed build are separate review targets. A deployment must not include a real local presentation export or an unreviewed database merely because the code package was reviewed.
