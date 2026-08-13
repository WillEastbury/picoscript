from picoscript_definition import DefinitionRegistry, INDEX_KINDS


def test_definition_ids_and_persistence_are_deterministic():
    backend = {}
    first = DefinitionRegistry(backend)
    definition = first.add("users", "HASH", 1)
    second = DefinitionRegistry(backend)
    assert definition.index_id == second.resolve_key("users", "HASH", 1)
    assert second.resolve_pack(definition.index_id) == "users"
    assert second.rebuild(definition.index_id, 4)["generation"] == 4
    assert second.state(definition.index_id)["generation"] == 4


def test_definition_kind_and_missing_statuses():
    registry = DefinitionRegistry()
    assert INDEX_KINDS == {
        "HASH", "ORDERED", "FULLTEXT", "POSITIONAL",
        "GRAPH_NODE", "GRAPH_EDGE", "GRAPH_WEIGHT",
    }
    assert registry.remove("missing") is False
    assert registry.status == 1
