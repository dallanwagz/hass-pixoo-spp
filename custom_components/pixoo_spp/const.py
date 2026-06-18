"""Constants for the Divoom Pixoo (SPP bridge) integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "pixoo_spp"
PLATFORMS = [Platform.LIGHT, Platform.SENSOR, Platform.BINARY_SENSOR]

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_PORT = 8888  # untether_spp bridge default

# Connection behaviour
CONNECT_TIMEOUT = 10.0
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0
STALE_AFTER = 90.0  # no inbound bytes for this long -> tear down & reconnect

# Services
SERVICE_PUSH_SOLID = "push_solid"
SERVICE_PUSH_IMAGE = "push_image"
SERVICE_UPLOAD_ANIMATION = "upload_animation"
