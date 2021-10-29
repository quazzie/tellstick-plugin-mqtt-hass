from board import Board  # type: ignore
from utils import getIpAddr, getMacAddr, sensorScaleIntToStr, sensorTypeIntToStr, sensorTypeIntToDeviceClass, sensorTypeIntToStateClass, slugify
from telldus import Device, Thermostat  # type: ignore
import json
import logging
import psutil  # type: ignore

origin = 'HaClient'


class HaBaseDevice(object):
    def __init__(self, deviceId, deviceName, deviceType, buildTopic, viaDevice=None, category=None):
        self.deviceId = deviceId
        self.deviceName = deviceName
        self.deviceType = deviceType
        self.buildTopic = buildTopic
        self.viaDevice = viaDevice
        self.category = category

    def _deviceCommand(self, device, cmd, **kwargs):
        logging.info('DeviceCommand CMD: %s, ARGS: %s' % (cmd, kwargs))

        def cmdFail(reason, **__kwargs):
            logging.info('Command failed: %s' % reason)
        device.command(cmd, origin=origin, failure=cmdFail, **kwargs)

    def getID(self):
        return '%s' % self.deviceId

    def getName(self):
        return self.deviceName

    def getType(self):
        return self.deviceType

    def getDeviceTopic(self):
        return self.buildTopic(self.getType(), self.getID())

    def getState(self):
        return None

    def getConfig(self):
        conf = {
            'name': self.getName(),
            'unique_id': '%s_%s' % (getMacAddr(True), self.getID()),
            'state_topic': '%s/state' % self.getDeviceTopic(),
        }
        if hasattr(self, 'runCommand'):
            conf.update({'command_topic': '%s/set' % self.getDeviceTopic()})
        if self.viaDevice:
            conf.update({'device': self.viaDevice})
        if self.category:
            conf.update({'entity_category': self.category})
        return conf


class HaHub(HaBaseDevice):
    def __init__(self, deviceName, buildTopic, confUrl=None):
        super(HaHub, self).__init__('hub', deviceName, 'binary_sensor', buildTopic, None, 'diagnostic')
        self.confUrl = confUrl

    def getState(self):
        return 'online'

    def getConfig(self):
        conf = super(HaHub, self).getConfig()
        conf.update({
            'device_class': 'connectivity',
            'payload_on': 'online',
            'payload_off': 'offline',
            'device': {
                'identifiers': getMacAddr(True),
                #'connections': [['mac', getMacAddr(False)]],
                'manufacturer': 'Telldus Technologies',
                'model': Board.product().replace('-', ' ').title().replace(' ', '_'),
                'name': self.getName(),
                'sw_version': Board.firmwareVersion()
            }
        })
        if self.confUrl:
            conf.update({'configuration_url': self.confUrl})
        return conf

    def getWillState(self):
        return 'offline'


class HaHubDevice(HaBaseDevice):
    def __init__(self, hub, deviceId, deviceName, deviceType, buildTopic, viaDevice=None, category=None):
        super(HaHubDevice, self).__init__(
            deviceId, 
            deviceName, 
            deviceType, 
            buildTopic, 
            viaDevice or {'identifiers': hub.getConfig().get('device', {}).get('identifiers', '')}, 
            category
        )
        self.hub = hub

    def getConfig(self):
        conf = super(HaHubDevice, self).getConfig()
        conf.update({
            'availability_topic': '%s/state' % self.hub.getDeviceTopic()
        })
        return conf


class HaHubSensor(HaHubDevice):
    def __init__(self, hub, deviceId, deviceName, buildTopic, viaDevice=None, category=None, unit=None):
        super(HaHubSensor, self).__init__(hub, deviceId, deviceName, 'sensor', buildTopic, viaDevice=viaDevice, category=category)
        self.unit = unit

    def getConfig(self):
        conf = super(HaHubSensor, self).getConfig()
        conf.update({'unit_of_measurement': self.unit or ''})
        return conf


class HaHubConnectivitySensor(HaHubDevice):
    def __init__(self, hub, deviceId, deviceName, buildTopic, viaDevice=None, category=None):
        super(HaHubConnectivitySensor, self).__init__(hub, deviceId, deviceName,
                                                      'binary_sensor', buildTopic, viaDevice=viaDevice, category=category)

    def getConfig(self):
        conf = super(HaHubConnectivitySensor, self).getConfig()
        conf.update({
            'device_class': 'connectivity',
            'payload_on': 'online',
            'payload_off': 'offline'
        })
        return conf


class HaTimedSensor(HaBaseDevice):
    pass


class HaLiveConnection(HaHubConnectivitySensor, HaTimedSensor):
    def __init__(self, hub, live, buildTopic):
        super(HaLiveConnection, self).__init__(hub, 'live', 'Telldus live', buildTopic, None, 'diagnostic')
        self.live = live

    def getState(self):
        return 'online' if self.live.registered else 'offline'


class HaIpAddr(HaHubSensor, HaTimedSensor):
    def __init__(self, hub, buildTopic):
        super(HaIpAddr, self).__init__(hub, 'ipaddr', 'IP address', buildTopic, None, 'diagnostic', None)

    def getState(self):
        return getIpAddr()


class HaCpu(HaHubSensor, HaTimedSensor):
    def __init__(self, hub, buildTopic):
        super(HaCpu, self).__init__(hub, 'cpu', 'Cpu usage', buildTopic, None, 'diagnostic', '%')

    def getState(self):
        return psutil.cpu_percent(1)


class HaRamFree(HaHubSensor, HaTimedSensor):
    def __init__(self, hub, buildTopic):
        super(HaRamFree, self).__init__(hub, 'ram_free', 'Free ram', buildTopic, None, None, '%')

    def getState(self):
        return int(psutil.virtual_memory().available * 100 / psutil.virtual_memory().total)


class HaNetIOSent(HaHubSensor, HaTimedSensor):
    def __init__(self, hub, buildTopic):
        super(HaNetIOSent, self).__init__(hub, 'net_sent', 'Network sent bytes', buildTopic, None, None, 'Mb')

    def getState(self):
        return int(psutil.net_io_counters().bytes_sent / 1024)


class HaNetIORecv(HaHubSensor, HaTimedSensor):
    def __init__(self, hub, buildTopic):
        super(HaNetIORecv, self).__init__(hub, 'net_recv', 'Network recv bytes', buildTopic, None, None, 'Mb')

    def getState(self):
        return psutil.net_io_counters().bytes_recv / 1024


class HaDeviceSensor(HaHubSensor):
    def __init__(self, hub, device, sensorType, sensorScale, buildTopic, viaDevice=None, category=None, unit=None):
        super(HaDeviceSensor, self).__init__(
            hub,
            '%s_%s_%s' % (device.id(), sensorType, sensorScale),
            '%s %s' % (device.name(), sensorTypeIntToStr(sensorType, sensorScale)),
            buildTopic,
            viaDevice=viaDevice,
            category=category,
            unit=unit or sensorScaleIntToStr(sensorType, sensorScale)
        )
        self.device = device
        self.sensorType = sensorType
        self.sensorScale = sensorScale

    def getState(self):
        sensor = next((x for x in self.device.sensorValues()[self.sensorType] if x['scale'] == self.sensorScale), None)
        if sensor:
            return json.dumps({
                'value': sensor.get('value', None),
                'lastUpdated': sensor.get('lastUpdated', None)
            })

    def getConfig(self):
        conf = super(HaDeviceSensor, self).getConfig()
        conf.update({
            'state_class': sensorTypeIntToStateClass(self.sensorType, self.sensorScale),
            'value_template': '{{ value_json.value }}'
        })
        devClass = sensorTypeIntToDeviceClass(self.sensorType, self.sensorScale)
        if devClass:
            conf.update({'device_class': devClass})
        return conf


class HaDeviceBinary(HaHubDevice):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceBinary, self).__init__(hub, device.id(), device.name(),
                                             'binary_sensor', buildTopic, viaDevice=viaDevice, category=category)
        self.device = device

    def getState(self):
        state, stateValue = self.device.state()
        return 'ON' if state == Device.TURNON else 'OFF'


class HaDeviceSwitch(HaHubDevice):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceSwitch, self).__init__(hub, device.id(), device.name(),
                                             'switch', buildTopic, viaDevice=viaDevice, category=category)
        self.device = device

    def getState(self):
        state, stateValue = self.device.state()
        result = 'ON' if state in [Device.TURNON, Device.BELL] else 'OFF'
        if state == Device.BELL:
            return [result, 'OFF']
        return result

    def getConfig(self):
        conf = super(HaDeviceSwitch, self).getConfig()
        if self.device.methods() & Device.BELL:
            conf.update({ 'payload_on': 'BELL' })
        return conf

    def runCommand(self, topic, payload):
        self._deviceCommand(
            self.device, 
            Device.TURNON if payload.upper() == 'ON' \
            else Device.BELL if payload.upper() == 'BELL' \
            else Device.TURNOFF
        )


class HaDeviceLight(HaHubDevice):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceLight, self).__init__(hub, device.id(), device.name(),
                                            'light', buildTopic, viaDevice=viaDevice, category=category)
        self.device = device

    def getState(self):
        state, stateValue = self.device.state()
        if state == Device.DIM:
            return json.dumps({
                'state': 'ON' if stateValue and int(stateValue) > 0 else 'OFF',
                'brightness': int(stateValue) if stateValue else 0
            })
        else:
            return json.dumps({
                'state': 'ON' if state == Device.TURNON else 'OFF',
                'brightness': (int(stateValue) if stateValue else 255) if state == Device.TURNON else 0
            })

    def getConfig(self):
        conf = super(HaDeviceLight, self).getConfig()
        conf.update({
            'schema': 'json', 
            'brightness': True
        })
        return conf

    def runCommand(self, topic, payload):
        command = json.loads(payload)
        if 'brightness' in command:
            if int(command['brightness']) == 0:
                self._deviceCommand(self.device, Device.TURNOFF)
            else:
                self._deviceCommand(self.device, Device.DIM, value=int(command['brightness']))
        else:
            self._deviceCommand(
                self.device,
                Device.TURNON if command['state'].upper() == 'ON'
                else Device.TURNOFF,
                value=255
            )


class HaDeviceRemote(HaDeviceBinary):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceRemote, self).__init__(hub, device, buildTopic, viaDevice, category)

    def getConfig(self):
        conf = super(HaDeviceRemote, self).getConfig()
        conf.update({'expire_after': 1})
        return conf


class HaDeviceCover(HaHubDevice):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceCover, self).__init__(hub, device.id(), device.name(),
                                            'cover', buildTopic, viaDevice=viaDevice, category=category)
        self.device = device

    def getState(self):
        state, stateValue = self.device.state()
        if self.device.methods() & Device.DIM:
            return int(stateValue)
        else:
            return 'open' if state == Device.UP else \
                   'closed' if state == Device.DOWN else \
                   'stopped'

    def getConfig(self):
        conf = super(HaDeviceCover, self).getConfig()
        if self.device.methods() & Device.DIM:
            conf.update({
                'position_topic': '%s/state' % self.getDeviceTopic(),
                'set_position_topic': '%s/pos' % self.getDeviceTopic(),
                'position_open': 0,
                'position_closed': 255
            })
        return conf

    def runCommand(self, topic, payload):
        topicType = topic.split('/')[-1].upper()
        if topicType == 'POS':
            self._deviceCommand(self.device, Device.DIM, value=int(payload))
        elif topicType == 'SET':
            self._deviceCommand(
                self.device,
                Device.UP if payload.upper() == 'OPEN'
                else Device.DOWN if payload.upper() == 'CLOSE' else
                Device.STOP
            )


class HaDeviceClimate(HaHubDevice):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None):
        super(HaDeviceClimate, self).__init__(hub, device.id(), device.name(),
                                              'hvac', buildTopic, viaDevice=viaDevice, category=category)
        self.device = device

    def _getThermostat(self):
        params = self.device.allParameters() if hasattr(self.device, 'allParameters') else self.device.parameters()
        return params.get('thermostat', {})

    def _getModes(self):
        return self._getThermostat().get('modes', [])

    def _getSetPoints(self):
        return self._getThermostat().get('setpoints', {})

    def getState(self):
        temp = self.device.sensorValue(Device.TEMPERATURE, Device.SCALE_TEMPERATURE_CELCIUS) or \
            self.device.sensorValue(Device.TEMPERATURE, Device.SCALE_TEMPERATURE_FAHRENHEIT)
        thermoValues = self.device.stateValue(Device.THERMOSTAT, {})
        mode = thermoValues.get('mode', None)
        setPoint = thermoValues.get('setpoint', {})
        modeTemp = setPoint.get(mode, None)

        return json.dumps({
            'temperature': temp,
            'setpoint': modeTemp,
            'mode': mode
        })

    def getConfig(self):
        conf = super(HaDeviceClimate, self).getConfig()

        modes = self._getModes()
        if len(modes) > 0:
            conf.update({
                'modes': modes,
                'mode_state_topic': '%s/state' % self.getDeviceTopic(),
                'mode_state_template': '{{ value_json.mode }}',
                'mode_command_topic': '%s/setMode' % self.getDeviceTopic()
            })

        setPoints = self._getSetPoints()
        if len(setPoints) > 0:
            conf.update({
                'temperature_state_topic': '%s/state' % self.getDeviceTopic(),
                'temperature_state_template': '{{ value_json.setpoint }}',
                'temperature_command_topic': '%s/setPoint' % self.getDeviceTopic()
            })

        if self.device.isSensor():
            sensorValues = self.device.sensorValues().get(Device.TEMPERATURE, [])
            tempValue = next(sensorValues, None)
            if tempValue:
                conf.update({
                    'current_temperature_topic': '%s/state' % self.getDeviceTopic(),
                    'current_temperature_template': '{{ value_json.temperature }}',
                    'unit_of_measurement': sensorScaleIntToStr(Device.TEMPERATURE, tempValue.scale) or ''
                })

        return conf

    def runCommand(self, topic, payload):
        topicType = topic.split('/')[-1].upper()
        if topicType == 'SETMODE':
            value = {
                'mode': payload,
                'changeMode': True,
            }
            self._deviceCommand(self.device, Device.THERMOSTAT, value=value)
        elif topicType == 'SETPOINT':
            setpoint = float(payload) if payload else None
            value = {
                'changeMode': False,
                'temperature': setpoint
            }
            self._deviceCommand(self.device, Device.THERMOSTAT, value=value)


class HaDeviceBattery(HaHubSensor):
    def __init__(self, hub, device, buildTopic, viaDevice=None, category=None, unit=None):
        super(HaDeviceBattery, self).__init__(hub, '%s_battery' % device.id(),
                                              device.name(), buildTopic, viaDevice=viaDevice, category=category, unit=unit)
        self.device = device

    def getState(self):
        level = self.device.battery()
        return {Device.BATTERY_LOW: 1, Device.BATTERY_OK: 100, Device.BATTERY_UNKNOWN: None}.get(level, int(level))

    def getConfig(self):
        conf = super(HaDeviceBattery, self).getConfig()
        conf.update({
            'name': '%s battery' % conf.get('name'),
            'device_class': 'battery',
            'state_class': 'measurement'
        })
        return conf


def createDevices(device, hub, buildTopic, createSubDevices=False):
    caps = device.methods()
    devType = device.allParameters().get('devicetype')

    result = []

    subDevice = {
        'identifiers': device.getOrCreateUUID(),
        #'connections': [['mac', getMacAddr(False)]],
        'manufacturer': device.protocol().title(),
        'model': device.model().title(),
        'name': device.name(),
        'suggested_area': device.room() or '',
        'via_device': hub.getConfig().get('device', {}).get('identifiers', '')
    } if createSubDevices else None

    if device.battery() and device.battery() != Device.BATTERY_UNKNOWN:
        result.append(HaDeviceBattery(hub, device, buildTopic, subDevice))

    if device.isSensor():
        for type, sensors in device.sensorValues().items():
            for sensor in sensors:
                result.append(HaDeviceSensor(hub, device, type, sensor.get('scale', 0), buildTopic, subDevice))

    if device.isDevice():
        if devType == Device.TYPE_THERMOSTAT:
            result.append(HaDeviceClimate(hub, device, buildTopic, subDevice))
        elif devType == Device.TYPE_REMOTE_CONTROL:
            result.append(HaDeviceRemote(hub, device, buildTopic, subDevice))
        elif devType == Device.TYPE_WINDOW_COVERING or caps & Device.UP and caps & Device.DOWN:
            result.append(HaDeviceCover(hub, device, buildTopic, subDevice))
        elif devType == Device.TYPE_LIGHT or caps & Device.DIM:
            result.append(HaDeviceLight(hub, device, buildTopic, subDevice))
        elif caps & Device.BELL or caps & Device.TURNON:
            result.append(HaDeviceSwitch(hub, device, buildTopic, subDevice))
        else:
            result.append(HaDeviceBinary(hub, device, buildTopic, subDevice))

    return result
