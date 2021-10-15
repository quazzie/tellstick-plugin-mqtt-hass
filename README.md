# TellStick mqtt-plugin for HomeAssistant
<img src="https://img.shields.io/github/v/release/quazzie/tellstick-plugin-mqtt-hass?include_prereleases" /> <img src="https://img.shields.io/github/release-date-pre/quazzie/tellstick-plugin-mqtt-hass" /> <img src="https://img.shields.io/github/commits-since/quazzie/tellstick-plugin-mqtt-hass/latest" /> <img src="https://img.shields.io/github/downloads/quazzie/tellstick-plugin-mqtt-hass/total" />

Plugin for **TellStick Znet v2** to support MQTT push/subscribe, enables [Home Assistant](https://home-assistant.io) integration via mqtt autodiscovery.

## Usage instruction

This plugin is intended to be installed on a TellStick Znet v2.

In order to have support for local plugins you must contact Telldus Support, via http://support.telldus.com.

Open a support request stating that you like an updated Firmware with support for **local plugins** and ask for the supported developer **quazzie@gmail.com** with the pubic key below.

Once the support request is accepted log in to your TellStick Znet v2 (http://tellstick.local) using your Telldus Live credentials. When logged in go to `Plugins (beta)` and from that click `Manual upload` (:arrow_up:) and upload the downloaded package (from https://github.com/quazzie/tellstick-plugin-mqtt-hass/releases).

**Reboot after install**

Configure the plugin with your mqtt settings and if your Home Assistant is configured to support auto discovery via [mqtt](https://www.home-assistant.io/integrations/mqtt) the devices connected to your TellStick will automatically show up in Home Assistant.

**Support / thanks**

Help support or thank me:
* [buy me a ~~coffee~~ beer](https://www.buymeacoffee.com/quazzie)
* [flattr](https://flattr.com/github/quazzie)

```key
-----BEGIN PGP PUBLIC KEY BLOCK-----

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
=6Ol1
-----END PGP PUBLIC KEY BLOCK-----
```
