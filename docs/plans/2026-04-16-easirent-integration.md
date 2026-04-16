# Easirent Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate Easirent into the gateway with static business rules and reference metadata first, then complete live search and booking once the XML POST contract is confirmed from the live feed.

**Architecture:** Add a new `easirent` adapter to the FastAPI gateway. Split the work into three layers: documented business rules and account-code selection, reference fleet/location metadata, and live XML feed interaction. This avoids guessing the supplier contract while still moving the integration forward safely.

**Tech Stack:** Python 3.11, FastAPI gateway, Pydantic schemas, `httpx`, `pytest`, static supplier YAML config, optional XML parsing via `lxml` or `xml.etree.ElementTree`.

## Known Inputs and Constraints

- Live endpoint: `https://easirent.com/broker/Vrooem/Livefeed.asp`
- The endpoint is reachable and returns XML.
- A plain GET returns:
  - `<error><number>90001</number><description>No post data found.</description></error>`
- Account codes from supplier email:
  - `$USA202` — exclusive net pay-on-arrival, no insurance, US domestic only
  - `$USA202A` — inclusive net pay-on-arrival, basic CDW included, non-US inbound to US
  - `$ROI202` — ROI product for all except US customers travelling to ROI
- Supplier pack includes:
  - US fleet reference sheet
  - ROI fleet reference sheet
  - ROI location sheet
  - US and ROI pickup instruction sheets
  - commercial terms for US exclusive and inclusive products
- Known business concerns already visible from the pack:
  - account code must be selected before search
  - `XXAR` appears in the US fleet sheet as a placeholder/special class
  - one-way bookings must be supported and tested
  - supplier requires test bookings for all accounts
- Missing from the pack:
  - exact XML POST request shape for search
  - exact XML response schema for search
  - booking request/response schema
  - cancel request/response schema

## Task 1: Add Easirent supplier configuration

**Files:**
- Create: `config/suppliers/easirent.yaml`
- Modify: `app/core/config.py`
- Test: none

**Step 1: Add supplier YAML config**

Create `config/suppliers/easirent.yaml` with:
- `id: easirent`
- `name: Easirent`
- `enabled: false` initially
- `protocol: legacy_xml_post`
- `auth_type: account_code`
- `supports_one_way: true`
- `default_currency: USD`
- `countries: ["US", "ROI"]`
- notes explaining the three account codes and staging status

**Step 2: Add gateway settings**

Add config values for:
- `easirent_api_url`
- `easirent_account_us_domestic`
- `easirent_account_us_inbound`
- `easirent_account_roi`

Default `easirent_api_url` to the live feed URL from the supplier email.

**Step 3: Do not enable the supplier yet**

Keep `enabled: false` until the search XML contract is verified.

## Task 2: Add Easirent business-rule helpers

**Files:**
- Create: `app/adapters/easirent_rules.py`
- Create: `tests/test_easirent_rules.py`

**Step 1: Write failing tests**

Add tests covering:
- US domestic customers searching US pickup → select `$USA202`
- non-US customers searching US pickup → select `$USA202A`
- US customers searching ROI pickup → excluded / no account available
- non-US customers searching ROI pickup → select `$ROI202`
- `XXAR` fleet row is treated as placeholder/special and excluded from canonical fleet mapping

**Step 2: Run tests to verify failure**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_rules.py -q`

Expected:
- fail because module/functions do not exist yet

**Step 3: Implement minimal helpers**

Create pure functions such as:
- `select_account_code(customer_country_code: str | None, pickup_country_code: str | None, settings) -> str | None`
- `is_placeholder_vehicle_code(sipp_code: str | None) -> bool`

Assumption:
- use `SearchRequest.country_code` as the customer country/origin signal
- if that assumption later proves wrong, only this rule layer should change

**Step 4: Re-run tests**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_rules.py -q`

Expected:
- pass

## Task 3: Add static Easirent reference metadata loader

**Files:**
- Create: `app/adapters/easirent_reference.py`
- Create: `tests/test_easirent_reference.py`
- Create: `config/suppliers/easirent_us_fleet.json`
- Create: `config/suppliers/easirent_roi_fleet.json`
- Create: `config/suppliers/easirent_roi_locations.json`
- Create: `config/suppliers/easirent_collection_details.json`
- Optional helper script: `scripts/parse_easirent_reference.py`

**Step 1: Write failing tests**

Test:
- US fleet loader returns canonical metadata by SIPP code
- ROI fleet loader returns canonical metadata by SIPP code
- `XXAR` is not exposed as a normal fleet mapping
- ROI locations include `DUB`, `ORK`, `SNN` with correct airport metadata
- collection instructions resolve by station/IATA code

**Step 2: Run tests to verify failure**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_reference.py -q`

Expected:
- fail because files/loader do not exist yet

**Step 3: Add normalized static JSON files**

Normalize the spreadsheet content into repo-owned JSON:
- compact and explicit
- no runtime dependency on the user’s Downloads folder

Recommended shapes:
- fleet rows keyed by SIPP or vendor group code
- locations keyed by station code / IATA
- collection details keyed by station code plus collection type

**Step 4: Implement loader**

Expose functions like:
- `load_us_fleet()`
- `load_roi_fleet()`
- `load_roi_locations()`
- `load_collection_details()`
- `resolve_fleet_metadata(country_code: str, sipp_code: str | None)`

**Step 5: Re-run tests**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_reference.py -q`

Expected:
- pass

## Task 4: Add Easirent adapter skeleton

**Files:**
- Create: `app/adapters/easirent.py`
- Modify: `app/adapters/registry.py` only if alias handling is needed
- Create: `tests/test_easirent_adapter.py`

**Step 1: Write failing tests**

Test:
- adapter registers as `supplier_id = "easirent"`
- `supports_one_way = True`
- `get_locations()` returns static ROI locations initially
- search without resolved account code returns empty result or explicit skip

**Step 2: Run tests to verify failure**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_adapter.py -q`

Expected:
- fail because adapter does not exist yet

**Step 3: Implement minimal adapter**

Implement:
- `supplier_id = "easirent"`
- `supplier_name = "Easirent"`
- `supports_one_way = True`
- static `get_locations()` using reference files
- `search_vehicles()` that:
  - resolves account code from the rules helper
  - returns empty with a logged warning if no account is valid
  - raises `NotImplementedError` or returns empty if request contract is not yet implemented

Do not implement booking yet in this task.

**Step 4: Re-run tests**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_adapter.py -q`

Expected:
- pass

## Task 5: Discover the XML search request/response contract

**Files:**
- Modify: `app/adapters/easirent.py`
- Create or extend: `tests/test_easirent_adapter.py`

**Step 1: Capture safe diagnostics**

Use the live endpoint only for minimal discovery:
- verify required POST structure
- inspect error codes/messages for malformed XML
- confirm whether account code is passed in request body
- confirm search fields for pickup/dropoff/date/time/driver age

Do not brute-force or spam the supplier endpoint.

**Step 2: Add failing parser test**

Once one sample XML response is captured, add a fixture and test:
- parse one available vehicle result into canonical `Vehicle`
- map SIPP, image, deposit/excess, mileage, locations, and pay-on-arrival flags

**Step 3: Implement XML request builder and parser**

Keep implementation small:
- request builder
- response parser
- canonical vehicle mapper

**Step 4: Re-run targeted tests**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_adapter.py -q`

Expected:
- pass

## Task 6: Add booking and cancel support

**Files:**
- Modify: `app/adapters/easirent.py`
- Create or extend: `tests/test_easirent_booking.py`

**Step 1: Write failing tests from real supplier fixtures**

Cover:
- create booking request for US domestic account
- create booking request for US inbound account
- create booking request for ROI account
- one-way booking payload
- cancel payload if supported

**Step 2: Verify tests fail**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_booking.py -q`

Expected:
- fail

**Step 3: Implement minimal booking support**

Map:
- main driver details
- flight details if required
- extras where supported
- pay-on-arrival totals and deposit behavior
- raw supplier confirmation ids into `BookingResponse`

**Step 4: Re-run tests**

Run:
`cd /mnt/c/laragon/www/vrooem-gateway && pytest tests/test_easirent_booking.py -q`

Expected:
- pass

## Task 7: Enable supplier and verify in gateway search

**Files:**
- Modify: `config/suppliers/easirent.yaml`
- Optional env updates in deployment config

**Step 1: Enable supplier only after tests and fixtures are ready**

Set:
- `enabled: true`

**Step 2: Verify gateway search**

Check:
- Easirent appears only for valid account/country combinations
- no `XXAR` placeholder rows are exposed
- one-way behavior is respected
- pay-on-arrival and coverage differences are visible in `supplier_data`

**Step 3: Verify booking**

Complete supplier-required test bookings:
- standard pickup/dropoff
- one-way
- one booking per account code

## Residual Risks

- The biggest blocker is still the exact live XML request/response contract.
- The supplier pack does not document booking/cancel message formats.
- The account-code rules depend on what `SearchRequest.country_code` means in real user flows.
- US locations in the provided pack do not include structured coordinates; location enrichment may need a secondary source.

## Recommended Immediate Next Slice

Start with Task 2 and Task 3:
- Easirent rules
- Easirent reference metadata

That gives a clean, testable foundation without guessing the live XML schema.
