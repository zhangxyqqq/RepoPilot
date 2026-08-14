def safe_divide(numerator: float, denominator: float) -> float:
    """Divide two numbers, raising a clear error for a zero denominator."""
    if denominator <= 0:
        raise ValueError("denominator must be non-zero")
    return numerator / denominator
