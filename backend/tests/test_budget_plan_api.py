"""HTTP-level tests for the annual budget plan endpoints."""

import pytest


@pytest.mark.asyncio
async def test_year_plan_requires_auth(client):
    r = await client.get("/api/budgets/year-plan")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_year_plan_defaults_to_current_year_and_has_twelve_months(
    client, auth_headers, test_categories
):
    r = await client.get("/api/budgets/year-plan", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["year"] >= 2025
    assert len(body["planned_by_month"]) == 12
    assert len(body["actual_by_month"]) == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [1999, 2101])
async def test_year_plan_rejects_out_of_range_year(client, auth_headers, year):
    r = await client.get("/api/budgets/year-plan", params={"year": year}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_apply_then_read_back_round_trip(client, auth_headers, test_categories):
    cat = test_categories[0]
    r = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={
            "year": 2027,
            "cells": [
                {"category_id": str(cat.id), "month": 1, "amount": "250.00"},
                {"category_id": str(cat.id), "month": 12, "amount": "400"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": 2, "deleted": 0}

    r = await client.get("/api/budgets/year-plan", params={"year": 2027}, headers=auth_headers)
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["category_id"] == str(cat.id))
    assert float(row["cells"][0]["planned"]) == 250.0
    assert float(row["cells"][11]["planned"]) == 400.0
    assert row["cells"][5]["planned"] is None
    assert float(r.json()["planned_total"]) == 650.0

    # The Budgets page's own endpoint sees the same numbers.
    r = await client.get("/api/budgets", params={"month": "2027-01-01"}, headers=auth_headers)
    assert [float(b["amount"]) for b in r.json()] == [250.0]

    # Clearing a month removes it.
    r = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={"year": 2027, "cells": [{"category_id": str(cat.id), "month": 1, "amount": None}]},
    )
    assert r.json() == {"saved": 0, "deleted": 1}


@pytest.mark.asyncio
async def test_apply_validation_errors(client, auth_headers, test_categories):
    cat = str(test_categories[0].id)
    bad_month = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={"year": 2027, "cells": [{"category_id": cat, "month": 13, "amount": "1"}]},
    )
    assert bad_month.status_code == 422
    negative = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={"year": 2027, "cells": [{"category_id": cat, "month": 1, "amount": "-5"}]},
    )
    assert negative.status_code == 422
    oversized = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={"year": 2027, "cells": [{"category_id": cat, "month": 1, "amount": "1e20"}]},
    )
    assert oversized.status_code == 422
    unknown = await client.put(
        "/api/budgets/year-plan",
        headers=auth_headers,
        json={
            "year": 2027,
            "cells": [
                {"category_id": "00000000-0000-0000-0000-000000000001", "month": 1, "amount": "5"}
            ],
        },
    )
    assert unknown.status_code == 400


@pytest.mark.asyncio
async def test_suggestion_endpoint_shape_and_param_validation(
    client, auth_headers, test_categories
):
    r = await client.get(
        "/api/budgets/year-plan/suggestion",
        params={"lookback_months": 6, "method": "seasonal", "year": 2027},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2027
    assert body["method"] == "seasonal"
    assert body["lookback_months"] == 6
    assert body["history_start"] < body["history_end"]

    bad_method = await client.get(
        "/api/budgets/year-plan/suggestion", params={"method": "magic"}, headers=auth_headers
    )
    assert bad_method.status_code == 422
    short = await client.get(
        "/api/budgets/year-plan/suggestion", params={"lookback_months": 1}, headers=auth_headers
    )
    assert short.status_code == 422
