"""Small utilities for scan calculations used by the GUI and CLI runner."""

def compute_step(length, points):
    """Compute step size given length and number of points.

    Uses the same rule as the GUI: avoid division by zero by using max(1, points).
    Returns a float step size.
    """
    pts = max(1, int(points))
    return float(length) / pts
