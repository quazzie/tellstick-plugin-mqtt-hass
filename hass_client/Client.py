# -*- coding: utf-8 -*-

import json

from base import \
	Application, \
	Plugin, \
	Settings, \
	configuration, \
	ConfigurationNumber, \
	ConfigurationString, \
	implements, \
	ISignalObserver, \
	slot
import paho.mqtt.client as mqtt
import logging
from board import Board
from telldus import DeviceManager, Device
import netifaces

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

def getMacAddr(ifname, compact = True):
	addrs = netifaces.ifaddresses(ifname)
	try:
		mac = addrs[netifaces.AF_LINK][0]['addr']
	except (IndexError, KeyError) as e:
		return ''
	return mac.upper().replace(':', '') if compact else mac.upper()

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
	base_topic=ConfigurationString(
		defaultValue='telldus',
		title='Base topic',
		description='Base topic for this device'
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
		self.client_on_disconnect = self.onDisconnect
		self.client.on_connect = self.onConnect
		self.client.on_message = self.onMessage
		if self.config('hostname') != '':
			self.connect()

	def onShutdown(self):
		self._running = False
		self.client.loop_stop()
		self.client.disconnect()

	def getKnownDevices(self):
		if not self._knownDevices:
			if self.config('devices_configured'):
				self._knownDevices = [tuple(x) for x in json.loads(self.config('devices_configured'))]
			else:
				self._knownDevices = []
		return self._knownDevices
	
	def setKnownDevices(self, devices):
		try:
			self.debug('Setting knownDevices to : %s' % devices)
			self._knownDevices = devices
			self.setConfig('devices_configured', json.dumps(devices))
		except Exception as e:
			self.debug('setKnownDevices error %s' % str(e))

	def isKnownDevice(self, type, devId, deviceId):
		devices = self.getKnownDevices()
		return (type, str(devId), str(deviceId)) in devices

	def addKnownDevice(self, type, devId, deviceId):
		devices = self.getKnownDevices()
		devices.append((type, str(devId), str(deviceId)))
		self.setKnownDevices(devices)

	def configWasUpdated(self, key, value):
		if not key in ['devices_configured']:
			self.connect()

	def tearDown(self):
		try:
			for type, id, fullId in self.getKnownDevices():
				deviceTopic = self.getDeviceTopic(type, fullId)
				self.client.publish('%s/config' % deviceTopic, '', retain = True)
				self.client.publish('%s/state' % deviceTopic, '', retain = True)
		except Exception as e:
			self.debug('tearDown %s' % str(e))

	def connect(self):
		username = self.config('username')
		password = self.config('password')
		base_topic = self.config('base_topic')
		hostname = self.config('hostname')
		port = self.config('port')
		
		if username != '':
			self.client.username_pw_set(username, password)
		self.client.will_set('%s/available' % (base_topic), 'offline', 0, True)
		self.client.connect_async(hostname, port, keepalive=10)
		self.client.loop_start()

	def debug(self, msg):
		base_topic = self.config('base_topic')
		debugTopic = '%s/debug' % (base_topic)
		self.client.publish(debugTopic, msg)

	def getDeviceType(self, device):
		capabilities = device.methods()
		if capabilities & Device.DIM:
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
		baseTopic = self.config('discovery_topic')
		return '%s/%s/telldus/%s' % (baseTopic, type, id)

	def getSensorId(self, deviceId, valueType, scale):
		return '%s_%s_%s' % (deviceId, valueType, scale)
	
	def getBatteryId(self, device):
		return '%s_battery' % device.id()

	def formatBattery(self, battery):
		if battery == Device.BATTERY_LOW:
			return 1
		elif battery == Device.BATTERY_UNKNOWN:
			return None
		elif battery == Device.BATTERY_OK:
			return 100
		else:
			return int(battery)

	def formatScale(self, type, scale):
		return ScaleConverter.get(type, {}).get(scale, '')

	def deviceState(self, device):
		try:
			state, stateValue = device.state()

			deviceType = self.getDeviceType(device)
			if not deviceType:
				return

			self.debug('deviceState %s, state: %s, value: %s' % (device.id(), state, stateValue))

			stateTopic = '%s/state' % self.getDeviceTopic(deviceType, device.id())
			payload = ''

			if deviceType in ['light']:
				if state == Device.DIM:
					payload = json.dumps({
						'state': 'ON' if stateValue and int(stateValue) > 0 else 'OFF',
						'brightness': int(stateValue) if stateValue else 0
					})
				else:
					payload = json.dumps({
						'state': 'ON' if state == Device.TURNON else 'OFF',
						'brightness': (int(stateValue) or 255) if state == Device.TURNON else 0
					})
			elif deviceType in ['switch']:
				payload = 'ON' if state in [Device.TURNON, Device.BELL] else 'OFF' 
			elif deviceType in ['binary_sensor']:
				payload = 'ON' if state in [Device.TURNON] else 'OFF' 
			elif deviceType in ['cover']:
				payload = 'OPEN' if state == Device.UP else 'CLOSED' if state == Device.DOWN else 'STOP'

			self.client.publish(stateTopic, payload, retain = True)
			if state == Device.BELL:
				self.client.publish(stateTopic, 'OFF', retain = True)
		except Exception as e:
			self.debug('deviceState exception %s' % e.message)

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
			self.debug('sensorState exception %s' % e.message)
	
	def batteryState(self, device):
		try:
			self.client.publish(
				'%s/state' % self.getDeviceTopic('sensor', self.getBatteryId(device)),
				self.formatBattery(device.battery()),
				retain = True
			)
		except Exception as e:
			self.debug('batteryState exception %s' % e.message)

	def discover(self, device, type, deviceId, config):
		base_topic = self.config('base_topic')
		config.update({ 
			'unique_id': 'telldus_%s' % deviceId,
			'availability_topic': '%s/available' % (base_topic),
			'device': {
				'identifiers': getMacAddr(Board.networkInterface()),
				'connections': [['mac', getMacAddr(Board.networkInterface(), False)]],
				'manufacturer': 'Telldus Technologies',
				'model': Board.product(),
				'name': 'telldus',
				'sw_version': Board.firmwareVersion(),
			} 
		})
		self.client.publish(
			'%s/config' % self.getDeviceTopic(type, deviceId), 
			json.dumps(config),
			retain = True
		)
		return (type, str(device.id()), str(deviceId))

	def undiscover(self, type, devId, fullId):
		deviceTopic = self.getDeviceTopic(type, fullId)
		self.debug('undiscover device %s,%s,%s : %s' % (type, devId, fullId, deviceTopic))
		self.client.publish('%s/config' % deviceTopic, '', retain = True)
		self.client.publish('%s/state' % deviceTopic, '', retain = True)

	def discoverBattery(self, device):
		try:
			sensorConfig = {
				'name': '%s - Battery' % device.name(),
				'unit_of_measurement': '%',
				'device_class': 'battery'
			}
			return self.discover(device, 'sensor', self.getBatteryId(device), sensorConfig)
		except Exception as e:
			self.debug('discoverBattery %s' % str(e))

	def discoverSensor(self, device, type, scale):
		try:
			sensorConfig = {
				'name': '%s %s - %s' % (
					device.name(), 
					Device.sensorTypeIntToStr(type), 
					self.formatScale(type, scale)
				),
				'value_template': '{{ value_json.value }}',
				'json_attributes': ['lastUpdated'],
				'unit_of_measurement': self.formatScale(type, scale),
			}
			if ClassConverter.get(type, None):
				sensorConfig.update({
					'device_class': ClassConverter.get(type, None)
				})

			sensorId = self.getSensorId(device.id(), type, scale)
			return self.discover(device, 'sensor', sensorId, sensorConfig)
		except Exception as e:
			self.debug('discoverSensor %s' % str(e))

	def discoverDevice(self, device):
		try:
			deviceType = self.getDeviceType(device)
			if not deviceType:
				return None	

			deviceTopic = self.getDeviceTopic(deviceType, device.id())
			deviceConfig = { 'name': device.name() }

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

			return self.discover(device, deviceType, device.id(), deviceConfig)
		except Exception as e:
			self.debug('discoverDevice %s' % str(e))

	def discovery(self, device):
		result = []
		try:
			if device.battery():
				self.debug('device %s has battery' % device.id())
				self.discoverBattery(device)
				result.append(self.batteryState(device))

			if device.isSensor():
				self.debug('device %s has sensors' % device.id())
				for type, sensors in device.sensorValues().items():
					self.debug('sensortype %s has %s' % (type, sensors))
					for sensor in sensors:
						result.append(self.discoverSensor(device, type, sensor['scale']))
						self.sensorState(device, type, sensor['scale'])

			if device.isDevice():
				self.debug('device %s is a device' % device.id())
				result.append(self.discoverDevice(device))
				self.deviceState(device)
		except Exception as e:
			self.debug('discovery %s' % str(e))
		return [x for x in result if x]

	def run_discovery(self):
		self.debug('discover devices')
		try:
			# publish devices
			publishedDevices = []
			deviceManager = DeviceManager(self.context)
			devices = deviceManager.retrieveDevices()
			for device in devices:
				try:
					self.debug(json.dumps({
						'deviceId': device.id(),
						'type': self.getDeviceType(device),
						'name': device.name(),
						'isDevice': device.isDevice(),
						'isSensor': device.isSensor(),
						'methods': device.methods(),
						'battery': device.battery(),
						'parameters': device.allParameters() if hasattr(device, 'allParameters') else device.parameters(),
						'type': device.typeString(),
						'sensors': device.sensorValues(),
						'state': device.state()
					}))
					publishedDevices.extend(self.discovery(device))
				except Exception as e:
					self.debug('run_discovery device exception %s' % e.message)

			for type, devId, fullId in list(set(self.getKnownDevices()) - set(publishedDevices)):
				self.undiscover(type, devId, fullId)

			self.setKnownDevices(publishedDevices)
		except Exception as e:
			self.debug('run_discovery exception %s' % e.message)

	def onConnect(self, client, userdata, flags, result):
		base_topic = userdata.config('base_topic')
		client.publish('%s/available' % (base_topic), 'online', 0, True)
		userdata.debug('Hello from telldus, connected')
		try:
			userdata.debug('KnownDevices: %s' % userdata.getKnownDevices())
			userdata.run_discovery()
			#subscribe to commands
			userdata.debug('subscribing')
			client.subscribe('%s/+/telldus/+/set' % userdata.config('discovery_topic'))
			userdata._ready = True
		except Exception as e:
			userdata.debug('OnConnect error %s' % str(e))

	def onDisconnect(self, client, userdata, rc):
		userdata._ready = False

	@slot('deviceAdded')
	def onDeviceAdded(self, device):
		try:
			self.debug('Device added %s' % device.id())
			devices = self.getKnownDevices()
			devices.extend(self.discovery(device))
			self.setKnownDevices(devices)
		except Exception as e:
			self.debug('onDeviceAdded error %s' % str(e))

	@slot('deviceRemoved')
	def onDeviceRemoved(self, deviceId):
		try:
			self.debug('Device removed %s' % deviceId)
			devices = self.getKnownDevices()
			for type, devId, fullId in devices:
				if devId == str(deviceId):
					self.undiscover(type, devId, fullId)
			devices = [x for x in devices if x[1] != str(deviceId)]
			self.setKnownDevices(devices)
		except Exception as e:
			self.debug('onDeviceRemoved error %s' % str(e))

	@slot('deviceUpdated')
	def onDeviceUpdated(self, device):
		try:
			self.debug('Device updated %s' % device.id())
			devices = self.getKnownDevices()
			for type, devId, fullId in devices:
				if devId == str(deviceId):
					self.undiscover(type, devId, fullId)
			devices = [x for x in devices if x[1] != str(device.id())]
			devices.extend(self.discovery(device))
			self.setKnownDevices(devices)
		except Exception as e:
			self.debug('onDeviceUpdated error %s' % str(e))

	@slot('rf433RawData')
	def onRawData(self, data,*__args, **__kwargs):
		self.debug(json.dumps(data))

	@slot('sensorValueUpdated')
	def onSensorValueUpdated(self, device, valueType, value, scale):
		if not self._ready:
			return
		self.debug(json.dumps({
			'type': 'sensorValueUpdated',
			'deviceId': device.id(),
			'valueType': valueType,
			'value': value,
			'scale': scale,
			'battery': device.battery()
		}))
		sensorId = self.getSensorId(device.id(), valueType, scale)
		if not self.isKnownDevice('sensor', device.id(), sensorId):
			self.debug('A wild sensor appeared! deviceId: %s, sensorId: %s' % (device.id(), sensorId))
			type, devId, deviceId = self.discoverSensor(device, valueType, scale)
			self.addKnownDevice(type, devId, deviceId)
		self.sensorState(device, valueType, scale)
		if device.battery():
			self.batteryState(device)

	@slot('deviceStateChanged')
	def onDeviceStateChanged(self, device, state, stateValue, origin=None):
		if not self._ready:
			return
		self.debug(json.dumps({
			'type': 'deviceStateChanged',
			'deviceId': device.id(),
			'state': state,
			'stateValue': stateValue,
			'origin': origin
		}))
		deviceType = self.getDeviceType(device)
		if not deviceType:
			return
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

			topicType = topic.split('/')[-1]
			deviceManager = DeviceManager(userdata.context)
			
			device_id = int(msg.topic.split('/')[-2])
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
			if deviceType == 'light':
				payload = json.loads(payload)
				if 'brightness' in payload:
					if int(payload['brightness']) == 0:
						device.command('turnoff', origin = 'mqtt_hass')
					else:
						device.command('dim', int(payload['brightness']), origin = 'mqtt_hass')
				else:
					device.command('turnon' if payload['state'] == 'ON' else 'turnoff', origin = 'mqtt_hass')
			elif deviceType == 'switch':
				device.command('turnon' if payload == 'ON' else 'bell' if payload == 'BELL' else 'turnoff', origin = 'mqtt_hass')
			elif deviceType == 'cover':
				device.command('up' if payload == 'OPEN' else 'down' if payload == 'CLOSE' else 'stop', origin = 'mqtt_hass')
		except Exception as e:
			userdata.debug('onMessage exception %s' % e.message)

	