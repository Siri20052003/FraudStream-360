from fraudstream.simulator import generate_transactions


def test_simulation_is_reproducible_and_chronological() -> None:
    first = list(generate_transactions(200, seed=17))
    second = list(generate_transactions(200, seed=17))
    assert first == second
    assert first == sorted(first, key=lambda event: event.occurred_at)
    assert {event.fraud_pattern for event in first} - {"legitimate"}


def test_invalid_generation_arguments() -> None:
    for kwargs in ({"count": 0}, {"fraud_rate": 1.1}):
        try:
            list(generate_transactions(**kwargs))
        except ValueError:
            pass
        else:
            raise AssertionError("invalid simulation parameters were accepted")
