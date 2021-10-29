# -*- coding: utf-8 -*-
import json
import logging
#from time import gmtime, strftime

from base import Application, Plugin, configuration, ConfigurationNumber, ConfigurationString, ConfigurationBool, ConfigurationSelect, ConfigurationList, implements, ISignalObserver, slot  # type: ignore
from hass_client.utils import getIpAddr

from telldus import DeviceManager  # type: ignore
from tellduslive.base import TelldusLive  # type: ignore

import Devices as devs
import logging
import paho.mqtt.client as mqtt  # type: ignore

__name__ = 'HASSMQTT'


@configuration(
    device_name=ConfigurationString(
        defaultValue='ztest',
        title='Device name',
        description='Name of this device',
        sortOrder=1
    ),

    hostname=ConfigurationString(
        defaultValue='',
        title='Mqtt hostname',
        description='Hostname for mqtt',
        sortOrder=2
    ),
    port=ConfigurationNumber(
        defaultValue=1883,
        title='Mqtt port',
        description='Port for mqtt',
        sortOrder=3
    ),
    username=ConfigurationString(
        defaultValue='',
        title='Mqtt username',
        description='Username for mqtt',
        sortOrder=4
    ),
    password=ConfigurationString(
        defaultValue='',
        title='Mqtt password',
        description='Password for mqtt',
        sortOrder=5
    ),


    discovery_topic=ConfigurationString(
        defaultValue='hatest',
        title='Autodiscovery topic',
        description='Homeassistants autodiscovery topic',
        sortOrder=6
    ),
    base_topic=ConfigurationString(
        defaultValue='telldus',
        title='Base topic',
        description='Base topic for this device, for ex. debug',
        sortOrder=7
    ),

    state_retain=ConfigurationBool(
        defaultValue=True,
        title='Retain state changes',
        description='Post state changes with retain',
        sortOrder=8
    ),
    use_via=ConfigurationBool(
        defaultValue=False,
        title='Create sub devices/via_device (Missing in HA)',
        description='Create sub devices and setup via_device to create device hierarchy. (Does not seem to work in home-assistant yet)',
        sortOrder=9
    ),
    useConfigUrl=ConfigurationBool(
        defaultValue=False,
        title='Support device configuration url (HA >= 2021.11)',
        description='Requires HA >= 2021.11.0',
        sortOrder=10
    ),
    configUrl=ConfigurationSelect(
        defaultValue='live',
        title='Configuration url',
        options={
            'live': 'Use Telldus live as url',
            'local': 'Use local IP as url'
        },
        sortOrder=11
    ),
    useEntityCategories=ConfigurationBool(
        defaultValue=False,
        title='Support entity categories (HA >= 2021.11)',
        description='Requires HA >= 2021.11.0',
        sortOrder=12
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
        self.live = TelldusLive(self.context)  # pylint: disable=too-many-function-args

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

        useConfigUrl = self.config('useConfigUrl')
        configUrl = 'https://live.telldus.se' if self.config('configUrl') == 'live' else ('http://%s' % getIpAddr())

        self.hub = devs.HaHub(self.config('device_name'), self._buildTopic, configUrl if useConfigUrl else None)
        self._debug('Hub: %s' % json.dumps(self._getDeviceConfig(self.hub)))

        self.staticDevices = [
            self.hub,
            devs.HaLiveConnection(self.hub, self.live, self._buildTopic),
            devs.HaIpAddr(self.hub, self._buildTopic),
            devs.HaCpu(self.hub, self._buildTopic),
            devs.HaRamFree(self.hub, self._buildTopic)
        ]
        self.devices = self.staticDevices + []
        Application().queue(self.discoverAndConnect)
        Application().registerScheduledTask(self._updateTimedSensors, seconds=30)

    def configWasUpdated(self, key, value):
        if key in ['use_via', 'useConfigUrl', 'configUrl', 'useEntityCategories', 'discovery_topic', 'device_name']:
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

    def _updateTimedSensors(self):
        for haDev in [x for x in self.devices if isinstance(x, devs.HaTimedSensor)]:
            self.publishState(haDev)

    def _debug(self, msg):
        logging.info("HaClient: %s", msg)
        if self.mqtt_connected_flag:
            baseTopic = self.config('base_topic')
            deviceName = self.config('device_name')
            self.client.publish('%s/%s/debug' % (baseTopic, deviceName), msg, 0, False)

    def _buildTopic(self, type, id):
        return '%s/%s/%s/%s' % (self.config('discovery_topic'), type, self.config('device_name'), id)

    def _getDeviceConfig(self, haDev):
        conf = haDev.getConfig()
        if not self.config('useEntityCategories'):
            conf.pop('entity_category', None)
        return conf

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
            try:
                devicesConfigured = self.config('devices_configured')
                if devicesConfigured and devicesConfigured != 'None' and devicesConfigured != '':
                    oldDevs = [tuple(x) for x in json.loads(devicesConfigured)]
                    for type, _, fullId in oldDevs:
                        oldTopic = self._buildTopic(type, fullId)
                        self.removeDeviceTopics(oldTopic)
                    self.setConfig('devices_configured', None)
            except:
                pass

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

        self.devices = self.staticDevices + []
        devMgr = DeviceManager(self.context)
        for device in devMgr.retrieveDevices():
            haDevs = devs.createDevices(device, self.hub, self._buildTopic, self.config('use_via'))
            self._debug('Discovered %s' % json.dumps(self._debugDevice(device, haDevs)))
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
                self.client.publish(topic, str(state), 0, self.config('state_retain'))

    def publishDevices(self):
        for device in self.devices:
            self.publishDevice(device)
            self.publishState(device)

    def publishDevice(self, haDev):
        config = self._getDeviceConfig(haDev)
        if self.config('useEntityCategories'):
            config.update({
                'entity_category': haDev.getCategory()
            })
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

    def _debugDevice(self, device, haDevs):
        return {
            'deviceId': device.id(),
            'name': device.name(),
            'isDevice': device.isDevice(),
            'isSensor': device.isSensor(),
            'methods': device.methods(),
            'battery': device.battery(),
            'parameters': device.allParameters() if hasattr(device, 'allParameters') else device.parameters(),
            'typeStr': device.typeString(),
            'sensors': device.sensorValues(),
            'state': device.state(),
            'devices': [self._getDeviceConfig(x) for x in haDevs]
        }

    @slot('deviceAdded')
    def onDeviceAdded(self, device):
        self._debug('Device added %s %s' % (device.id(), device.name()))

        if device not in (x.device for x in self.devices if hasattr(x, 'device')):
            haDevs = devs.createDevices(device, self.hub, self._buildTopic, self.config('use_via'))
            self._debug('New discovery %s' % json.dumps(self._debugDevice(device, haDevs)))
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
        haDev = next((x for x in self.devices if x.deviceId == device.id()), None)
        if haDev:
            self.publishState(haDev)
        else:
            self._debug('failed to find device for state change %s' % device.id())

    @slot('sensorValueUpdated')
    def onSensorValueUpdated(self, device, valueType, value, scale):
        self._debug('Sensor value changed (%s) type: %s scale: %s value: %s' %
                    (device.id(), valueType, scale, value))
        haDev = next((x for x in self.devices if hasattr(x, 'device') and x.device == device and x.sensorType ==
                     valueType and x.sensorScale == scale), None)
        if haDev:
            self.publishState(haDev)
        else:
            self._debug('failed to find device for sensor change %s %s %s' % (device.id(), valueType, scale))

    @slot('liveRegistered')
    def liveRegistered(self, _msg, _refReq):
        liveSensor = next(x for x in self.devices if isinstance(x, devs.HaLiveConnection))
        if liveSensor:
            self.publishState(liveSensor)

    @slot('liveDisconnected')
    def liveDisconnected(self):
        liveSensor = next(x for x in self.devices if isinstance(x, devs.HaLiveConnection))
        if liveSensor:
            self.publishState(liveSensor)
