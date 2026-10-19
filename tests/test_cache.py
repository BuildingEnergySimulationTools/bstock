import pandas as pd
import pytest

from bstock.cache import read_period, update_cache
from bstock.exceptions import InvalidArgumentError, TimeSeriesError
from bstock.meters import Channel, Meter
from bstock.readers import ParquetReader

TZ = "Europe/Paris"


class FakeApi:
    """Daily values from ``first`` to ``last`` (exclusive); records the windows asked."""

    def __init__(self, first="2024-01-01", last="2024-03-01", fail_after=None):
        self.days = pd.date_range(first, last, freq="D", tz=TZ, inclusive="left")
        self.calls = []
        self.fail_after = fail_after

    def __call__(self, start, end):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise ConnectionError("API down")
        self.calls.append((start, end))
        days = self.days[(self.days >= start) & (self.days < end)]
        return pd.DataFrame({"energy": [float(d.day) for d in days]}, index=days)


def update(path, api, start="2024-01-01", end="2024-03-01", **kwargs):
    return update_cache(path, api, start=start, end=end, timestep="1D", tz=TZ, **kwargs)


def ts(text):
    return pd.Timestamp(text, tz=TZ)


def test_first_update_fetches_the_window_and_records_its_coverage(tmp_path):
    path = tmp_path / "m.parquet"
    result = update(path, FakeApi())
    assert result.rows == 60
    assert result.period == read_period(path, TZ) == (ts("2024-01-01"), ts("2024-03-01"))
    assert len(pd.read_parquet(path)) == 60


def test_second_update_fetches_nothing(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi())
    api = FakeApi()
    assert update(path, api).fetched == []
    assert api.calls == []


def test_update_fetches_only_after_and_before_the_coverage(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(), start="2024-01-10", end="2024-02-01")
    api = FakeApi()
    update(path, api)
    assert api.calls == [(ts("2024-01-01"), ts("2024-01-10")), (ts("2024-02-01"), ts("2024-03-01"))]
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-03-01"))
    assert len(pd.read_parquet(path)) == 60


def test_coverage_stops_after_the_last_sample_so_recent_days_are_asked_again(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(last="2024-02-10"))
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-02-10"))
    api = FakeApi()
    update(path, api)
    assert api.calls == [(ts("2024-02-10"), ts("2024-03-01"))]


def test_window_without_data_is_not_recorded(tmp_path):
    path = tmp_path / "m.parquet"
    result = update(path, FakeApi(first="2025-01-01", last="2025-02-01"))
    assert result.period is None
    assert not path.exists()


def test_empty_chunk_before_data_is_covered(tmp_path):
    path = tmp_path / "m.parquet"
    api = FakeApi(first="2024-02-01")
    update(path, api, chunk="10D")
    assert len(api.calls) == 6
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-03-01"))


def test_chunks_limit_each_fetch(tmp_path):
    api = FakeApi()
    update(tmp_path / "m.parquet", api, chunk="31D")
    assert api.calls == [(ts("2024-01-01"), ts("2024-02-01")), (ts("2024-02-01"), ts("2024-03-01"))]


def test_received_rows_replace_cached_rows(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(last="2024-01-05"))

    def corrected(start, end):
        days = pd.date_range(start, end, freq="D", inclusive="left")
        return pd.DataFrame({"energy": 0.0}, index=days)

    update_cache(path, corrected, start="2024-01-05", end="2024-01-06", timestep="1D", tz=TZ)
    # a new window only: the cached rows before it are kept
    assert pd.read_parquet(path)["energy"].tolist() == [1.0, 2.0, 3.0, 4.0, 0.0]


def test_rows_outside_the_window_are_dropped(tmp_path):
    path = tmp_path / "m.parquet"

    def too_much(start, end):
        days = pd.date_range("2023-12-25", "2024-01-10", freq="D", tz=TZ)
        return pd.DataFrame({"energy": 1.0}, index=days)

    update_cache(path, too_much, start="2024-01-01", end="2024-01-05", timestep="1D", tz=TZ)
    assert len(pd.read_parquet(path)) == 4


def test_data_received_before_an_error_is_saved(tmp_path):
    path = tmp_path / "m.parquet"
    with pytest.raises(ConnectionError):
        update(path, FakeApi(fail_after=1), chunk="31D")
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-02-01"))
    api = FakeApi()
    update(path, api)
    assert api.calls == [(ts("2024-02-01"), ts("2024-03-01"))]


def test_daily_coverage_end_is_a_local_midnight_across_dst(tmp_path):
    path = tmp_path / "m.parquet"
    api = FakeApi(first="2024-03-25", last="2024-04-02")
    update(path, api, start="2024-03-25", end="2024-04-10")
    assert read_period(path, TZ)[1] == ts("2024-04-02")


def test_naive_index_is_refused(tmp_path):
    def naive(start, end):
        return pd.DataFrame({"energy": [1.0]}, index=pd.DatetimeIndex(["2024-01-01"]))

    with pytest.raises(TimeSeriesError):
        update(tmp_path / "m.parquet", naive)


@pytest.mark.parametrize(
    "kwargs",
    [{"start": "2024-02-01", "end": "2024-01-01"}, {"timestep": "x"}, {"chunk": "bad"}],
)
def test_invalid_arguments(tmp_path, kwargs):
    arguments = {"start": "2024-01-01", "end": "2024-02-01", "timestep": "1D", **kwargs}
    with pytest.raises(InvalidArgumentError):
        update_cache(tmp_path / "m.parquet", FakeApi(), **arguments)


def test_cache_is_read_by_a_parquet_reader(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi())
    meter = Meter(
        id="m", channels=[Channel(name="energy", unit="kWh")], reader=ParquetReader(path=str(path))
    )
    assert len(meter.load_data()) == 60


def test_disjoint_window_after_the_period_is_extended_to_it(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(), start="2024-01-01", end="2024-01-10")
    api = FakeApi()
    update(path, api, start="2024-02-01", end="2024-02-10")
    assert api.calls == [(ts("2024-01-10"), ts("2024-02-10"))]
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-02-10"))


def test_disjoint_window_before_the_period_is_extended_to_it(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(), start="2024-02-01", end="2024-02-10")
    api = FakeApi()
    update(path, api, start="2024-01-01", end="2024-01-05")
    assert api.calls == [(ts("2024-01-01"), ts("2024-02-01"))]
    assert read_period(path, TZ) == (ts("2024-01-01"), ts("2024-02-10"))


def test_period_keeps_the_time_zone_across_dst(tmp_path):
    path = tmp_path / "m.parquet"
    update(path, FakeApi(), start="2024-01-01", end="2024-03-01")
    first, last = read_period(path, TZ)
    assert str(first.tz) == TZ
    assert str(last.tz) == TZ
    api = FakeApi(last="2024-06-01")
    update(path, api, end="2024-06-01", chunk="31D")
    # chunk boundaries are local midnights, also after the DST change
    assert all(start.hour == 0 for start, _ in api.calls)
