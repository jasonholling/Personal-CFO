import pytest
from glide_path_engine import interpolate_targets, glide_path_preview

def test_interpolates_each_asset_class_and_preserves_total():
    assert interpolate_targets({"us_large_cap": 60, "us_bonds": 40}, {"us_large_cap": 40, "us_bonds": 60}, 55, 65, 60) == {"us_large_cap": 50.0, "us_bonds": 50.0}

def test_clamps_before_and_after_schedule():
    assert interpolate_targets({"stocks": 80, "bonds": 20}, {"stocks": 50, "bonds": 50}, 55, 65, 50) == {"stocks": 80.0, "bonds": 20.0}

def test_preview_includes_both_endpoints():
    preview = glide_path_preview({"stocks": 80, "bonds": 20}, {"stocks": 60, "bonds": 40}, 60, 62)
    assert [row["age"] for row in preview] == [60, 61, 62]

def test_invalid_schedule_is_rejected():
    with pytest.raises(ValueError): interpolate_targets({}, {}, 65, 65, 65)
