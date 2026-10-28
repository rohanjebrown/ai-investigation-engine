"""Correctness checks for the schema-preserving retrieval hot-path changes."""

from sqlalchemy import func, literal, select

from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PYTHON_WHITESPACE


def test_sql_boundary_blankness_matches_python_and_previous_sql_for_all_whitespace(db_engine):
    values = ["", PYTHON_WHITESPACE, *PYTHON_WHITESPACE,
              *(char + "quasar" + char for char in PYTHON_WHITESPACE),
              "interior\u2000space", "😀", "\u200b", "\ufeff", "\u200b \n", "\n\t😀\u00a0"]
    with db_engine.connect() as connection:
        for value in values:
            current, previous = connection.execute(select(
                func.btrim(literal(value), PYTHON_WHITESPACE) != "",
                func.translate(literal(value), PYTHON_WHITESPACE, "") != "",
            )).one()
            assert current == previous == bool(value.strip())
