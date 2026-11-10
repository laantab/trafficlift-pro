from backend.heygen_video_api import product_prompt


def test_locked_baking_mat_prompt_preserves_visible_removal_sequence():
    prompt = product_prompt(
        "Non-Stick Silicone Baking Mat",
        {"mode": "product_overview", "evidence": []},
    )

    required_phrases = [
        "BAKING MAT DEMONSTRATION SEQUENCE",
        "Begin with cooked chicken resting naturally on the flat silicone mat.",
        "In one continuous, clearly visible removal shot",
        "use an appropriate food-safe utensil",
        "completely clear of the mat",
        "the mat must stay flat and stationary during the lift",
        "the viewer must see the actual removal action",
        "Only after the food-removal demonstration is complete may there be a clean cut to the same mat EMPTY",
        "No food may clip through, sink into, merge with, melt through, teleport through, or disappear behind the mat.",
        "Speak exactly the following once",
    ]
    for phrase in required_phrases:
        assert phrase in prompt


def test_locked_baking_mat_rules_do_not_leak_into_other_products():
    prompt = product_prompt(
        "Adjustable Drawer Organizer",
        {"mode": "product_overview", "evidence": [{"text": "Adjustable storage divider"}]},
    )

    assert "BAKING MAT DEMONSTRATION SEQUENCE" not in prompt
    assert "cooked chicken" not in prompt
