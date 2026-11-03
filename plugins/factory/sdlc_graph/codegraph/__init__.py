"""The DERIVED code graph: extractors rebuild it from source per commit SHA; nothing writes it
through an API. ``extract_*`` modules are pure (files in, ExtractResult out, stdlib only);
``snapshot`` persists and versions results; ``queries`` reads them.

Extractor protocol (every ``extract_*`` module): ``extract(repo_root) -> ExtractResult`` over the
files on disk and ``link(merged) -> list[Edge]`` for cross-extractor edges computed after all
results are merged (``[]`` when the extractor has none). ``EXTRACTORS`` is the order
``snapshot.extract_all`` runs them in.
"""

EXTRACTORS: tuple[str, ...] = (
    "sdlc_graph.codegraph.extract_python",
    "sdlc_graph.codegraph.extract_frontend",
    "sdlc_graph.codegraph.extract_typescript",
    "sdlc_graph.codegraph.extract_deps",
    "sdlc_graph.codegraph.extract_owners",
)
