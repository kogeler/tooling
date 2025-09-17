#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

"""
Enhanced traffic masking components with advanced obfuscation techniques
"""

from .timing import AdaptiveTimingModel
from .correlation import CorrelationBreaker
from .ml_resistance import MLResistantGenerator
from .entropy import EntropyEnhancer
from .state_machine import ProtocolStateMachine

__all__ = [
    'AdaptiveTimingModel',
    'CorrelationBreaker',
    'MLResistantGenerator',
    'EntropyEnhancer',
    'ProtocolStateMachine'
]

__version__ = '2.0.0'
