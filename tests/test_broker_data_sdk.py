"""Pin the exact SDK mapping while leaving live acceptance independent."""

import inspect

from moomoo import OpenQuoteContext, OptionDataFilter
from moomoo.common.pb import Qot_Common_pb2

from moomoo_mcp.services.data_evidence import OptionChainFilter
from scripts.inspect_broker_data_sdk import inspect_sdk


def test_selected_filter_fields_and_periods_exist_in_installed_sdk():
    fields = set(OptionChainFilter.model_fields)
    assert fields <= set(inspect.signature(OptionDataFilter).parameters)
    assert {"option_cond_type", "data_filter"} <= set(
        inspect.signature(OpenQuoteContext.get_option_chain).parameters
    )
    enum = Qot_Common_pb2.DESCRIPTOR.enum_types_by_name[
        "OptionVolatilityTimePeriodType"
    ]
    assert {value.number for value in enum.values} == {0, 1, 2, 3, 4, 5}


def test_offline_evidence_never_claims_gateway_acceptance():
    report = inspect_sdk()
    assert report["evidence_layer"] == "INSTALLED_SDK_OFFLINE"
    assert report["gateway_support"] == "UNKNOWN"
    assert report["sdk_version"] == "10.10.7008"
    assert all(row["present"] for row in report["operations"])
    assert all(len(row["source_sha256"]) == 64 for row in report["operations"])
