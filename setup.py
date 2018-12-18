#!/usr/bin/env python
# -*- coding: utf-8 -*-

try:
	from setuptools import setup
except ImportError:
	from distutils.core import setup

setup(
	name='MQTT Homeassistant',
	version='0.74',
	description='Plugin to connect to Homeassistant via MQTT Autodiscover',
	icon='hass.png',
	color='#660066',
	author='Tommy Jonsson',
	author_email='quazzie@gmail.com',
	category='notifications',
	packages=['hass_client'],
	entry_points={ \
		'telldus.startup': ['c = hass_client:Client [cREQ]']
	},
	extras_require=dict(cREQ='Base>=0.1\nTelldus>=0.1'),
)
