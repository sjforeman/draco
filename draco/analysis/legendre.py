"""Legendre spectrum estimation and filtering.
"""

import numpy as np
import scipy.linalg as la
import scipy.stats as st

from caput import mpiarray, config

from ..core import containers, task, io



class LegendreSpectrum(task.SingleTask):
    """ Calculate the Legendre transform along frequency direction

    Attributes
    ----------

    """

    def setup(self, telescope):
        """Set the telescope needed to generate Stokes I.

        Parameters
        ----------
        telescope : TransitTelescope
        """
        self.telescope = io.get_telescope(telescope)


    def process(self, mmodes):
        """Estimate the Legendre spectrum.

        Parameters
        ----------
        mmodes: m-modes transformed data

        Returns
        -------
        lspec: LegendreSpectrum
        """


        tel = self.telescope

        ss.redistribute('prod')

        # iterate over all baselines and use
        for lbi, bi in delay_spec.spectrum[:].enumerate(axis=0):
            pass



        return


