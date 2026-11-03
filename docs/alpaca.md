# ASCOM Alpaca

OpenSkyMount serves an [ASCOM Alpaca](https://ascom-standards.org/api/) **Telescope** device, so programs that speak
Alpaca can read the mount position and send GoTo, Sync, Tracking and AbortSlew commands. It is on by default
(`--no-alpaca` turns it off).

**Status**: implemented and tested with automated tests, including an end-to-end run with
[alpyca](https://github.com/ASCOMInitiative/alpyca), the ASCOM Initiative's Python client. **Not yet tested** with
N.I.N.A., Stellarium, SkySafari or ASCOM ConformU (the official conformance checker could not be downloaded in the
development environment). Reports from real clients are welcome.

## Connecting

| Setting | Value |
|---|---|
| Address | the OpenSkyMount server, e.g. `127.0.0.1`, or the computer's LAN address when started with `--host 0.0.0.0` |
| Port | the web UI port (default `8000`) |
| Device | Telescope, device number `0` |
| Discovery | UDP port 32227 (IPv4). Answers on the address the server listens on, so with the default `--host 127.0.0.1` LAN broadcasts are not received: enter the address manually. `--no-alpaca-discovery` turns the responder off |
| Password mode | Alpaca requests need HTTP Basic authentication: any user name, the OpenSkyMount password. `/management/...` stays open (it only describes the device) |

Example with alpyca:

```python
from alpaca.telescope import Telescope
t = Telescope("127.0.0.1:8000", 0)
t.Connected = True
t.SlewToCoordinatesAsync(5.28, 46.0)   # RA hours, Dec degrees, apparent coordinates of date ("JNow")
while t.Slewing: ...
print(t.RightAscension, t.Declination, t.Tracking)
```

## How it fits the rest of OpenSkyMount

- **Sessions and control authority.** Each Alpaca client (address + `ClientID`) that sets `Connected=true`
  becomes a normal session and appears in the web UI's client list as `Alpaca #<ClientID> @<address>`. A motion
  command takes control automatically **only if nobody holds it**. If a browser holds control, the command fails
  with `InvalidOperationException` ("… has control of the mount"). The browser can take control back at any time.
  Every Alpaca request refreshes the client's control lease; a client silent for 5 minutes is disconnected.
- **Same safety rules.** All commands go through the same controller as the UI: solar exclusion (the Sun is never a
  target on real hardware), altitude and cable-wrap limits, E-stop latch and faults. `AbortSlew` is a stop and is
  accepted from every connected client, like Stop in the UI. There is no Alpaca E-stop; use the UI or
  `POST /api/estop`.
- **Coordinates.** `EquatorialSystem = equTopocentric` (1): RA/Dec are apparent coordinates of date (precession,
  nutation, aberration), what N.I.N.A. calls "JNow". Incoming coordinates are converted to an ICRS direction
  (iterative inverse of the apparent-place calculation, checked against Meeus example 23.a and Astropy's `TETE`
  frame within 0.5″) and tracked as a fixed sky position. `RightAscension`/`Declination` report the estimated
  pointing (pointing model applied), refraction removed.
- **Time.** `UTCDate` and `SiderealTime` follow the server clock. With simulated time (time warp in the UI) they
  report the simulated time; Alpaca clients that compute JNow from their own clock will then send slightly
  different coordinates. Use live time with external programs.

## Implemented members (ITelescopeV4, alt-az)

| Group | Members |
|---|---|
| Common | `Connected`, `Connect`, `Disconnect`, `Connecting`, `Description`, `DriverInfo`, `DriverVersion`, `InterfaceVersion` (4), `Name`, `SupportedActions` (none), `DeviceState` |
| Position | `RightAscension`, `Declination`, `Altitude`, `Azimuth`, `SiderealTime`, `UTCDate` (read only) |
| Site | `SiteLatitude`, `SiteLongitude`, `SiteElevation` (read and write; writing needs control and stops motion, like changing the site in the UI) |
| Motion | `SlewToCoordinatesAsync`, `SlewToTargetAsync`, `SlewToAltAzAsync`, `AbortSlew`, `Slewing`, `Tracking` (read and write), `TargetRightAscension`, `TargetDeclination` |
| Alignment | `SyncToCoordinates`, `SyncToTarget`: add a point to the multi-star pointing model, like Sync in the UI |
| Fixed values | `AlignmentMode` = algAltAz, `EquatorialSystem` = equTopocentric, `TrackingRate` = sidereal (lunar/solar reported while tracking the Moon/Sun in the UI), `TrackingRates` = [sidereal], `RightAscensionRate`/`DeclinationRate` = 0, `DoesRefraction` = true when refraction is configured, `AtPark`/`AtHome` = false, `SlewSettleTime` = 0 |

**Not implemented** (`Can…` = false, `NotImplementedException`): `MoveAxis`, `PulseGuide`, `Park`/`Unpark`/`SetPark`,
`FindHome`, synchronous slews, `SyncToAltAz`, `SideOfPier`, guide rates, RA/Dec rate offsets, aperture and focal
length, writing `UTCDate`, `DoesRefraction` and `SlewSettleTime`. `MoveAxis` needs a dead-man design first (manual
motion in OpenSkyMount must be refreshed every second; Alpaca clients do not refresh it).

## Deliberate deviations from the ASCOM specification

- `SlewToCoordinatesAsync` and `SyncToCoordinates` do **not** require `Tracking=true` first. On this mount tracking
  is tied to a target: a coordinate slew always ends in tracking, and tracking cannot always be switched on first
  (pointing below the altitude limit, e.g. after an alt/az slew to the horizon).
- `Tracking=true` while idle starts tracking the sky position the mount points at. It is refused below the altitude
  limit.
- `SlewToAltAzAsync` does require `Tracking=false` (always possible).

## Protocol details

- `GET` parameters come from the query string, `PUT` parameters from the form body. Parameter names are
  case-insensitive. Missing or unparsable parameters give HTTP 400; out-of-range values give `InvalidValueException`
  (0x401). Unknown members give HTTP 400, other device numbers HTTP 404.
- Members other than the common ones return `NotConnectedException` (0x407) until the client sets
  `Connected=true`. Connection state is per client: disconnecting one Alpaca client never disconnects the mount.
- Management API: `/management/apiversions`, `/management/v1/description`, `/management/v1/configureddevices`
  (stable `UniqueID` derived from the state directory). `/setup` redirects to the web UI.
