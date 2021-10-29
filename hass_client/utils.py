from time import gmtime, strftime
import netifaces  # type: ignore
from board import Board  # type: ignore
from telldus import Device  # type: ignore


def getMacAddr(compact=True):
    addrs = netifaces.ifaddresses(Board.networkInterface())
    try:
        mac = addrs[netifaces.AF_LINK][0]['addr']
    except (IndexError, KeyError):
        return ''
    return mac.upper().replace(':', '') if compact else mac.upper()


def getIpAddr():
    iface = netifaces.ifaddresses(Board.networkInterface())
    try:
        inet = iface[netifaces.AF_INET][0]
    except:
        inet = {'addr': ''}
    return inet.get('addr', '')


def slugify(value):
    allowed_chars = set('_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
    return filter(lambda x: x in allowed_chars, value.replace(' ', '_').replace('-', '_'))


def sensorTypeIntToStateClass(sensorType, sensorScale):
    types = {
        Device.RAINTOTAL: {
            0: 'total_increasing'
        },
        Device.WATT: {
            Device.SCALE_POWER_KWH: 'total_increasing'
        }
    }
    return types.get(sensorType, {}).get(sensorScale, 'measurement')


def sensorTypeIntToDeviceClass(sensorType, scaleType):
    if sensorType == Device.WATT:
        return {
            Device.SCALE_POWER_KWH: 'energy',
            Device.SCALE_POWER_WATT: 'power',
            4: 'voltage',
            5: 'current',
            6: 'power_factor'
        }.get(scaleType, None)

    types = {
        Device.TEMPERATURE: 'temperature',
        Device.HUMIDITY: 'humidity',
        Device.LUMINANCE: 'illuminance',
        Device.BAROMETRIC_PRESSURE: 'pressure',
        Device.CO2: 'carbon_dioxide',
        Device.VOLUME: 'gas',
        Device.LOUDNESS: 'signal_strenth',
        Device.PM25: 'pm25',
        Device.CO: 'carbon_monoxide'
    }
    return types.get(sensorType, None)


def sensorTypeIntToStr(sensorType, sensorScale):
    # todo: add better names for type+scale combos, ex Device.WATT + Device.SCALE_KWH = energy
    types = {
        Device.TEMPERATURE: {None: 'temp'},
        Device.HUMIDITY: {None: 'humidity'},
        Device.RAINRATE: {None: 'rrate'},
        Device.RAINTOTAL: {None: 'rtot'},
        Device.WINDDIRECTION: {None: 'wdir'},
        Device.WINDAVERAGE: {None: 'wavg'},
        Device.WINDGUST: {None: 'wgust'},
        Device.UV: {None: 'uv'},
        Device.WATT: {
            1: 'apparent energy',
            Device.SCALE_POWER_KWH: 'energy',
            Device.SCALE_POWER_WATT: 'power',
            4: 'volt',
            5: 'current',
            6: 'power factor',
            None: 'unknown'
        },
        Device.LUMINANCE: {None: 'lum'},
        Device.DEW_POINT: {None: 'dewp'},
        Device.BAROMETRIC_PRESSURE: {None: 'barpress'},
        Device.GENERIC_METER: {None: 'genmeter'},
        Device.WEIGHT: {None: 'weight'},
        Device.CO2: {None: 'co2'},
        Device.VOLUME: {None: 'volume'},
        Device.LOUDNESS: {None: 'loudness'},
        Device.PM25: {None: 'pm25'},
        Device.CO: {None: 'co'},
        Device.MOISTURE: {None: 'moisture'}
    }
    t = types.get(sensorType, {})
    return t.get(sensorScale, None) or t.get(None, 'unknown')


def sensorScaleIntToStr(type, scale):
    scales = {
        Device.WATT: {
            1: 'kVAh',  # Device.SCALE_POWER_KVAH
            Device.SCALE_POWER_KWH: 'kWh',
            Device.SCALE_POWER_WATT: 'W',
            4: 'V',  # Device.SCALE_POWER_VOLT
            5: 'A',  # Device.SCALE_POWER_AMPERE
            6: 'PF'  # Device.SCALE_POWER_POWERFACTOR
        },
        Device.TEMPERATURE: {
            Device.SCALE_TEMPERATURE_CELCIUS: '°C',
            Device.SCALE_TEMPERATURE_FAHRENHEIT: '°F'
        },
        Device.HUMIDITY: {
            Device.SCALE_HUMIDITY_PERCENT: '%'
        },
        Device.RAINRATE: {
            Device.SCALE_RAINRATE_MMH: 'mm/h'
        },
        Device.RAINTOTAL: {
            Device.SCALE_RAINTOTAL_MM: 'mm'
        },
        Device.WINDDIRECTION: {
            0: ''
        },
        Device.WINDAVERAGE: {
            Device.SCALE_WIND_VELOCITY_MS: 'm/s'
        },
        Device.WINDGUST: {
            Device.SCALE_WIND_VELOCITY_MS: 'm/s'
        },
        Device.LUMINANCE: {
            Device.SCALE_LUMINANCE_PERCENT: '%',
            Device.SCALE_LUMINANCE_LUX: 'lux'
        },
        Device.BAROMETRIC_PRESSURE: {
            Device.SCALE_BAROMETRIC_PRESSURE_KPA: 'kPa'
        }
    }
    return scales.get(type, {}).get(scale, '')
