from board import Board
from utils import getMacAddr, sensorScaleIntToStr, sensorTypeIntToStr, sensorTypeIntToDeviceClass, sensorTypeIntToStateClass, slugify
from telldus import Device, Thermostat
import json
import logging

origin = 'HaClient'


def getDevice(deviceName):
    return {
        'identifiers': getMacAddr(True),
        'connections': [['mac', getMacAddr(False)]],
        'manufacturer': 'Telldus Technologies',
        'model': Board.product().replace('-', ' ').title().replace(' ', '_'),
        'name': deviceName,
        'sw_version': Board.firmwareVersion()
    }


def getViaDevice(device):
    return {
        'identifiers': device.getOrCreateUUID(),
        'connections': [['mac', getMacAddr(False)]],
        'manufacturer': device.protocol().title(),
        'model': device.model().title(),
        'name': device.name(),
        'suggested_area': device.room(),
        'via_device': getMacAddr(True)
    }


class HaHub:
    def __init__(self, deviceName, buildTopic):
        self.deviceName = deviceName
        self.deviceId = None
        self.device = None
        self.buildTopic = buildTopic

    def getID(self):
        return "hub"

    def getType(self):
        return 'binary_sensor'

    def getDeviceTopic(self):
        return self.buildTopic(self.getType(), self.getID())

    def getState(self):
        return 'online'

    def getConfig(self, hub, useVia):
        return {
            'name': self.deviceName,
            'state_topic': '%s/state' % self.getDeviceTopic(),
            'payload_on': 'online',
            'payload_off': 'offline',
            'device_class': 'connectivity',
            'unique_id': getMacAddr(True),
            'device': getDevice(self.deviceName)
        }

    def getWillState(self):
        return "offline"


class HaDevice:
    def __init__(self, device, buildTopic):
        self.deviceId = device.id()
        self.device = device
        self.buildTopic = buildTopic

    def getID(self):
        return '%s' % self.device.id()

    def getType(self):
        return 'binary_sensor'

    def getDeviceTopic(self):
        return self.buildTopic(self.getType(), self.getID())

    def getState(self):
        state, stateValue = self.device.state()
        logging.info('Device (%s) state : %s %s' % (self.getID(), state, stateValue))
        return 'ON' if state == Device.TURNON else 'OFF'

    def getConfig(self, hub, useVia):
        conf = {
            'name': self.device.name(),
            'unique_id': '%s_%s' % (getMacAddr(True), self.getID()),
            'state_topic': '%s/state' % self.getDeviceTopic(),
            'availability_topic': '%s/state' % hub.getDeviceTopic(),
            'device': getViaDevice(self.device) if useVia else {'identifiers': getMacAddr(True)}
        }

        if hasattr(self, 'runCommand'):
            conf.update({'command_topic': '%s/set' % self.getDeviceTopic()})

        return conf

    def _runCommand(self, topic, command):
        logging.info('runCommand %s %s %s %s', self.getType(), self.getID(), topic, command)

    def _deviceCommand(self, cmd, **kwargs):
        logging.info('DeviceCommand CMD: %s, ARGS: %s' % (cmd, kwargs))

        def cmdFail(reason, **__kwargs):
            logging.info('Command failed: %s' % reason)
        self.device.command(cmd, origin=origin, failure=cmdFail, **kwargs)


class HaRemote(HaDevice):
    def getType(self):
        return 'binary_sensor'

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia).copy()
        sConf.update({'expire_after': 1})
        return sConf


class HaCover(HaDevice):
    def getType(self):
        return 'cover'

    def getState(self):
        state, stateValue = self.device.state()
        logging.info('Cover (%s) state : %s %s' % (self.getID(), state, stateValue))
        if self.device.methods() & Device.DIM:
            return int(stateValue)
        else:
            return 'open' if state == Device.UP else \
                'closed' if state == Device.DOWN else \
                'stopped'

    # def getConfig(self, hub, useVia):
    #    sConf = HaDevice.getConfig(self, hub, useVia).copy()
    #    sConf.update({
    #        'position_topic': '%s/state' % self.getDeviceTopic(),
    #        'set_position_topic': '%s/set' % self.getDeviceTopic(),
    #        'position_open': 0,
    #        'position_closed': 255
    #    })
    #    return sConf

    def runCommand(self, topic, command):
        HaDevice._runCommand(self, topic, command)
        topicType = topic.split('/')[-1].upper()
        if topicType == 'POS':
            self._deviceCommand(Device.DIM, int(command))
        elif topicType == 'SET':
            self._deviceCommand(
                Device.UP if command.upper() == 'OPEN'
                else Device.DOWN if command.upper() == 'CLOSE' else
                Device.STOP
            )


class HaLight(HaDevice):
    def getType(self):
        return 'light'

    def getState(self):
        state, stateValue = self.device.state()
        logging.info('Light (%s) state : %s %s' % (self.getID(), state, stateValue))
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

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia).copy()
        sConf.update({'schema': 'json', 'brightness': True})
        return sConf

    def runCommand(self, topic, command):
        HaDevice._runCommand(self, topic, command)
        payload = json.loads(command)
        if 'brightness' in payload:
            if int(payload['brightness']) == 0:
                self._deviceCommand(Device.TURNOFF)
            else:
                self._deviceCommand(Device.DIM, value=int(payload['brightness']))
        else:
            self._deviceCommand(
                Device.TURNON if payload['state'].upper() == 'ON'
                else Device.TURNOFF,
                value=255
            )


class HaSwitch(HaDevice):
    def getType(self):
        return 'switch'

    def getState(self):
        state, stateValue = self.device.state()
        logging.info('Switch (%s) state : %s %s' % (self.getID(), state, stateValue))
        result = 'ON' if state in [Device.TURNON, Device.BELL] else 'OFF'
        if state == Device.BELL:
            return [result, 'OFF']
        return result

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia).copy()
        if self.device.methods() & Device.BELL:
            sConf.update({'payload_on': 'BELL'})
        return sConf

    def runCommand(self, topic, command):
        HaDevice._runCommand(self, topic, command)
        self._deviceCommand(
            Device.TURNON if command.upper() == 'ON'
            else Device.BELL if command.upper() == 'BELL'
            else Device.TURNOFF
        )


class HaClimate(HaDevice):
    def __init__(self, device, buildTopic, sensors):
        HaDevice.__init__(self, device, buildTopic)
        self.sensors = sensors

    def getType(self):
        return 'hvac'

    def getClimateModes(self):
        params = self.device.allParameters() if hasattr(self.device, 'allParameters') else self.device.parameters()
        modes = params.get('thermostat', {}).get('modes', ['auto'])
        return modes

    def getClimateMode(self):
        #state, stateValue = device.state()
        thermoValues = self.device.stateValue(Device.THERMOSTAT)
        availModes = self.getClimateModes()
        return thermoValues.get('mode') or availModes[0]

    def getClimateSetPoint(self, mode=None):
        thermoValues = self.device.stateValue(Device.THERMOSTAT)
        setpoint = thermoValues.get('setpoint')
        if isinstance(setpoint, dict) and mode:
            setpoint = setpoint.get(mode)
        return setpoint

    def getState(self):
        #thermoValues = self.device.stateValue(Device.THERMOSTAT)
        sensorValues = self.device.sensorValues()
        tempValues = sensorValues[Device.TEMPERATURE]
        mode = self.getClimateMode()
        setpoint = self.getClimateSetPoint(mode)

        result = {
            'setpoint': setpoint,
            'mode': {Thermostat.MODE_FAN: 'fan_only'}.get(mode, mode),
        }

        if self.device.isSensor() and sensorValues[Device.TEMPERATURE]:
            value = tempValues[0] if isinstance(tempValues, list) else tempValues
            result.update({'temperature': value.get('value')})

        return json.dumps(result)

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia)
        sConf.update({
            'temperature_command_topic': '%s/set/setpoint' % self.getDeviceTopic(),
            'json_attributes_topic': '%s/attr' % self.getDeviceTopic(),
            'json_attributes_template': '{{ json_value }}'
        })

        sensorValues = self.device.sensorValues()
        thermoValues = self.device.stateValue(Device.THERMOSTAT)
        params = self.device.allParameters() if hasattr(self.device, 'allParameters') else self.device.parameters()
        modes = params.get('thermostat', {}).get('modes', ['auto'])

        if self.device.isSensor() and sensorValues[Device.TEMPERATURE]:
            existingSensor = next((x for x in self.sensors if x.device.id() ==
                                  self.device.id() and x.sensorType == Device.TEMPERATURE), None)
            sConf.update({
                'current_temperature_topic': '%s/state' % ((existingSensor or self).getDeviceTopic()),
                'current_temperature_template': '{{ value_json.temperature }}',
                'unit_of_measurement': sensorScaleIntToStr(Device.TEMPERATURE, sensorValues[Device.TEMPERATURE]['scale'])
            })

        if modes:
            sConf.update({
                'modes': modes,
                'mode_command_topic': '%s/set/mode' % self.getDeviceTopic(),
                'mode_state_topic': '%s/state' % self.getDeviceTopic(),
                'mode_state_template': '{{ value_json.mode }}'
            })

        if thermoValues.get('setpoint', None) is not None:
            sConf.update({
                'temperature_state_topic': '%s/state' % self.getDeviceTopic(),
                'temperature_state_template': '{{ value_json.setpoint }}'
            })

        return sConf

    def runCommand(self, topic, command):
        HaDevice._runCommand(self, topic, command)
        topicType = topic.split('/')[-1].upper()
        payload = json.loads(command)
        if topicType == 'MODE':
            mode = {'fan_only': Thermostat.MODE_FAN}.get(payload, payload)
            setpoint = self.getClimateSetPoint(mode)
            if setpoint:
                value = {
                    'mode': mode,
                    'changeMode': True,
                    'temperature': self.getClimateSetPoint(mode)
                }
                self._deviceCommand(Device.THERMOSTAT, value=value)
            else:
                logging.info('Can not set mode, !setpoint')
        elif topicType == 'SETPOINT':
            setpoint = float(payload) if payload else None
            if setpoint:
                value = {
                    'mode': self.getClimateMode(),
                    'changeMode': False,
                    'temperature': setpoint
                }
                self._deviceCommand(Device.THERMOSTAT, value=value)
            else:
                logging.info('Can not update setpoint, !setpoint (%s)' % payload)


class HaBattery(HaDevice):
    def getID(self):
        sID = HaDevice.getID(self)
        return '%s_battery' % sID

    def getType(self):
        return 'sensor'

    def getState(self):
        level = self.device.battery()
        return {
            Device.BATTERY_LOW: 1,
            Device.BATTERY_UNKNOWN: None,
            Device.BATTERY_OK: 100
        }.get(level, int(level))

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia).copy()
        sConf.update({
            'name': '%s battery' % sConf.get('name'),
            'device_class': 'battery',
            'state_class': 'measurement'
        })
        return sConf


class HaSensor(HaDevice):
    def __init__(self, device, buildTopic, sensorType, sensorScale):
        HaDevice.__init__(self, device, buildTopic)
        self.sensorType = sensorType
        self.sensorScale = sensorScale

    def getID(self):
        sID = HaDevice.getID(self)
        return '%s_%s_%s' % (sID, self.sensorType, self.sensorScale)

    def getType(self):
        return 'sensor'

    def getState(self):
        sensor = next((x for x in self.device.sensorValues()[
                      self.sensorType] if x['scale'] == self.sensorScale), None)
        if sensor:
            return json.dumps({
                'value': sensor.get('value', None),
                'lastUpdated': sensor.get('lastUpdated', None)
            })

        return None

    def getConfig(self, hub, useVia):
        sConf = HaDevice.getConfig(self, hub, useVia).copy()
        sConf.update({
            'name': '%s %s' % (sConf.get('name'), sensorTypeIntToStr(self.sensorType, self.sensorScale)),
            'state_class': sensorTypeIntToStateClass(self.sensorType, self.sensorScale),
            'value_template': '{{ value_json.value }}',
            'unit_of_measurement': sensorScaleIntToStr(self.sensorType, self.sensorScale)
        })
        devClass = sensorTypeIntToDeviceClass(self.sensorType, self.sensorScale)
        if devClass:
            sConf.update({ 'device_class': devClass })
        return sConf


def createDevices(device, buildTopic):
    caps = device.methods()
    devType = device.allParameters().get('devicetype')

    result = []

    if device.battery() and device.battery() != Device.BATTERY_UNKNOWN:
        result.append(HaBattery(device, buildTopic))

    if device.isSensor():
        for type, sensors in device.sensorValues().items():
            for sensor in sensors:
                result.append(HaSensor(device, buildTopic, type, sensor['scale']))

    if device.isDevice():
        if devType == Device.TYPE_THERMOSTAT:
            result.append(HaClimate(device, buildTopic, [x for x in result if isinstance(x, HaSensor)]))
        elif devType == Device.TYPE_REMOTE_CONTROL:
            result.append(HaRemote(device, buildTopic))
        elif caps & Device.UP and caps & Device.DOWN:
            result.append(HaCover(device, buildTopic))
        elif devType == Device.TYPE_LIGHT or caps & Device.DIM:
            result.append(HaLight(device, buildTopic))
        elif caps & Device.BELL or caps & Device.TURNON:
            result.append(HaSwitch(device, buildTopic))
        else:
            result.append(HaDevice(device, buildTopic))

    return result
