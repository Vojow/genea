"""Compatibility facade for the modular presentation layer.

The public API remains here because the CLI and downstream integrations import
``genea.frontend``. Projectors and the localhost server live in
``genea.presentation`` and share explicit dependencies.
"""

from .presentation.common import (
    birth_detail as _birth_detail,
    display_rationale as _display_rationale,
    event_group as _event_group,
    json_object as _json_object,
    mergeable_event_dates as _mergeable_event_dates,
    unique_display_values as _unique_display_values,
)
from .presentation.events import participant_presence as _participant_presence
from .presentation.graph_scopes import (
    build_graph_scope_facade as _build_graph_scope_facade,
    load_graph_scope_config as _presentation_graph_scope_config,
)
from .presentation.map import (
    _event_year,
    _place_key,
    build_map_facade as _build_map_facade,
    load_map_config as _presentation_map,
)
from .presentation.media import (
    LOCAL_ARTIFACT_PREFIX,
    PREVIEWABLE_MEDIA_TYPES,
    artifact_media_type as _artifact_media_type,
    local_originals as _local_originals,
)
from .presentation.payload import (
    FRONTEND_SCHEMA_VERSION,
    build_frontend_payload,
    open_readonly_connection as _connection,
)
from .presentation.privacy import RESTRICTED_CLASSES, PrivacyContext
from .presentation.server import (
    FrontendRequestHandler as _FrontendRequestHandler,
    build_frontend_assets,
    export_frontend,
    serve_reading_verification,
    serve_frontend,
    served_originals as _served_originals,
)
from .presentation.tree import (
    enrich_tree_occurrences as _enrich_presentation_tree_occurrences,
    load_tree as _presentation_tree,
)


def _privacy_map(connection):  # type: ignore[no-untyped-def]
    """Legacy helper retained for callers during the module transition."""

    return PrivacyContext.load(connection).labels


__all__ = [
    "FRONTEND_SCHEMA_VERSION",
    "build_frontend_payload",
    "build_frontend_assets",
    "export_frontend",
    "serve_frontend",
    "serve_reading_verification",
]
