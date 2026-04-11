from scripts.run_kpi_benchmark_pack import MIN_BENCHMARK_TIMEOUT_SEC, _effective_timeout_cap


def test_effective_timeout_cap_enforces_minimum_budget() -> None:
    assert _effective_timeout_cap(180) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(600) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(900) == 900
