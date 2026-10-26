#!/usr/bin/env python3
"""Print bounded installed-SDK evidence offline; never connect to a gateway."""

import hashlib
import inspect
import json

from moomoo import OpenQuoteContext, OptionDataFilter

from moomoo_mcp.services.data_evidence import DATA_CAPABILITIES, sdk_version


def inspect_sdk() -> dict:
    operations = []
    for name in DATA_CAPABILITIES:
        method = getattr(OpenQuoteContext, name, None)
        operations.append(
            {
                "method": name,
                "present": callable(method),
                "signature": str(inspect.signature(method))
                if callable(method)
                else None,
                "source_sha256": hashlib.sha256(
                    inspect.getsource(method).encode("utf-8")
                ).hexdigest()
                if callable(method)
                else None,
            }
        )
    return {
        "sdk_version": sdk_version(),
        "evidence_layer": "INSTALLED_SDK_OFFLINE",
        "gateway_support": "UNKNOWN",
        "region_entitlement": "UNKNOWN",
        "option_filter_signature": str(inspect.signature(OptionDataFilter)),
        "operations": operations,
    }


if __name__ == "__main__":
    print(json.dumps(inspect_sdk(), indent=2, sort_keys=True))
