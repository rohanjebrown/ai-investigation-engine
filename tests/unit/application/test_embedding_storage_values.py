from dataclasses import replace

import pytest

from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector


def test_float32_conversion_preserves_values_without_normalizing():
    vector = storage_vector((0.1, 2.0) + (0.0,) * 766)
    assert vector[0] == 0.10000000149011612 and vector[1] == 2.0
    assert isinstance(vector, tuple) and storage_vector(vector) == vector


@pytest.mark.parametrize(
    "values",
    [
        (1.0,) * 767,
        (0.0,) * 768,
        (1e-100,) * 768,
        (1e100,) + (0.0,) * 767,
        (float("nan"),) + (0.0,) * 767,
        (float("inf"),) + (0.0,) * 767,
        (True,) + (0.0,) * 767,
        ("1",) + (0.0,) * 767,
    ],
)
def test_vectors_must_remain_valid_in_float32_storage(values):
    with pytest.raises(ValueError):
        storage_vector(values)


@pytest.mark.parametrize(
    "changes",
    [
        dict(dimensions=767),
        dict(document_format="body_only"),
        dict(normalization="l2"),
        dict(model_revision=" "),
        dict(provider=""),
    ],
)
def test_only_representable_explicit_profiles_are_accepted(changes):
    with pytest.raises(ValueError):
        replace(EmbeddingConfig(provider="synthetic", model_id="test"), **changes)
