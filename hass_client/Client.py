# -*- coding: utf-8 -*-
<<<<<<< HEAD

import json
import threading
from time import gmtime, strftime

# pylint: disable=no-name-in-module,import-error
from base import \
  Application, \
  Plugin, \
  Settings, \
  configuration, \
  ConfigurationManager, \
  ConfigurationNumber, \
  ConfigurationString, \
  implements, \
  ISignalObserver, \
  slot
import paho.mqtt.client as mqtt
import logging
from board import Board
from telldus import DeviceManager, Device, Thermostat
import netifaces

#try:
#	from zwave.telldus import TelldusZWave
#	from zwave.base import CommandClass, ZWSendDataMsg
#except ImportError:
#	TelldusZWave = None
# pylint: enable=no-name-in-module,import-error

__name__ = 'HASSMQTT'  # pylint: disable=W0622

ScaleConverter = {
  Device.WATT: {
    1: 'kVAh', #Device.SCALE_POWER_KVAH
    Device.SCALE_POWER_KWH: 'kWh',
    Device.SCALE_POWER_WATT: 'W',
    4: 'V', #Device.SCALE_POWER_VOLT
    5: 'A', #Device.SCALE_POWER_AMPERE
    6: 'PF' #Device.SCALE_POWER_POWERFACTOR
  },
  Device.TEMPERATURE: {
    Device.SCALE_TEMPERATURE_CELCIUS: u'°C',
    Device.SCALE_TEMPERATURE_FAHRENHEIT: u'°F'
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

ClassConverter = {
  Device.TEMPERATURE: 'temperature',
  Device.HUMIDITY: 'humidity',
  Device.BAROMETRIC_PRESSURE: 'pressure',
  Device.LUMINANCE: 'illuminance'
}

def getMacAddr(compact = True):
  addrs = netifaces.ifaddresses(Board.networkInterface())
  try:
    mac = addrs[netifaces.AF_LINK][0]['addr']
  except (IndexError, KeyError):
    return ''
  return mac.upper().replace(':', '') if compact else mac.upper()

def slugify(value):
  allowed_chars = set('_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
  return filter(lambda x: x in allowed_chars, value.replace(' ', '_').replace('-', '_'))

@configuration(
  username = ConfigurationString(
    defaultValue='',
    title='MQTT Username',
    description='Username'
  ),
  password=ConfigurationString(
    defaultValue='',
    title='MQTT Password',
    description='Password'
  ),
  hostname=ConfigurationString(
    defaultValue='',
    title='MQTT Hostname',
    description='Hostname'
  ),
  port=ConfigurationNumber(
    defaultValue=1883,
    title='MQTT Port',
    description='Port'
  ),
  discovery_topic=ConfigurationString(
    defaultValue='homeassistant',
    title='Autodiscovery topic',
    description='Homeassistants autodiscovery topic'
  ),
  device_name=ConfigurationString(
    defaultValue='znet',
    title='Device name',
    description='Name of this device'
  ),
  base_topic=ConfigurationString(
    defaultValue='telldus',
    title='Base topic',
    description='Base topic for this device'
  ),
  state_retain=ConfigurationNumber(
    defaultValue=1,
    title='Retain state changes, 1 = yes, 0 = no',
    description='Post state changes with retain'
  ),
  devices_configured=ConfigurationString(
    defaultValue='',
    hidden=True,
    title='Internal, do not change',
    description='Internal, do not change. Used to store what devices has been published.'
  )
)
class Client(Plugin):
  implements(ISignalObserver)

  def __init__(self):
    self._ready = False
    self._running = True
    self._knownDevices = None
    Application().registerShutdown(self.onShutdown)
    self.client = mqtt.Client(userdata = self)
    self.client.on_disconnect = self.onDisconnect
    self.client.on_connect = self.onConnect
    self.client.on_message = self.onMessage
    if self.config('hostname'):
      Application().queue(self.connect)

  def getSlugifiedConfig(self, name):
    return slugify(self.config(name))

  def getBaseTopic(self):
    return self.getSlugifiedConfig('base_topic')

  def getDeviceName(self):
    return self.getSlugifiedConfig('device_name')

  def onShutdown(self):
    self._running = False 
    self.disconnect()

  def updateConfig(self):
    self.debug('Updating config.devices_configured to : %s' % self._knownDevices)
    try:
      self.setConfig('devices_configured', self._knownDevices)
    except Exception as e:
      self.debug('updateConfig error %s' % str(e))

  def getKnownDevices(self):
    if not self._knownDevices:
      if self.config('devices_configured'):
        self._knownDevices = [tuple(x) for x in json.loads(self.config('devices_configured'))]
      else:
        self._knownDevices = []
    return self._knownDevices
  
  def setKnownDevices(self, devices):
    self._knownDevices = devices
    self.updateConfig()

  def isKnownDevice(self, type, devId, deviceId):
    devices = self.getKnownDevices()
    return (type, str(devId), str(deviceId)) in devices

  def addKnownDevice(self, type, devId, deviceId):
    devices = self.getKnownDevices()
    devices.append((type, str(devId), str(deviceId)))
    self.setKnownDevices(devices)
  
  def delKnownDevice(self, type, devId, deviceId):
    devices = self.getKnownDevices()
    devices.remove((type, devId, deviceId))
    self.setKnownDevices(devices)

  def configWasUpdated(self, key, value):
    self.debug('config updated %s %s' % (key, value))
    if key == 'state_retain' and value == '0':
      # clear retained messages
      try:
        for type, _, fullId in self.getKnownDevices():
          deviceTopic = self.getDeviceTopic(type, fullId)
          self.debug('clear retain for %s/state' % deviceTopic)
          self.client.publish('%s/state' % deviceTopic, None, retain = True)
      except Exception as e:
        self.debug('clear retain error %s' % str(e))
    if not key in ['devices_configured', 'state_retain']:
      self.disconnect()
      Application().queue(self.connect)

  def tearDown(self):
    try:
      for type, _, fullId in self.getKnownDevices():
        deviceTopic = self.getDeviceTopic(type, fullId)
        self.client.publish('%s/config' % deviceTopic, '', retain = True)
        self.client.publish('%s/state' % deviceTopic, '', retain = True)
    except Exception as e:
      self.debug('tearDown %s' % str(e))
    self._knownDevices = []
    self.updateConfig()
    self.disconnect()

  def disconnect(self):
    #self.client.disconnect()
    self.client.loop_stop()
    self._running = False
    self._ready = False

  def connect(self):
    username = self.config('username')
    password = self.config('password')
    base_topic = self.getBaseTopic()
    device_name = self.getDeviceName()
    hostname = self.config('hostname')
    port = self.config('port')

    if username != '':
      self.client.username_pw_set(username, password)
    self.client.will_set(
      '%s/%s/available' % (base_topic, device_name) if base_topic \
      else '%s/available' % device_name, 
      'offline', 
      0, 
      True
    )
    self.client.connect_async(hostname, port, keepalive=10)
    self.client.loop_start()

  def debug(self, msg):
    logging.info('HASS DBG: %s', msg)
    base_topic = self.getBaseTopic()
    device_name = self.getDeviceName()
    debugTopic = (
      '%s/%s/debug' % (base_topic, device_name) if base_topic \
      else '%s/debug' % device_name
    )
    time = strftime('%H:%M:%S', gmtime())
    self.client.publish(debugTopic, '%s - %s' % (time, msg))

  def getDeviceType(self, device):
    capabilities = device.methods()
    devicetype = device.allParameters().get('devicetype')
    if devicetype == Device.TYPE_THERMOSTAT:
      return 'climate'
    elif devicetype == Device.TYPE_REMOTE_CONTROL:
      return 'remote'
    elif capabilities & Device.DIM:
      return 'light'
    elif capabilities & Device.TURNON:
      return 'switch'
    elif capabilities & Device.UP:
      return 'cover'
    elif capabilities & Device.BELL:
      return 'switch'
    else:
      return 'binary_sensor'

  def getDeviceTopic(self, type, id):
    discoverTopic = self.getSlugifiedConfig('discovery_topic')
    telldusName = self.getDeviceName() or 'telldus'
    if type in ['remote']:
      type = 'binary_sensor'
    return '%s/%s/%s/%s' % (discoverTopic, type, telldusName, id)

  def getSensorId(self, deviceId, valueType, scale):
    return slugify('%s_%s_%s' % (deviceId, valueType, scale))

  def getBatteryId(self, device):
    return slugify('%s_%s_battery' % (getMacAddr(), device.id()))

  def formatBattery(self, battery):
    return {
      Device.BATTERY_LOW: 1,
      Device.BATTERY_UNKNOWN: None,
      Device.BATTERY_OK: 100
    }.get(battery, int(battery))

  def formatScale(self, type, scale):
    return ScaleConverter.get(type, {}).get(scale, '')

  def getClimateModes(self, device):
    params = device.allParameters() if hasattr(device, 'allParameters') else device.parameters()
    modes = params.get('thermostat', {}).get('modes', ['auto'])
    return modes

  def getClimateMode(self, device):
    state, stateValue = device.state()
    thermoValues = device.stateValue(Device.THERMOSTAT)
    availModes = self.getClimateModes(device)
    return thermoValues.get('mode') or availModes[0]

  def getClimateSetPoint(self, device, mode = None):
    thermoValues = device.stateValue(Device.THERMOSTAT)
    setpoint = thermoValues.get('setpoint')
    if isinstance(setpoint, dict) and mode:
        setpoint = setpoint.get(mode)
    return setpoint

  def climateState(self, device):
    try:
      if self.getDeviceType(device) != "climate":
        return

      thermoValues = device.stateValue(Device.THERMOSTAT)
      sensorValues = device.sensorValues()
      tempValues = sensorValues[Device.TEMPERATURE]
      mode = self.getClimateMode(device)
      setpoint = self.getClimateSetPoint(device, mode)

      self.debug(
        'climateState %s, sensorValues: %s, thermovalues: %s, setPoint: %s, mode: %s' % \
        (device.id(), sensorValues, thermoValues, setpoint, mode)
      )

      payload = {
        'setpoint': setpoint,
        'mode': { Thermostat.MODE_FAN: 'fan_only' }.get(mode, mode),
      }

      if device.isSensor() and sensorValues[Device.TEMPERATURE]:
        value = tempValues[0] if isinstance(tempValues, list) else tempValues
        payload.update({
          'temperature': value.get('value'),
        })

      stateTopic = '%s/state' % self.getDeviceTopic("climate", device.id())
      self.client.publish(stateTopic, json.dumps(payload), retain = True)
    except Exception as e:
      self.debug('climateState exception %s' % str(e))

  def deviceState(self, device):
    try:
      state, stateValue = device.state()

      deviceType = self.getDeviceType(device)
      if not deviceType:
        return

      self.debug('deviceState %s, state: %s, value: %s' % (device.id(), state, stateValue))

      stateTopic = '%s/state' % self.getDeviceTopic(deviceType, device.id())
      payload = ''

      retain = True
      if deviceType in ['light']:
        if state == Device.DIM:
          payload = json.dumps({
            'state': 'ON' if stateValue and int(stateValue) > 0 else 'OFF',
            'brightness': int(stateValue) if stateValue else 0
          })
        else:
          payload = json.dumps({
            'state': 'ON' if state == Device.TURNON else 'OFF',
            'brightness': (int(stateValue) if stateValue else 255) if state == Device.TURNON else 0
          })
      elif deviceType in ['remote']:
        payload = 'ON' if state in [Device.TURNON] else 'OFF'
        retain = False
      elif deviceType in ['switch']:
        payload = 'ON' if state in [Device.TURNON, Device.BELL] else 'OFF' 
      elif deviceType in ['binary_sensor']:
        payload = 'ON' if state in [Device.TURNON] else 'OFF' 
      elif deviceType in ['cover']:
        payload = 'OPEN' if state == Device.UP else 'CLOSED' if state == Device.DOWN else 'STOP'

      use_retain = retain and (self.config('state_retain') == 1)
      self.client.publish(stateTopic, payload, retain = use_retain)
      if state == Device.BELL:
        self.client.publish(stateTopic, 'OFF', retain = use_retain)
    except Exception as e:
      self.debug('deviceState exception %s' % str(e))

  def sensorState(self, device, valueType, scale):
    try:
      sensorId = self.getSensorId(device.id(), valueType, scale)
      for sensor in device.sensorValues()[valueType]:
        if sensor['scale'] == scale:
          self.debug('sensorState %s' % sensor)
          payload = { 
            'value': sensor['value'],
            'lastUpdated': sensor.get('lastUpdated')
          }
          self.client.publish(
            '%s/state' % self.getDeviceTopic('sensor', sensorId),
            json.dumps(payload),
            retain = True
          )
    except Exception as e:
      self.debug('sensorState exception %s' % str(e))

  def batteryState(self, device):
    try:
      self.client.publish(
        '%s/state' % self.getDeviceTopic('sensor', self.getBatteryId(device)),
        self.formatBattery(device.battery()),
        retain = True
      )
    except Exception as e:
      self.debug('batteryState exception %s' % str(e))

  def publish_discovery(self, device, type, deviceId, config):
    base_topic = self.getBaseTopic()
    device_name = self.getDeviceName()
    config.update({
      'unique_id': '%s_%s' % (getMacAddr(), deviceId),
      'availability_topic': (
        '%s/%s/available' % (base_topic, device_name) if base_topic \
        else '%s/available' % device_name
      ),
      'device': {
        'identifiers': device.getOrCreateUUID(),
        'manufacturer': device.protocol().title(),
        'model': device.model().title(),  # Model is always 'n/a' but is supposed to be updated.
        'name': device.name(),
        'via_device': getMacAddr(),
      }
    })
    self.client.publish(
      '%s/config' % self.getDeviceTopic(type, deviceId), 
      json.dumps(config),
      retain = True
    )
    return (type, str(device.id()), str(deviceId))

  def remove_discovery(self, type, devId, fullId):
    deviceTopic = self.getDeviceTopic(type, fullId)
    self.debug('remove discovered device %s,%s,%s : %s' % (type, devId, fullId, deviceTopic))
    self.client.publish('%s/config' % deviceTopic, '', retain = True)
    self.client.publish('%s/state' % deviceTopic, '', retain = True)

  def discoverClimate(self, device):
    deviceTopic = self.getDeviceTopic('climate', device.id())
    try:
      sensorValues = device.sensorValues()
      thermoValues = device.stateValue(Device.THERMOSTAT)
      availModes = self.getClimateModes(device)

      climateConfig = {
        'name': device.name(),
        'temperature_command_topic': '%s/set/setpoint' % deviceTopic,
        'json_attributes_topic': '%s/attr' % deviceTopic,
        'json_attributes_template': '{{ json_value }}',
      }

      if device.isSensor() and sensorValues[Device.TEMPERATURE]:
        climateConfig.update({
          'current_temperature_topic': '%s/state' % deviceTopic,
          'current_temperature_template': '{{ value_json.temperature }}',
          # Only after https://github.com/home-assistant/home-assistant/pull/30602
          #'unit_of_measurement': ScaleConverter.get(Device.TEMPERATURE).get()
        })

      if availModes:
        climateConfig.update({
          'modes': availModes,
          'mode_command_topic': '%s/set/mode' % deviceTopic,
          'mode_state_topic': '%s/state' % deviceTopic,
          'mode_state_template': '{{ value_json.mode }}',
        })

      if thermoValues.get('setpoint', None) is not None:
        climateConfig.update({
          'temperature_state_topic': '%s/state' % deviceTopic,
          'temperature_state_template': '{{ value_json.setpoint }}',
        })

      self.client.publish(
        '%s/attr' % deviceTopic,
        json.dumps({ 'modes': availModes }),
        retain = True
      )

      return self.publish_discovery(device, 'climate', device.id(), climateConfig)
    except Exception as e:
      self.debug('discoverThermostat %s' % str(e))

  def discoverBattery(self, device):
    deviceTopic = self.getDeviceTopic('sensor', self.getBatteryId(device))
    try:
      sensorConfig = {
        'name': '%s - Battery' % device.name(),
        'unit_of_measurement': '%',
        'device_class': 'battery',
        'state_topic': '%s/state' % deviceTopic
      }
      return self.publish_discovery(device, 'sensor', self.getBatteryId(device), sensorConfig)
    except Exception as e:
      self.debug('discoverBattery %s' % str(e))

  def discoverSensor(self, device, valueType, scale):
    sensorId = self.getSensorId(device.id(), valueType, scale)
    deviceTopic = self.getDeviceTopic("sensor", sensorId)
    try:
      sensorConfig = {
        'name': '%s %s - %s' % (
          device.name(), 
          Device.sensorTypeIntToStr(valueType), 
          self.formatScale(valueType, scale)
        ),
        'state_topic': '%s/state' % deviceTopic,
        'value_template': '{{ value_json.value }}',
        'json_attributes_topic': '%s/state' % deviceTopic,
        'unit_of_measurement': self.formatScale(valueType, scale),
      }
      if ClassConverter.get(valueType, None):
        sensorConfig.update({
          'device_class': ClassConverter.get(valueType, None)
        })

      sensorId = self.getSensorId(device.id(), valueType, scale)
      return self.publish_discovery(device, 'sensor', sensorId, sensorConfig)
    except Exception as e:
      self.debug('discoverSensor %s' % str(e))

  def discoverDevice(self, device):
    try:
      deviceType = self.getDeviceType(device)
      if not deviceType:
        return None	

      deviceTopic = self.getDeviceTopic(deviceType, device.id())
      deviceConfig = { 
        'name': device.name(),
        'state_topic': '%s/state' % deviceTopic
      }

      if deviceType in ['remote']:
        deviceConfig.update({
          'expire_after': 1
        })
      if deviceType in ['switch', 'light', 'cover']:
        deviceConfig.update({
          'command_topic': '%s/set' % deviceTopic
        })
      if deviceType == 'light':
        deviceConfig.update({
          'schema': 'json',
          'brightness': True
        })
      if deviceType == 'switch' and (device.methods() & Device.BELL):
        deviceConfig.update({
          'payload_on': 'BELL'
        })

      self.debug('device is device: %s' % json.dumps({
        'deviceType': deviceType,
        'deviceTopic': deviceTopic,
        'deviceConfig': deviceConfig
      }))

      return self.publish_discovery(device, deviceType, device.id(), deviceConfig)
    except Exception as e:
      self.debug('discoverDevice %s' % str(e))

  def discovery(self, device):
    result = []
    try:
      if device.battery() and device.battery() != Device.BATTERY_UNKNOWN:
        self.debug('device %s has battery' % device.id())
        result.append(self.discoverBattery(device))
        self.batteryState(device)

      if device.deviceType() == Device.TYPE_THERMOSTAT:
        self.debug('device %s is climate' % device.id())
        result.append(self.discoverClimate(device))
        self.climateState(device)
      else:
        if device.isSensor():
          self.debug('device %s has sensors' % device.id())
          for type, sensors in device.sensorValues().items():
            self.debug('sensortype %s has %s' % (type, sensors))
            for sensor in sensors:
              result.append(self.discoverSensor(device, type, sensor['scale']))
              self.sensorState(device, type, sensor['scale'])

        if device.isDevice():
          self.debug('device %s is a device' % device.id())
          item = self.discoverDevice(device)
          result.append(item)
          if item[0] != "remote":
            self.deviceState(device)
    except Exception as e:
      self.debug('discovery %s' % str(e))
    return [x for x in result if x]

  def publish_hub_device(self):
    base_topic = self.getBaseTopic()
    device_name = self.getDeviceName()
    deviceId = 'hub'
    config = {
      'name': device_name,
      'state_topic': (
        '%s/%s/available' % (base_topic, device_name) if base_topic \
        else '%s/available' % device_name
      ),
      'payload_on': 'online',
      'payload_off': 'offline',
      'device_class': 'connectivity',
      'unique_id': '%s_%s' % (getMacAddr(), deviceId),
      'availability_topic': (
        '%s/%s/available' % (base_topic, device_name) if base_topic \
        else '%s/available' % device_name
      ),
      'device': {
        'identifiers': getMacAddr(),
        'connections': [['mac', getMacAddr(False)]],
        'manufacturer': 'Telldus Technologies',
        'model': Board.product().replace('-', ' ').title().replace(' ', '_'),
        'name': device_name,
        'sw_version': Board.firmwareVersion()
      }
    }
    self.client.publish(
      '%s/config' % self.getDeviceTopic('binary_sensor', deviceId),
      json.dumps(config),
      retain = True
    )
    return (deviceId, deviceId, deviceId)


  def run_discovery(self):
    self.debug('discover devices')
    try:
      # publish devices
      publishedDevices = [self.publish_hub_device()]
      deviceManager = DeviceManager(self.context)
      devices = deviceManager.retrieveDevices()
      for device in devices:
        try:
          self.debug(json.dumps({
            'deviceId': device.id(),
            'type': self.getDeviceType(device),
=======
import json
import logging
from time import gmtime, strftime

from base import \
    Application, \
    Plugin, \
    configuration, \
    ConfigurationNumber, \
    ConfigurationString, \
    ConfigurationBool, \
    ConfigurationList, \
    implements, \
    ISignalObserver, \
    slot

from telldus import DeviceManager
import Devices as devs
import logging
import paho.mqtt.client as mqtt

__name__ = 'HASSMQTT'


@configuration(
    username=ConfigurationString(
        defaultValue='',
        title='Mqtt username',
        description='Username for mqtt'
    ),
    password=ConfigurationString(
        defaultValue='',
        title='Mqtt password',
        description='Password for mqtt'
    ),
    hostname=ConfigurationString(
        defaultValue='',
        title='Mqtt hostname',
        description='Hostname for mqtt'
    ),
    port=ConfigurationNumber(
        defaultValue=1883,
        title='Mqtt port',
        description='Port for mqtt'
    ),
    discovery_topic=ConfigurationString(
        defaultValue='hatest',
        title='Autodiscovery topic',
        description='Homeassistants autodiscovery topic'
    ),
    device_name=ConfigurationString(
        defaultValue='ztest',
        title='Device name',
        description='Name of this device'
    ),
    base_topic=ConfigurationString(
        defaultValue='telldus',
        title='Base topic',
        description='Base topic for this device'
    ),
    state_retain=ConfigurationBool(
        defaultValue=True,
        title='Retain state changes',
        description='Post state changes with retain'
    ),
    use_via=ConfigurationBool(
        defaultValue=False,
        title='Use via_device',
        description='Use via_device to create device hierarchy. (Does not seem to work in home-assistant yet)'
    ),
    device_topics=ConfigurationList(
        defaultValue=[],
        hidden=True
    ),
    devices_configured=ConfigurationString(
        defaultValue='',
        hidden=True
    )
)
class Client(Plugin):
    implements(ISignalObserver)

    def __init__(self):
        Application().registerShutdown(self.onShutdown)

        self.discovered_flag = False

        self.mqtt_connected_flag = False
        self.client = mqtt.Client()
        self.client.on_disconnect = self.onMqttDisconnect
        self.client.on_connect = self.onMqttConnect
        self.client.on_message = self.onMqttMessage

        username = self.config('username')
        password = self.config('password')
        # if username setup mqtt login
        if username != '':
            self.client.username_pw_set(username, password)

        self.hub = devs.HaHub(self.config('device_name'), self._buildTopic)
        self._debug('Hub: %s' % json.dumps(self.hub.getConfig(None, False)))

        self.devices = [self.hub]
        Application().queue(self.discoverAndConnect)

    def configWasUpdated(self, key, value):
        if key in ['use_via', 'discovery_topic', 'device_name']:
            self.devices = []
            self.cleanupDevices()
            self.hub.deviceName = self.config('device_name')
            Application().queue(self.discoverAndConnect)
        elif key == 'state_retain' and value == False and self.mqtt_connected_flag:
            self._debug('Retain set to false, clear retained states')
            for topic in self.config('device_topics'):
                self.client.publish('%s/%s/state' % (self.config('discovery_topic'), topic), None, 0, False)
        elif key in ['username', 'password', 'hostname', 'port']:
            Application().queue(self.connect)

    def discoverAndConnect(self):
        self.discover()
        if self.config('hostname'):
            self.connect()

    def _debug(self, msg):
        logging.info("HaClient: %s", msg)
        if self.mqtt_connected_flag:
            baseTopic = self.config('base_topic')
            deviceName = self.config('device_name')
            self.client.publish('%s/%s/debug' % (baseTopic, deviceName), msg, 0, False)

    def _buildTopic(self, type, id):
        return '%s/%s/%s/%s' % (self.config('discovery_topic'), type, self.config('device_name'), id)

    def tearDown(self):
        # remove plugin
        self.devices = []
        self.cleanupDevices()
        self.disconnect()

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def connect(self):
        self.disconnect()

        host = self.config('hostname')
        port = self.config('port')

        self.client.will_set('%s/state' % self.hub.getDeviceTopic(), self.hub.getWillState(), 0, True)
        self.client.connect_async(host, port, keepalive=10)
        self.client.loop_start()

    def cleanupDevices(self):
        if self.mqtt_connected_flag and self.discovered_flag:
            # clean up published devices not found
            devicesConfigured = self.config('devices_configured')
            if devicesConfigured and devicesConfigured != '':
                oldDevs = [tuple(x) for x in json.loads(devicesConfigured)]
                for type, _, fullId in oldDevs:
                    oldTopic = self._buildTopic(type, fullId)
                    self.removeDeviceTopics(oldTopic)
                self.setConfig('devices_configured', None)

            savedTopics = self.config('device_topics')
            devTopics = [x.getDeviceTopic() for x in self.devices]
            removedTopics = [x for x in savedTopics if x not in devTopics]
            self._debug('Cleaning up devices : %s, %s, %s' % (savedTopics, devTopics, removedTopics))
            for topic in removedTopics:
                self.removeDeviceTopics(topic)

    def onMqttDisconnect(self, client, userdata, rc):
        self.mqtt_connected_flag = False
        self._debug('Mqtt disconnected')

    def onMqttConnect(self, client, userdata, flags, result):
        self.mqtt_connected_flag = True
        self._debug('Mqtt connected')
        self.publishDevices()
        self.client.subscribe('%s/+/%s/+/set' % (self.config('discovery_topic'), self.config('device_name')))
        self.client.subscribe('%s/+/%s/+/set/#' % (self.config('discovery_topic'), self.config('device_name')))
        Application().queue(self.cleanupDevices)

    def onMqttMessage(self, client, userdata, msg):
        self._debug('Mqtt message : %s, %s' % (msg.topic, msg.payload))
        devId = msg.topic.split('/')[3]
        for device in self.devices:
            if device.getID() == devId:
                device.runCommand(msg.topic, msg.payload)

    def onShutdown(self):
        # self.disconnect()
        pass

    def discover(self):
        self.discovered_flag = False
        self._debug('Discovering devices ...')

        self.devices = [self.hub]
        devMgr = DeviceManager(self.context)
        for device in devMgr.retrieveDevices():
            haDevs = devs.createDevices(device, self._buildTopic)
            self._debug('Discovered %s' % json.dumps(self._debugDevice(device)))
            for haDev in haDevs:
                self.devices.append(haDev)

        self.discovered_flag = True
        self._debug('Discovered %s devices' % len(self.devices))
        Application().queue(self.cleanupDevices)

    def publishState(self, haDev):
        states = haDev.getState()
        topic = '%s/state' % haDev.getDeviceTopic()
        self._debug('publish state for (%s) %s : %s' % (haDev.getID(), topic, states))
        if self.mqtt_connected_flag:
            for state in (states if isinstance(states, list) else [states]):
                self.client.publish(topic, state, 0, self.config('state_retain'))

    def publishDevices(self):
        for device in self.devices:
            self.publishDevice(device)
            self.publishState(device)

    def publishDevice(self, haDev):
        config = haDev.getConfig(self.hub, self.config('use_via'))
        topic = '%s/config' % haDev.getDeviceTopic()
        self._debug('publish config for (%s) %s : %s' % (haDev.getID(), topic, json.dumps(config)))
        if self.mqtt_connected_flag:
            self.client.publish(topic, json.dumps(config), 0, self.config('state_retain'))
        self.setConfig('device_topics', list(set(x.getDeviceTopic() for x in self.devices)))

    def removeDevice(self, haDev):
        self.removeDeviceTopics(haDev.getDeviceTopic())
        self.setConfig('device_topics', list(set(x.getDeviceTopic() for x in self.devices)))

    def removeDeviceTopics(self, devTopic):
        if self.mqtt_connected_flag:
            self._debug('Removing devicetopics %s/#' % devTopic)
            self.client.publish('%s/config' % devTopic, None, 0, True)
            self.client.publish('%s/state' % devTopic, None, 0, True)

    def _debugDevice(self, device):
        haDevs = devs.createDevices(device, self._buildTopic)
        return {
            'deviceId': device.id(),
>>>>>>> rewrite
            'name': device.name(),
            'isDevice': device.isDevice(),
            'isSensor': device.isSensor(),
            'methods': device.methods(),
            'battery': device.battery(),
            'parameters': device.allParameters() if hasattr(device, 'allParameters') else device.parameters(),
            'typeStr': device.typeString(),
            'sensors': device.sensorValues(),
<<<<<<< HEAD
            'state': device.state()
          }))
          publishedDevices.extend(self.discovery(device))
        except Exception as e:
          self.debug('run_discovery device exception %s' % str(e))

      for type, devId, fullId in list(set(self.getKnownDevices()) - set(publishedDevices)):
        self.remove_discovery(type, devId, fullId)

      self.setKnownDevices(publishedDevices)
    except Exception as e:
      self.debug('run_discovery exception %s' % str(e))

  def onConnect(self, client, userdata, flags, result):
    base_topic = userdata.config('base_topic')
    device_name = userdata.config('device_name')
    client.publish(
      '%s/%s/available' % (base_topic, device_name) if base_topic \
      else '%s/available' % device_name, 
      'online', 
      0, 
      True
    )
    try:
      userdata.run_discovery()
      #subscribe to commands
      userdata.debug('subscribing')
      client.subscribe('%s/+/%s/+/set' % (userdata.config('discovery_topic'), device_name))
      client.subscribe('%s/+/%s/+/set/+' % (userdata.config('discovery_topic'), device_name))
      userdata._ready = True
    except Exception as e:
      userdata.debug('OnConnect error %s' % str(e))

  def onDisconnect(self, client, userdata, rc):
    self.debug("Mqtt disconnected")
    userdata._ready = False

  @slot('deviceAdded')
  def onDeviceAdded(self, device):
    if not self._running:
      return
    try:
      self.debug('Device added %s' % device.id())
      devices = self.getKnownDevices()
      devices.extend(self.discovery(device))
      self.setKnownDevices(devices)
    except Exception as e:
      self.debug('onDeviceAdded error %s' % str(e))

  @slot('deviceRemoved')
  def onDeviceRemoved(self, deviceId):
    if not self._running:
      return
    try:
      self.debug('Device removed %s' % deviceId)
      devices = self.getKnownDevices()
      for type, devId, fullId in devices:
        if devId == str(deviceId):
          self.remove_discovery(type, devId, fullId)
      devices = [x for x in devices if x[1] != str(deviceId)]
      self.setKnownDevices(devices)
    except Exception as e:
      self.debug('onDeviceRemoved error %s' % str(e))

  @slot('deviceUpdated')
  def onDeviceUpdated(self, device):
    if not self._running:
      return
    try:
      self.debug('Device updated %s' % device.id())
      devices = self.getKnownDevices()
      for type, devId, fullId in devices:
        if devId == str(device.id()):
          self.remove_discovery(type, devId, fullId)
      devices = [x for x in devices if x[1] != str(device.id())]
      devices.extend(self.discovery(device))
      self.setKnownDevices(devices)
    except Exception as e:
      self.debug('onDeviceUpdated error %s' % str(e))

  @slot('rf433RawData')
  def onRawData(self, data,*__args, **__kwargs):
    if not self._running:
      return
    self.debug(json.dumps(data))

  @slot('sensorValueUpdated')
  def onSensorValueUpdated(self, device, valueType, value, scale):
    if not self._ready or not self._running:
      return
    self.debug(json.dumps({
      'type': 'sensorValueUpdated',
      'deviceId': device.id(),
      'valueType': valueType,
      'value': value,
      'scale': scale,
      'battery': device.battery()
    }))
    if self.getDeviceType(device) == 'climate':
      if not self.isKnownDevice('climate', device.id(), device.id()):
        self.debug('A wild climate device appeared! deviceId: %s' % device.id())
        type, devId, deviceId = self.discoverClimate(device)
        self.addKnownDevice(type, devId, deviceId)
      self.climateState(device)
    else:
      sensorId = self.getSensorId(device.id(), valueType, scale)
      if not self.isKnownDevice('sensor', device.id(), sensorId):
        self.debug('A wild sensor appeared! deviceId: %s, sensorId: %s' % (device.id(), sensorId))
        type, devId, deviceId = self.discoverSensor(device, valueType, scale)
        self.addKnownDevice(type, devId, deviceId)
      self.sensorState(device, valueType, scale)
      if device.battery() and device.battery() != Device.BATTERY_UNKNOWN:
        self.batteryState(device)

  @slot('deviceStateChanged')
  def onDeviceStateChanged(self, device, state, stateValue, origin=None):
    if not self._ready or not self._running:
      return
    deviceType = self.getDeviceType(device)
    self.debug(json.dumps({
      'type': 'deviceStateChanged',
      'deviceId': device.id(),
      'state': state,
      'stateValue': stateValue,
      'origin': origin,
      'devicetype': deviceType
    }))
    if not deviceType:
      return
    if self.getDeviceType(device) == 'climate':
      if not self.isKnownDevice('climate', device.id(), device.id()):
        self.debug('A wild climate device appeared! deviceId: %s' % device.id())
        type, devId, deviceId = self.discoverClimate(device)
        self.addKnownDevice(type, devId, deviceId)
      self.climateState(device)
    else:
      if not self.isKnownDevice(deviceType, device.id(), device.id()):
        self.debug('A wild device appeared! type: %s, deviceId: %s' % (deviceType, device.id()))
        type, devId, deviceId = self.discoverDevice(device)
        self.addKnownDevice(type, devId, deviceId)
      self.deviceState(device)
      if device.battery():
        self.batteryState(device)

  def onMessage(self, client, userdata, msg):
    try:
      topic = msg.topic
      payload = msg.payload

      if topic.split('/')[-1] == 'set':
        topicType = 'set'
      else:
        topicType = topic.split('/')[-1]
      deviceManager = DeviceManager(userdata.context)
      
      device_id = int(msg.topic.split('/')[3])
      device = deviceManager.device(device_id)
      deviceType = userdata.getDeviceType(device)
      if not deviceType:
        return

      userdata.debug(json.dumps({
        'type': 'command',
        'device_id': device_id,
        'device_type': deviceType,
        'command': payload
      }))

      def failed(reason, **__kwargs):
        self.debug('Device command failed: %s' % reason)

      if deviceType == 'climate':
        if topicType == 'mode':
          mode = { 'fan_only': Thermostat.MODE_FAN }.get(payload, payload)
          setpoint = self.getClimateSetPoint(device, mode)
          if setpoint is not None:
            value = { 
              'mode': mode, 
              'changeMode': True, 
              'temperature': self.getClimateSetPoint(device, mode) 
            }
            self.debug('Command THERMOSTAT value: %s' % value)
            device.command(
              Device.THERMOSTAT, 
              value = value,
              origin = 'mqtt_hass',
              failure = failed
            )
          else:
            self.debug('Can not set mode, setpoint none')
        if topicType == 'setpoint':
          setpoint = float(payload) if payload else None
          if setpoint is not None:
            value = { 
              'mode': self.getClimateMode(device), 
              'changeMode': False, 
              'temperature': setpoint
            }
            self.debug('Command THERMOSTAT value: %s' % value)
            device.command(
              Device.THERMOSTAT,
              value = value,
              origin = 'mqtt_hass',
              failure = failed
            )
          else:
            self.debug('Can not update setpoint, setpoint none (%s)' % payload)

      elif deviceType == 'light':
        payload = json.loads(payload)
        if 'brightness' in payload:
          if int(payload['brightness']) == 0:
            device.command(
              Device.TURNOFF, 
              origin = 'mqtt_hass',
              failure = failed
            )
          else:
            device.command(
              Device.DIM, 
              value = int(payload['brightness']), 
              origin = 'mqtt_hass',
              failure = failed
            )
        else:
          device.command(
            Device.TURNON if payload['state'].upper() == 'ON' \
            else Device.TURNOFF, 
            value = 255, 
            origin = 'mqtt_hass',
            failure = failed
          )

      elif deviceType == 'switch':
        device.command(
          Device.TURNON if payload.upper() == 'ON' \
          else Device.BELL if payload.upper() == 'BELL' \
          else Device.TURNOFF, 
          origin = 'mqtt_hass',
          failure = failed
        )

      elif deviceType == 'cover':
        device.command(
          Device.UP if payload.upper() == 'OPEN' \
          else Device.DOWN if payload.upper() == 'CLOSE' else \
          Device.STOP, 
          origin = 'mqtt_hass',
          failure = failed
        )
    except Exception as e:
      userdata.debug('onMessage exception %s' % str(e))

  
=======
            'state': device.state(),
            'devices': [x.getConfig(self.hub, self.config('use_via')) for x in haDevs]
        }

    @slot('deviceAdded')
    def onDeviceAdded(self, device):
        self._debug('Device added %s %s' % (device.id(), device.name()))

        if device not in (x.device for x in self.devices):
            haDevs = devs.createDevices(device, self._buildTopic)
            self._debug('New discovery %s' % json.dumps(self._debugDevice(device)))
            for haDev in haDevs:
                self.devices.append(haDev)
                self.publishDevice(haDev)
                self.publishState(haDev)
        else:
            self._debug('Device already exists, ignoring')

    @slot('deviceRemoved')
    def onDeviceRemoved(self, deviceId):
        self._debug('Device removed %s' % deviceId)
        haDevs = [x for x in self.devices if x.deviceId == deviceId]
        for haDev in haDevs:
            self.devices.remove(haDev)
            self.removeDevice(haDev)

    @slot('deviceUpdated')
    def onDeviceUpdate(self, device):
        self._debug('Device updated %s, %s' % (device.id(), self.debugDevice(device)))
        for haDev in [x for x in self.devices if x.deviceId == device.id()]:
            self.publishDevice(haDev)

    @slot('deviceStateChanged')
    def onDeviceStateChanged(self, device, state, stateValue, origin=None):
        self._debug('Device state changed (%s) state: %s value: %s origin: %s' %
                    (device.id(), state, stateValue, origin))
        haDev = next((x for x in self.devices if x.device == device), None)
        if haDev:
            self.publishState(haDev)
        else:
            self._debug('failed to find device for state change %s' % device.id())

    @slot('sensorValueUpdated')
    def onSensorValueUpdated(self, device, valueType, value, scale):
        self._debug('Sensor value changed (%s) type: %s scale: %s value: %s' %
                    (device.id(), valueType, scale, value))
        haDev = next((x for x in self.devices if x.device == device and x.sensorType ==
                     valueType and x.sensorScale == scale), None)
        if haDev:
            self.publishState(haDev)
        else:
            self._debug('failed to find device for sensor change %s %s %s' % (device.id(), valueType, scale))
>>>>>>> rewrite
