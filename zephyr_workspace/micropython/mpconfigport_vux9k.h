// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

// MicroPython's configuration for vux9k (make build-micropython passes this file as
// CONFIG_MICROPY_CONFIGFILE): the port's full mpconfigport.h, less what doesn't build here.
#include "mpconfigport.h"

// The zsensor module names SENSOR_ATTR_GAIN and SENSOR_ATTR_RESOLUTION, which Zephyr v3.7's
// sensor.h doesn't have (the port's CI builds against v4.4); the board has no sensor anyway.
#undef MICROPY_PY_ZSENSOR
#define MICROPY_PY_ZSENSOR (0)
