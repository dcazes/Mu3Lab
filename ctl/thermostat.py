"""The household thermostat, paired with Mu3Lab directly over HomeKit (no Home Assistant, no cloud).

An administrator pairs it once in Settings > Home devices with the HomeKit code the thermostat
shows (an ecobee: Menu > Settings > HomeKit). Mu3Lab then speaks to it on the home network
like an iPhone would: HomeKit's own encryption, keys kept in Mu3Lab's secret store. The
manufacturer's app keeps working; a HomeKit device pairs with one home controller, so it
cannot also be in Apple Home.

Everyone in the household sees the thermostat on Home; only administrators change it.
Readings are kept a few seconds so many open screens ask the thermostat once.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ctl.store.secrets import SecretStore

SCOPE = "home_devices"
NAME = "thermostat"
ALIAS = "mu3lab-thermostat"
FIND_SECONDS = 8
READ_SECONDS = 20
CODE_DIGITS = 8
# HomeKit heating/cooling modes, as HomeKit numbers them.
MODES = ("off", "heat", "cool", "auto")
STATES = ("idle", "heating", "cooling")

_lock = asyncio.Lock()
_reading: tuple[float, dict[str, Any]] | None = None


class ThermostatError(RuntimeError):
    """Pairing, reading or changing the thermostat failed; the message is for the person."""


def paired(store: SecretStore | None = None) -> dict[str, Any] | None:
    saved = (store or SecretStore()).get(SCOPE, NAME)
    return saved if isinstance(saved, dict) and saved.get("pairing") else None


@asynccontextmanager
async def _controller() -> AsyncIterator[Any]:
    """A HomeKit controller listening for devices on the home network, for one operation."""
    try:
        from aiohomekit import Controller
        from aiohomekit.characteristic_cache import CharacteristicCacheMemory
        from aiohomekit.zeroconf import ZeroconfServiceListener
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError:
        raise ThermostatError("HomeKit support is not installed yet; run the installer again.") from None
    zeroconf = AsyncZeroconf()
    controller = Controller(async_zeroconf_instance=zeroconf, char_cache=CharacteristicCacheMemory())
    async with zeroconf:
        browser = AsyncServiceBrowser(
            zeroconf.zeroconf, ["_hap._tcp.local.", "_hap._udp.local."], listener=ZeroconfServiceListener()
        )
        try:
            async with controller:
                yield controller
        finally:
            await browser.async_cancel()


async def discover() -> list[dict[str, Any]]:
    """HomeKit thermostats on the home network that are ready to pair."""
    from aiohomekit.model.categories import Categories

    found: list[dict[str, Any]] = []
    async with _controller() as controller:
        # Answers to the network-wide question arrive over a few seconds.
        await asyncio.sleep(FIND_SECONDS)
        async for discovery in controller.async_discover(FIND_SECONDS):
            description = discovery.description
            if description.category != Categories.THERMOSTAT:
                continue
            found.append(
                {
                    "id": str(description.id),
                    "name": str(description.name).split("._hap")[0],
                    "model": str(getattr(description, "model", "") or ""),
                    "paired": bool(discovery.paired),
                }
            )
    return found


def _code(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != CODE_DIGITS:
        raise ThermostatError("The HomeKit code has 8 digits, like 123-45-678.")
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


async def pair(device_id: str, code: str, store: SecretStore | None = None) -> dict[str, Any]:
    """Pair with the thermostat using the code it shows, and keep the keys in the secret store."""
    from aiohomekit.exceptions import AccessoryNotFoundError, AuthenticationError, HomeKitException

    pin = _code(code)
    async with _lock, _controller() as controller:
        try:
            discovery = await controller.async_find(device_id, timeout=FIND_SECONDS * 2)
            if discovery.paired:
                raise ThermostatError(
                    "That thermostat is already paired with another home (Apple Home or another hub). Remove it "
                    "there, or reset HomeKit on the thermostat, then try again."
                )
            finish = await discovery.async_start_pairing(ALIAS)
            pairing = await finish(pin)
            name = await pairing.get_primary_name()
        except AccessoryNotFoundError:
            raise ThermostatError("The thermostat was not found on the home network. Check it is online.") from None
        except AuthenticationError:
            raise ThermostatError("The thermostat refused that code. Check the code it shows and try again.") from None
        except HomeKitException as exc:
            raise ThermostatError(f"Pairing did not finish: {exc}. Try again in a minute.") from None
        data = dict(pairing.pairing_data)
        await pairing.close()
    (store or SecretStore()).put(SCOPE, NAME, {"pairing": data, "name": name or "Thermostat", "paired_at": time.time()})
    _forget()
    return {"name": name or "Thermostat"}


async def unpair(store: SecretStore | None = None) -> None:
    """Remove Mu3Lab's pairing from the thermostat (when it answers) and forget the keys either way."""
    saved = paired(store)
    if saved:
        async with _lock:
            try:
                async with _controller() as controller:
                    pairing = controller.load_pairing(ALIAS, saved["pairing"])
                    await pairing.remove_pairing(saved["pairing"]["iOSPairingId"])
                    await pairing.close()
            except Exception:  # an unreachable thermostat must not keep its keys here
                pass
    (store or SecretStore()).delete(SCOPE, NAME)
    _forget()


def _forget() -> None:
    global _reading
    _reading = None


def _thermostat_service(accessories_data: list[dict[str, Any]]) -> Any:
    from aiohomekit.model import Accessories
    from aiohomekit.model.services import ServicesTypes

    for accessory in Accessories.from_list(accessories_data):  # type: ignore[arg-type]
        service = accessory.services.first(service_type=ServicesTypes.THERMOSTAT)
        if service is not None:
            return service
    raise ThermostatError("The paired device has no thermostat controls.")


def _value(service: Any, kind: str) -> Any:
    characteristic = service.characteristics_by_type.get(kind)
    return characteristic.value if characteristic is not None else None


def _limits(service: Any, kind: str) -> tuple[float, float, float] | None:
    characteristic = service.characteristics_by_type.get(kind)
    if characteristic is None:
        return None
    return (
        float(characteristic.minValue if characteristic.minValue is not None else 10),
        float(characteristic.maxValue if characteristic.maxValue is not None else 33),
        float(characteristic.minStep or 0.5),
    )


def _describe(service: Any, name: str) -> dict[str, Any]:
    from aiohomekit.model.characteristics import CharacteristicsTypes as C

    mode = _value(service, C.HEATING_COOLING_TARGET)
    state = _value(service, C.HEATING_COOLING_CURRENT)
    target_limits = _limits(service, C.TEMPERATURE_TARGET)
    return {
        "ok": True,
        "paired": True,
        "name": name,
        # HomeKit always speaks Celsius; the thermostat's own display unit is a separate setting.
        "units": "fahrenheit" if _value(service, C.TEMPERATURE_UNITS) == 1 else "celsius",
        "current": _value(service, C.TEMPERATURE_CURRENT),
        "humidity": _value(service, C.RELATIVE_HUMIDITY_CURRENT),
        "target": _value(service, C.TEMPERATURE_TARGET),
        "heat_to": _value(service, C.TEMPERATURE_HEATING_THRESHOLD),
        "cool_to": _value(service, C.TEMPERATURE_COOLING_THRESHOLD),
        "mode": MODES[mode] if isinstance(mode, int) and 0 <= mode < len(MODES) else "off",
        "state": STATES[state] if isinstance(state, int) and 0 <= state < len(STATES) else "idle",
        "modes": [MODES[value] for value in _valid(service, C.HEATING_COOLING_TARGET, range(len(MODES)))],
        "minimum": target_limits[0] if target_limits else 10.0,
        "maximum": target_limits[1] if target_limits else 33.0,
        "step": target_limits[2] if target_limits else 0.5,
    }


def _valid(service: Any, kind: str, every: range) -> list[int]:
    characteristic = service.characteristics_by_type.get(kind)
    allowed = getattr(characteristic, "valid_values", None) if characteristic is not None else None
    return [value for value in every if not allowed or value in allowed]


async def status(store: SecretStore | None = None) -> dict[str, Any]:
    """What the thermostat reads now, or that none is paired."""
    global _reading
    saved = paired(store)
    if not saved:
        return {"ok": True, "paired": False}
    if _reading and time.monotonic() - _reading[0] < READ_SECONDS:
        return _reading[1]
    async with _lock:
        if _reading and time.monotonic() - _reading[0] < READ_SECONDS:
            return _reading[1]
        reading = await _read(saved)
        _reading = (time.monotonic(), reading)
        return reading


async def _read(saved: dict[str, Any]) -> dict[str, Any]:
    try:
        async with _controller() as controller:
            pairing = controller.load_pairing(ALIAS, saved["pairing"])
            try:
                data = await asyncio.wait_for(pairing.list_accessories_and_characteristics(), timeout=15)
            finally:
                await pairing.close()
    except ThermostatError:
        raise
    except Exception:
        raise ThermostatError(
            "The thermostat did not answer. Check it is on the home Wi-Fi; if it was reset, pair it again in "
            "Settings > Home devices."
        ) from None
    return _describe(_thermostat_service(data), str(saved.get("name") or "Thermostat"))


async def change(
    *,
    mode: str | None = None,
    target: float | None = None,
    heat_to: float | None = None,
    cool_to: float | None = None,
    store: SecretStore | None = None,
) -> dict[str, Any]:
    """Set the mode and/or temperatures (Celsius), then read back what the thermostat now holds."""
    from aiohomekit.model.characteristics import CharacteristicsTypes as C

    saved = paired(store)
    if not saved:
        raise ThermostatError("No thermostat is paired yet. An administrator pairs it in Settings > Home devices.")
    if mode is not None and mode not in MODES:
        raise ThermostatError("Choose off, heat, cool or auto.")
    async with _lock:
        try:
            async with _controller() as controller:
                pairing = controller.load_pairing(ALIAS, saved["pairing"])
                try:
                    data = await asyncio.wait_for(pairing.list_accessories_and_characteristics(), timeout=15)
                    service = _thermostat_service(data)
                    writes: list[tuple[int, int, Any]] = []

                    def add(kind: str, value: Any) -> None:
                        characteristic = service.characteristics_by_type.get(kind)
                        if characteristic is None:
                            raise ThermostatError("This thermostat cannot change that.")
                        if isinstance(value, float):
                            low, high, step = _limits(service, kind) or (10.0, 33.0, 0.5)
                            value = round(min(max(value, low), high) / step) * step
                        writes.append((service.accessory.aid, characteristic.iid, value))

                    if mode is not None:
                        add(C.HEATING_COOLING_TARGET, MODES.index(mode))
                    if target is not None:
                        add(C.TEMPERATURE_TARGET, float(target))
                    if heat_to is not None:
                        add(C.TEMPERATURE_HEATING_THRESHOLD, float(heat_to))
                    if cool_to is not None:
                        add(C.TEMPERATURE_COOLING_THRESHOLD, float(cool_to))
                    if writes:
                        failed = await asyncio.wait_for(pairing.put_characteristics(writes), timeout=15)
                        if failed:
                            raise ThermostatError("The thermostat did not accept that change.")
                    data = await asyncio.wait_for(pairing.list_accessories_and_characteristics(), timeout=15)
                finally:
                    await pairing.close()
        except ThermostatError:
            raise
        except Exception:
            raise ThermostatError("The thermostat did not answer. Check it is on the home Wi-Fi.") from None
        global _reading
        reading = _describe(_thermostat_service(data), str(saved.get("name") or "Thermostat"))
        _reading = (time.monotonic(), reading)
        return reading
