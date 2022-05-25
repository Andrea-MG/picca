"""This module defines the class Dr16FixedFudgeExpectedFlux"""
import logging

import fitsio
import numpy as np
from scipy.interpolate import interp1d

from picca.delta_extraction.errors import ExpectedFluxError
from picca.delta_extraction.expected_fluxes.dr16_expected_flux import Dr16ExpectedFlux
from picca.delta_extraction.expected_fluxes.dr16_expected_flux import (
    defaults, accepted_options)

accepted_options = sorted(list(set(accepted_options + ["eta value"])))

defaults = defaults.copy()
defaults.update({
    "eta value": 1.0,
})


class Dr16FixedEtaExpectedFlux(Dr16ExpectedFlux):
    """Class to the expected flux similar to Dr16ExpectedFlux but fixing eta

    Methods
    -------
    (see Dr16ExpectedFlux in py/picca/delta_extraction/expected_fluxes/dr16_expected_flux.py)
    __init__
    __initialize_get_eta
    __parse_config

    Attributes
    ----------
    (see Dr16ExpectedFlux in py/picca/delta_extraction/expected_fluxes/dr16_expected_flux.py)

    eta_value: float or string
    If a string, name of the file containing the eta values as a function of
    wavelength. If a float, the eta value will be applied to all wavelengths
    """

    def __init__(self, config):
        """Initialize class instance.

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class
        """
        self.logger = logging.getLogger(__name__)

        # load variables from config
        self.eta_value = None
        self.__parse_config(config)

        super().__init__(config)

    def _initialize_get_eta(self):
        """Initialiaze function get_fudge"""
        # initialize fudge factor
        if self.eta_value.endswith(".fits") or self.eta_value.endswith(
                ".fits.gz"):
            hdu = fitsio.read(self.eta_value, ext="VAR_FUNC")
            self.get_eta = interp1d(hdu["loglam"],
                                    hdu["eta"],
                                    fill_value='extrapolate',
                                    kind='nearest')
        else:
            eta = np.ones(self.num_bins_variance) * float(self.eta_value)
            self.get_eta = interp1d(self.log_lambda_var_func_grid,
                                    eta,
                                    fill_value='extrapolate',
                                    kind='nearest')

    def __parse_config(self, config):
        """Parse the configuration options

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class

        Raises
        ------
        ExpectedFluxError if iter out prefix is not valid
        """
        self.eta_value = config.get("eta value")
        if self.eta_value is None:
            raise ExpectedFluxError("Missing argument 'eta value' required "
                                    "by Dr16FixEtaExpectedFlux")
        if not (self.eta_value.endswith(".fits") or
                self.eta_value.endswith(".fits.gz")):
            try:
                _ = float(self.eta_value)
            except ValueError as error:
                raise ExpectedFluxError(
                    "Wrong argument 'eta value'. Expected a fits file or "
                    f"a float. Found {self.eta_value}") from error
