import numpy as np
import pytest

from fdn_reverb.matrices import (
    build_feedback_matrix,
    geodesic_path,
    orthogonality_error,
)


@pytest.mark.parametrize("kind", ["hadamard", "householder", "qr"])
def test_orthogonal_generators_are_energy_normalized(kind: str) -> None:
    matrix = build_feedback_matrix(kind, 8, seed=17)
    assert orthogonality_error(matrix) < 1e-12
    assert np.linalg.cond(matrix) == pytest.approx(1.0, rel=1e-12)


def test_nonorthogonal_variant_is_stable_but_measurably_nonorthogonal() -> None:
    matrix = build_feedback_matrix("nonorthogonal", 8, seed=17)
    eigenvalues = np.linalg.eigvals(matrix)
    assert orthogonality_error(matrix) > 1.0
    assert np.max(np.abs(eigenvalues)) == pytest.approx(1.0, rel=1e-10)


def test_geodesic_points_remain_orthogonal() -> None:
    start = build_feedback_matrix("hadamard", 8)
    stop = build_feedback_matrix("householder", 8)
    path = geodesic_path(start, stop, steps=33)
    assert np.max([orthogonality_error(item) for item in path]) < 1e-10

