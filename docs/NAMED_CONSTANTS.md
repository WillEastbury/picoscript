# PicoScript named constants, enums, and localization

PicoScript ships a built-in constant catalog (`NAMED_CONSTANTS`) and localized
metadata (`NAMED_CONSTANT_I18N`) from `picoscript_lang.py`.

These constants resolve in all four frontends (C, BASIC, Python, English) and
in the browser compiler (`vm/picoc.js`) with Python/JS bytecode parity.

## Canonical built-in families

| Family | Prefix / form | Notes |
|--------|----------------|-------|
| HTTP methods | `HTTP_METHOD_*`, `METHOD_*`, `HTTPMETHOD.*` | `Req.Method()` values |
| HTTP status | `HTTP_STATUS_*`, `STATUS_*`, `HTTPSTATUS.*` | `Resp.Status()` values |
| Weekday | `DAY_*`, `DAY.*` | ISO-style Monday=1..Sunday=7 |
| Month | `MONTH_*`, `MONTH.*` | January=1..December=12 |
| Time zone | `TZ_*`, `TZ.*`, `TIMEZONE.*` | Stable enum IDs; host maps to tzdb rules |
| DST | `DST_*`, `DST.*` | `NONE`, `OBSERVED`, `ACTIVE` |
| Currency code | `CURRENCY_*`, `CURRENCY.*` | ISO-4217 numeric values |
| Currency minor units | `CURRENCY_MINOR_*`, `CURRENCYMINOR.*` | Decimal places per currency |
| Country code | `COUNTRY_*`, `COUNTRY.*` | ISO-3166-1 numeric values |
| Units of measure | `UOM_*`, `UOM.*` | SI base + common derived units |
| Colours | `COLOR_*`, `COLOR.*` | 24-bit RGB values |
| Integer sizing / masks | `UINT*`, `INT*`, `MASK*`, `SIGN*` | 8/16/24/32-bit sizing constants |
| Conversion constants | `*_PER_*`, `PI_Q16`, `RAD_PER_DEG_Q16`, `DEG_PER_RAD_Q16` | Time/size/unit/fixed-point conversions |

## Standard values (canonical keys)

### HTTP methods
`HTTP_METHOD_GET=1`, `POST=2`, `PUT=3`, `DELETE=4`, `HEAD=5`, `PATCH=6`, `OPTIONS=7`, `CONNECT=8`, `TRACE=9`

### HTTP status
`HTTP_STATUS_OK=200`, `CREATED=201`, `ACCEPTED=202`, `NO_CONTENT=204`, `BAD_REQUEST=400`,
`UNAUTHORIZED=401`, `FORBIDDEN=403`, `NOT_FOUND=404`, `CONFLICT=409`,
`UNPROCESSABLE_ENTITY=422`, `TOO_MANY_REQUESTS=429`,
`INTERNAL_SERVER_ERROR=500`, `NOT_IMPLEMENTED=501`, `BAD_GATEWAY=502`,
`SERVICE_UNAVAILABLE=503`

### Days / months
- Days: `DAY_MONDAY..DAY_SUNDAY` = `1..7`
- Months: `MONTH_JANUARY..MONTH_DECEMBER` = `1..12`

### Time zones / DST
- Time zones: `TZ_UTC=0`, `TZ_EUROPE_LONDON=1`, `TZ_EUROPE_PARIS=2`,
  `TZ_AMERICA_NEW_YORK=3`, `TZ_AMERICA_CHICAGO=4`, `TZ_AMERICA_DENVER=5`,
  `TZ_AMERICA_LOS_ANGELES=6`, `TZ_ASIA_TOKYO=7`, `TZ_ASIA_SINGAPORE=8`,
  `TZ_ASIA_HONG_KONG=9`, `TZ_AUSTRALIA_SYDNEY=10`, `TZ_ASIA_DUBAI=11`
- DST: `DST_NONE=0`, `DST_OBSERVED=1`, `DST_ACTIVE=2`

### Currencies (ISO-4217 numeric)
`CURRENCY_USD=840`, `EUR=978`, `GBP=826`, `JPY=392`, `CNY=156`, `AUD=36`,
`CAD=124`, `CHF=756`, `SEK=752`, `NOK=578`, `NZD=554`, `INR=356`, `SGD=702`,
`HKD=344`, `AED=784`, `BRL=986`, `ZAR=710`, `KRW=410`, `MXN=484`

Minor units: `CURRENCY_MINOR_*` (for the same codes) where `JPY=0`, `KRW=0`, most others `=2`.

### Countries (ISO-3166-1 numeric)
`COUNTRY_US=840`, `GB=826`, `FR=250`, `DE=276`, `ES=724`, `IT=380`, `NL=528`,
`SE=752`, `NO=578`, `DK=208`, `FI=246`, `CH=756`, `IE=372`, `PL=616`, `PT=620`,
`AU=36`, `NZ=554`, `JP=392`, `CN=156`, `HK=344`, `SG=702`, `IN=356`, `AE=784`,
`BR=76`, `ZA=710`, `KR=410`, `MX=484`, `CA=124`

### Units / colors
- Units: `UOM_METER`, `KILOGRAM`, `SECOND`, `AMPERE`, `KELVIN`, `MOLE`, `CANDELA`,
  plus `UOM_LITER`, `UOM_GRAM`, `UOM_CELSIUS`
- Colors: `COLOR_BLACK`, `WHITE`, `RED`, `GREEN`, `BLUE`, `YELLOW`, `CYAN`,
  `MAGENTA`, `ORANGE`, `GRAY` (`GREY` alias)

### Integer sizing / conversions
- Bit sizing: `BITS_PER_BYTE`, `UINT8_MAX`, `UINT16_MAX`, `UINT24_MAX`, `UINT32_MAX`,
  `INT8_MIN/MAX`, `INT16_MIN/MAX`, `INT24_MIN/MAX`, `INT32_MIN/MAX`,
  `MASK8/16/24/32`, `SIGN8/16/24/32`
- Conversion: `MS_PER_SECOND`, `SECONDS_PER_MINUTE`, `MINUTES_PER_HOUR`,
  `HOURS_PER_DAY`, `DAYS_PER_WEEK`, `BYTES_PER_KIB`, `BYTES_PER_MIB`,
  `MM_PER_METER`, `CM_PER_METER`, `GRAMS_PER_KILOGRAM`,
  `PI_Q16`, `RAD_PER_DEG_Q16`, `DEG_PER_RAD_Q16`

## Locale formatting (`toLocale`)

Python:

```python
from picoscript_lang import toLocale

print(toLocale("HTTP_STATUS_NOT_FOUND"))
print(toLocale("TZ_EUROPE_LONDON", "en-GB", {
    "en-GB": {
        "TZ_EUROPE_LONDON": {
            "label": "UK time",
            "description": "Europe/London with DST transitions."
        }
    }
}))
```

Browser/JS (`vm/pico_hooks.js`):

```javascript
PV_HOOKS.toLocale("CURRENCY_USD"); // built-in English metadata
PV_HOOKS.toLocale("COUNTRY_GB", "en", {
  en: { COUNTRY_GB: { label: "United Kingdom" } }
});
```

`user_dictionary` can be:
1. Flat: `{ "CURRENCY_USD": { label, description } }`
2. Locale-scoped: `{ "fr": { "CURRENCY_USD": { ... } } }`

Built-in English metadata is always the fallback.

## Runtime locale hooks (`Locale.*`)

PicoScript now supports runtime locale/timezone configuration in the VM and
browser harness:

- `Locale.SetLocale(localeSpan, tzSpanOrId)`
- `Locale.GetCurrentLocale()`
- `Locale.FormatNumber(value, scale)`
- `Locale.FormatCurrency(minorUnits, currencyCodeOrNumeric)`
- `Locale.FormatDate(epochSecondsUtc, tzSpanOrId)`
- `Locale.FormatTime(epochSecondsUtc, tzSpanOrId)`
- `Locale.Translate(keySpan, localeSpan)`

Semantics:
- Date/time values are stored as **UTC epoch-seconds**.
- Date/time formatting always includes an explicit offset (`+HH:MM`/`-HH:MM`).
- In browser runtime, default locale/timezone come from browser settings unless
  overridden by `Locale.SetLocale`.
- Number/currency formatting is roundtrip-safe (no grouping, explicit decimal scale).

## User-defined constants and enums (all frontends)

### C-style (`.pc`)

```c
const RETRY = 3;
enum HttpCode { OK = 200, CREATED = 201, ACCEPTED };
Io.WriteByte(RETRY);
Io.WriteByte(HttpCode.OK);
Io.WriteByte(HTTPCODE_CREATED);
```

### BASIC (`.pbas`)

```basic
CONST RETRY = 3
ENUM HTTPCODE
OK = 200
CREATED = 201
ACCEPTED
ENDENUM
Io.WriteByte(RETRY)
Io.WriteByte(HTTPCODE_OK)
```

### Python-style (`.ppy`)

```python
const RETRY = 3
enum HttpCode:
    OK = 200
    CREATED = 201
    ACCEPTED
Io.WriteByte(RETRY)
Io.WriteByte(HTTPCODE_OK)
```

### English (`.eng`)

```text
Define constant RETRY as 3.
Define enum HttpCode:
    OK is 200.
    CREATED is 201.
    ACCEPTED.
Io.WriteByte(RETRY).
Io.WriteByte(HTTPCODE_OK).
```

Enum members are available as compile-time constants. For cross-language source,
prefer `ENUMNAME_MEMBER` form (for example `HTTPCODE_OK`).

## Source of truth

The authoritative definitions are in:
- `picoscript_lang.py` (`HTTP_NAMED_CONSTANTS`, `SYSTEM_NAMED_CONSTANTS`, `NAMED_CONSTANT_I18N`)
- `tests/test_named_constants.py` (cross-frontend + Python/JS parity coverage, `toLocale`, user-defined `const`/`enum`)
