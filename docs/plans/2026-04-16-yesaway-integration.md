## YesAway Integration Plan

Date: 2026-04-16

### Goal

Integrate YesAway into the gateway with enough data fidelity to support:

- unified location sync
- live availability search
- full pricing display
- deposit / pay-on-arrival split display
- insurance / protection display
- extras display
- booking creation
- booking lookup / cancellation

### Source Material Reviewed

- `C:\Users\Anshul\Downloads\yesaway-car-rental.txt`
- `C:\Users\Anshul\Downloads\yesaway\Javelin API Implementation Guide V2.0.docx`
- `C:\Users\Anshul\Downloads\yesaway\Location code & Net Rate List （Yesaway POA）-2026.xlsx`

### What Is Confirmed

#### Credentials and endpoint

- Production endpoint: `https://javelin-api.yesaway.com/services`
- Username: `vrooem`
- Password: supplied in partner email
- No separate test environment
- Test reservations should be marked as `TEST`

#### Live branch endpoint works

Using the live SOAP branch request (`OTA_VehLocSearchRQ`), the endpoint returns rich location metadata successfully.

Verified live fields include:

- store code
- airport code
- location name
- address
- city / country
- phone
- emergency phone
- email
- latitude / longitude
- business hours
- lead time
- pickup / dropoff guide text
- photo URLs
- terms and conditions HTML

Example confirmed live location:

- `HKT01` / `HKT`
- `Phuket Airport`

#### Documentation confirms rich availability data exists

The guide and sample availability response show that the YesAway search response should include:

- total charge
- prepaid total
- pay-on-delivery total
- deposit
- fee breakdown
- priced coverages
- special equipment / extras
- age restrictions

This is enough data for a strong booking/offer presentation if the live search contract is accepted.

#### Workbook confirms static package/rate-code mapping

The workbook provides partner-facing commercial rate codes such as:

- `W_TH_ORDER_BASE_ARRIVAL`
- `W_TH_ORDER_COM_ARRIVAL`
- `W_US_ORDER_BASE_ARRIVAL`
- `W_US_ORDER_NOPROTECTION_ARRIVAL`

It also provides commercial metadata such as:

- country
- package name
- partial-pay model
- insurance type
- excess amount
- inclusion notes
- deposit range

### Current Blocker

Live availability search (`OTA_VehAvailRateMoreRQ`) is not yet accepted by the supplier with the inputs currently provided.

Observed live responses:

- with basic auth but without rate-code attributes: permission-style rejection
- with real workbook rate codes: generic business error `500000`

This strongly suggests that the current supplier onboarding pack is still missing one or more of the following account-specific search values:

- package code for `RequestorID/@ID`
- company code for `CompanyName/@Code`
- company short name for `CompanyName/@CompanyShortName`
- supplier-specific vendor code for availability, if different from branch usage
- any search-only account certification YesAway applies separately from branch access

### Implementation Order

#### Phase 1: Reference layer

- add YesAway config/env settings
- add supplier YAML
- load static location/rate-code reference data from workbook
- implement branch sync using live `OTA_VehLocSearchRQ`

#### Phase 2: Search contract confirmation

- obtain the missing YesAway search account identifiers
- rerun live `OTA_VehAvailRateMoreRQ`
- capture a successful raw response
- verify:
  - pricing
  - prepaid amount
  - POD amount
  - deposit
  - coverages
  - extras

#### Phase 3: Availability adapter

- map live search results into gateway `Vehicle`
- preserve YesAway raw identifiers needed for booking
- normalize:
  - pricing
  - location metadata
  - protection / policy data
  - extras
  - deposit

#### Phase 4: Booking flow

- implement booking request (`OTA_VehResRQ`)
- implement booking lookup (`OTA_VehRetResRQ`)
- implement cancellation (`OTA_VehCancelRQ`)
- verify one-way and same-location test reservations

### Recommended Next Action

Do not guess the availability contract.

Ask YesAway to confirm the exact partner search identity fields for live availability:

- package code
- company code
- company short name
- any required supplier code for `VendorPref`

Once that is confirmed, the search adapter can be implemented against a real raw payload instead of sample documentation alone.
