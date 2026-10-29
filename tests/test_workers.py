"""How many parallel test workers `-n auto` starts (tests/conftest.py): by CPUs, capped by memory."""

from conftest import WORKER_MEMORY, memory_limit, worker_count

GB = 1 << 30


def test_one_worker_per_cpu_while_memory_allows():
    assert worker_count(10, 32 * GB) == 10


def test_memory_caps_the_workers_and_keeps_one_spare():
    assert worker_count(16, 4 * GB) == 3  # a 4 GB runner container: 3 workers instead of 16
    assert worker_count(8, 8 * GB) == 7


def test_at_least_one_worker():
    assert worker_count(4, GB) == 1
    assert worker_count(0, None) == 1


def test_without_a_known_limit_the_cpus_count():
    assert worker_count(6, None) == 6


def test_this_machine_has_a_memory_limit():
    limit = memory_limit()
    assert limit is None or limit > WORKER_MEMORY
