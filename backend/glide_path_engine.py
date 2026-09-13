"""Opt-in glide-path math for Portfolio Coach.

Pure interpolation only: callers decide whether to use the result.  No
trade, account, or retirement calculation is changed by this module.
"""
from typing import Dict, List


def interpolate_targets(start: Dict[str, float], end: Dict[str, float], start_age: int, end_age: int, age: int) -> Dict[str, float]:
    if end_age <= start_age:
        raise ValueError("end_age must be greater than start_age")
    if age <= start_age:
        return {key: round(float(value or 0), 2) for key, value in start.items()}
    if age >= end_age:
        return {key: round(float(value or 0), 2) for key, value in end.items()}
    fraction = (age - start_age) / (end_age - start_age)
    keys = set(start) | set(end)
    return {key: round(float(start.get(key, 0) or 0) + (float(end.get(key, 0) or 0) - float(start.get(key, 0) or 0)) * fraction, 2) for key in keys}


def glide_path_preview(start: Dict[str, float], end: Dict[str, float], start_age: int, end_age: int) -> List[Dict]:
    return [{"age": age, "targets": interpolate_targets(start, end, start_age, end_age, age)} for age in range(start_age, end_age + 1)]
