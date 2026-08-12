# DateTime provider ABI

Live clock hooks use signed UTC Unix seconds and an explicit provider:
`DateTime.Now`, `DateTime.UtcNow`, and `DateTime.UnixTimestamp` return the
provider's wall-clock value in seconds since `1970-01-01T00:00:00Z`.

The null provider and Python/JavaScript fixed-clock options freeze all three
hooks to the configured value. A live provider may also expose a monotonic
tick through its host implementation, but monotonic values are never used as
wall-clock timestamps.

Calendar parsing, formatting, arithmetic, weekday, and day-of-year operations
remain pure and deterministic. Timezone names are provider/database inputs;
unsupported timezone data returns an explicit unavailable/invalid status
instead of silently using the machine's local timezone.

Provider failures use `Status.Last`: `0` is success, `1` unavailable, and `2`
invalid time/provider data. PIOS obtains live time through its kernel time lane;
hosted Windows/POSIX providers use the platform clock. Tests must use a fixed
clock whenever bytecode output is compared.
