"""Validated graph writes (BS-26). No database."""

import pytest

from app.kg.changes import InvalidChange, statement


@pytest.mark.parametrize(("op", "payload"), [
    ("add_node", {"id": "X", "label": "Entity"}), ("add_node", {"id": "X", "label": "Model) DETACH DELETE n //"}), ("add_node", {"id": "", "label": "Model"}),
    ("add_node", {"id": "X", "label": "Model", "props": {"source": "seed"}}), ("add_node", {"id": "X", "label": "Model", "props": {"a b": 1}}),
    ("update_node", {"id": "X", "props": {"id": "Y"}}), ("update_node", {"id": "X", "props": {"a": ["x"]}}), ("update_node", {"id": 1}),
    ("add_edge", {"src": "A", "dst": "B", "type": "OWNS"}), ("add_edge", {"src": "A", "type": "FITS"}), ("delete_node", {"id": "X"})])
def test_rejected(op, payload):
    with pytest.raises(InvalidChange):
        statement(op, payload, "DOC-X", 1)


def test_values_are_parameters_and_carry_provenance():
    cy, p = statement("add_node", {"id": "DHD-E11", "label": "ErrorCode", "props": {"name": "x'}) DELETE"}}, "DOC-X", 7)
    assert "DELETE" not in cy and "ON CREATE SET n:ErrorCode" in cy and (p["source"], p["chunk"]) == ("upload:DOC-X", 7)
