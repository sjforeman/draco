"""Legendre spectrum estimation and filtering.
"""

import numpy as np
import scipy.linalg as la
import scipy.stats as st

from caput import mpiarray, config

from ..core import containers, task, io



class LegendreSpectrum(task.SingleTask):



    def process(self, ss):




