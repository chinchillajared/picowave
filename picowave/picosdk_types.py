from __future__ import annotations

from ctypes import Structure, c_int16, c_int32, c_uint16


class PS2000_TRIGGER_CONDITIONS(Structure):
    _fields_ = [
        ("channelA", c_int16),
        ("channelB", c_int16),
        ("channelC", c_int16),
        ("channelD", c_int16),
        ("external", c_int16),
        ("pulseWidthQualifier", c_int16),
    ]


class PS2000_PWQ_CONDITIONS(Structure):
    _fields_ = [
        ("channelA", c_int16),
        ("channelB", c_int16),
        ("channelC", c_int16),
        ("channelD", c_int16),
        ("external", c_int16),
    ]


class PS2000_TRIGGER_CHANNEL_PROPERTIES(Structure):
    _fields_ = [
        ("thresholdMajor", c_int16),
        ("thresholdMinor", c_int16),
        ("hysteresis", c_uint16),
        ("channel", c_int16),
        ("thresholdMode", c_int32),
    ]
