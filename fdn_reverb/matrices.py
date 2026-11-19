"""Feedback-matrix generators and orthogonality diagnostics."""

from __future__ import annotations

import numpy as np


def _validate_square(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("feedback matrix must be square")
    return matrix


def _orthogonalize(matrix: np.ndarray) -> np.ndarray:
    """Return the nearest orthogonal factor through a polar decomposition."""
    u, _, vt = np.linalg.svd(matrix)
    return u @ vt


def orthogonality_error(matrix: np.ndarray) -> float:
    """Frobenius distance from identity after M.T @ M, normalized by N."""
    matrix = _validate_square(matrix)
    identity = np.eye(matrix.shape[0])
    return float(np.linalg.norm(matrix.T @ matrix - identity, "fro") / np.sqrt(matrix.shape[0]))


def build_feedback_matrix(kind: str, size: int, seed: int = 7) -> np.ndarray:
    """Build an energy-normalized matrix with a requested orthogonality.

    Supported kinds:
    - ``hadamard``: recursive Hadamard matrix (size must be a power of two).
    - ``householder``: deterministic symmetric Householder reflection.
    - ``qr``: random orthogonal matrix from QR decomposition.
    - ``nonorthogonal``: an eigenvalue-preserving diagonal similarity transform.
    """
    if size < 2:
        raise ValueError("matrix size must be at least 2")
    rng = np.random.default_rng(seed)
    kind = kind.lower()

    if kind == "hadamard":
        if size & (size - 1):
            raise ValueError("Hadamard size must be a power of two")
        matrix = np.array([[1.0]])
        while matrix.shape[0] < size:
            matrix = np.block([[matrix, matrix], [matrix, -matrix]])
        return matrix / np.sqrt(size)

    if kind == "householder":
        vector = rng.standard_normal(size)
        vector[0] += np.copysign(np.linalg.norm(vector), vector[0])
        vector /= np.linalg.norm(vector)
        return np.eye(size) - 2.0 * np.outer(vector, vector)

    if kind == "qr":
        q, r = np.linalg.qr(rng.standard_normal((size, size)))
        signs = np.sign(np.diag(r))
        signs[signs == 0.0] = 1.0
        return q * signs

    if kind == "nonorthogonal":
        base = build_feedback_matrix("householder", size, seed=seed)
        scale = np.geomspace(0.35, 1.65, size)
        return scale[:, None] * base / scale[None, :]

    raise ValueError(f"unknown feedback-matrix kind: {kind}")


def geodesic_path(
    start: np.ndarray,
    stop: np.ndarray,
    steps: int = 257,
) -> np.ndarray:
    """Interpolate two orthogonal matrices while staying near orthogonal.

    The midpoint is projected back to SO(n)/O(n) with a polar decomposition.
    Sampling this path avoids per-sample SVD work while keeping every control
    point energy normalized.
    """
    start = _validate_square(start)
    stop = _validate_square(stop)
    if start.shape != stop.shape:
        raise ValueError("start and stop matrices must have equal shape")
    if steps < 2:
        raise ValueError("steps must be at least 2")

    fractions = np.linspace(0.0, 1.0, steps)[:, None, None]
    linear = (1.0 - fractions) * start + fractions * stop
    if max(orthogonality_error(start), orthogonality_error(stop)) > 1e-8:
        return linear
    path = np.empty_like(linear)
    path[0] = start
    path[-1] = stop
    for index in range(1, steps - 1):
        path[index] = _orthogonalize(linear[index])
    return path
