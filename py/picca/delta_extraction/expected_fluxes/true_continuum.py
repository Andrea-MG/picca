"""This module defines the class TrueContinuum"""
import logging
import multiprocessing

import fitsio
import numpy as np
from scipy.interpolate import interp1d
import healpy
from pkg_resources import resource_filename

from picca.delta_extraction.astronomical_objects.forest import Forest
from picca.delta_extraction.astronomical_objects.pk1d_forest import Pk1dForest
from picca.delta_extraction.errors import ExpectedFluxError, AstronomicalObjectError
from picca.delta_extraction.expected_flux import ExpectedFlux
from picca.delta_extraction.utils import find_bins

accepted_options = [
    "input directory", "iter out prefix", "num processors", "out dir",
    "raw statistics file", "use constant weight"
]

defaults = {
    "iter out prefix": "delta_attributes",
    "raw statistics file": "",
    "use constant weight": False,
}


class TrueContinuum(ExpectedFlux):
    """Class to compute the expected flux using the true unabsorbed contiuum
    for mocks.
    It uses var_lss pre-computed from mocks and the mean flux modeled from a
    2nd order polinomial in effective optical depth.

    Methods
    -------
    (see ExpectedFlux in py/picca/delta_extraction/expected_flux.py)
    __init__
    __parse_config
    compute_expected_flux
    compute_mean_cont
    populate_los_ids
    read_true_continuum
    read_raw_statistics
    save_iteration_step

    Attributes
    ----------
    (see ExpectedFlux in py/picca/delta_extraction/expected_flux.py)

    get_mean_cont: scipy.interpolate.interp1d
    Interpolation function to compute the unabsorbed mean quasar continua.

    get_mean_cont_weight: scipy.interpolate.interp1d
    Interpolation function to compute the weights associated with the unabsorbed
    mean quasar continua.

    get_var_lss: scipy.interpolate.interp1d
    Interpolation function to compute mapping functions var_lss. See equation 4 of
    du Mas des Bourboux et al. 2020 for details. Data for interpolation is read from a file.

    input_directory: str
    Directory where true continum data is store

    iter_out_prefix: str
    Prefix of the iteration files. These file contain the statistical properties
    of deltas at a given iteration step. Intermediate files will add
    '_iteration{num}.fits.gz' to the prefix for intermediate steps and '.fits.gz'
    for the final results.
    """

    def __init__(self, config):
        """Initialize class instance.

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class

        Raise
        -----
        ExpectedFluxError if Forest.wave_solution is not 'lin' or 'log'
        """
        self.logger = logging.getLogger(__name__)
        super().__init__(config)

        # load variables from config
        self.input_directory = None
        self.iter_out_prefix = None
        self.__parse_config(config)

        # check that Forest class variables are set
        try:
            Forest.class_variable_check()
        except AstronomicalObjectError as error:
            raise ExpectedFluxError(
                "Forest class variables need to be set "
                "before initializing variables here."
            ) from error

        # read large scale structure variance and mean flux
        self.get_var_lss = None
        self.get_mean_flux = None
        self.read_raw_statistics()

        # functions to store mean continuum and weights
        self.get_mean_cont = None
        self.get_mean_cont_weight = None


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
        self.input_directory = config.get("input directory")
        if self.input_directory is None:
            raise ExpectedFluxError(
                "Missing argument 'input directory' required "
                "by TrueContinuum")

        self.iter_out_prefix = config.get("iter out prefix")
        if self.iter_out_prefix is None:
            raise ExpectedFluxError(
                "Missing argument 'iter out prefix' required "
                "by TrueContinuum")
        if "/" in self.iter_out_prefix:
            raise ExpectedFluxError(
                "Error constructing TrueContinuum. "
                "'iter out prefix' should not incude folders. "
                f"Found: {self.iter_out_prefix}")

        self.use_constant_weight = config.getboolean("use constant weight")
        self.raw_statistics_filename = config.get("raw statistics file")

    def compute_expected_flux(self, forests):
        """

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.

        Raise
        -----
        ExpectedFluxError if Forest.wave_solution is not 'lin' or 'log'
        """
        context = multiprocessing.get_context('fork')
        pool = context.Pool(processes=self.num_processors)
        self.logger.progress(
            f"Reading continum with {self.num_processors} processors")

        forests = pool.map(self.read_true_continuum, forests)
        pool.close()

        # the might be some small changes in the var_lss compared to the read
        # values due to some smoothing of the forests
        # thus, we recompute it from the actual deltas
        self.compute_var_lss(forests)
        # note that this does not change the output deltas but might slightly
        # affect the mean continuum so we have to compute it after updating
        # var_lss
        self.compute_mean_cont(forests)

        self.save_iteration_step()

        # now loop over forests to populate los_ids
        self.populate_los_ids(forests)

    def compute_mean_cont(self, forests):
        """Compute the mean quasar continuum over the whole sample.
        Then updates the value of self.get_mean_cont to contain it

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.
        """
        mean_cont = np.zeros_like(Forest.log_lambda_rest_frame_grid)
        mean_cont_weight = np.zeros_like(Forest.log_lambda_rest_frame_grid)

        for forest in forests:
            if forest.bad_continuum_reason is not None:
                continue
            bins = find_bins(forest.log_lambda - np.log10(1 + forest.z),
                             Forest.log_lambda_rest_frame_grid,
                             Forest.wave_solution)

            if self.use_constant_weight:
                weights = np.ones_like(forest.log_lambda)
            else:
                var_lss = self.get_var_lss(forest.log_lambda)
                var_pipe = 1. / forest.ivar / forest.continuum**2
                variance = var_lss + var_pipe
                weights = 1 / variance
            cont = np.bincount(bins, weights=forest.continuum * weights)
            mean_cont[:len(cont)] += cont
            cont_weight = np.bincount(bins, weights=weights)
            mean_cont_weight[:len(cont)] += cont_weight

        w = mean_cont_weight > 0
        mean_cont[w] /= mean_cont_weight[w]
        mean_cont /= mean_cont.mean()
        log_lambda_cont = Forest.log_lambda_rest_frame_grid[w]

        self.get_mean_cont = interp1d(log_lambda_cont,
                                      mean_cont,
                                      fill_value="extrapolate")
        self.get_mean_cont_weight = interp1d(log_lambda_cont,
                                             mean_cont_weight[w],
                                             fill_value=0.0,
                                             bounds_error=False)

    def populate_los_ids(self, forests):
        """Populate the dictionary los_ids with the mean expected flux, weights,
        and inverse variance arrays for each line-of-sight.

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.
        """
        for forest in forests:
            if forest.bad_continuum_reason is not None:
                continue
            # get the variance functions
            if self.use_constant_weight:
                weights = np.ones_like(forest.log_lambda)
                mean_expected_flux = forest.continuum
            else:
                var_lss = self.get_var_lss(forest.log_lambda)

                mean_expected_flux = forest.continuum
                var_pipe = 1. / forest.ivar / forest.continuum**2
                variance = var_lss + var_pipe
                weights = 1. / variance

            forest_info = {
                "mean expected flux": mean_expected_flux,
                "weights": weights,
                "continuum": forest.continuum,}
            if isinstance(forest, Pk1dForest):
                ivar = forest.ivar * mean_expected_flux**2

                forest_info["ivar"] = ivar
            self.los_ids[forest.los_id] = forest_info

    def read_true_continuum(self, forest):
        """Read the forest continuum and insert it into

        Arguments
        ---------
        forest: Forest
        A forest instance where the continuum will be computed

        Return
        ------
        forest: Forest
        The modified forest instance

        Raise
        -----
        ExpectedFluxError if Forest.wave_solution is not 'lin' or 'log'
        """
        in_nside = 16
        healpix = healpy.ang2pix(in_nside,
                                 np.pi / 2 - forest.dec,
                                 forest.ra,
                                 nest=True)
        filename_truth = (
            f"{self.input_directory}/{healpix//100}/{healpix}/truth-{in_nside}-"
            f"{healpix}.fits")
        hdul = fitsio.FITS(filename_truth)
        header = hdul["TRUE_CONT"].read_header()
        lambda_min = header["WMIN"]
        lambda_max = header["WMAX"]
        delta_lambda = header["DWAVE"]
        lambda_ = np.arange(lambda_min, lambda_max + delta_lambda, delta_lambda)
        true_cont = hdul["TRUE_CONT"].read()
        indx = np.where(true_cont["TARGETID"] == forest.targetid)
        true_continuum = interp1d(lambda_, true_cont["TRUE_CONT"][indx])

        forest.continuum = true_continuum(10**forest.log_lambda)[0]
        forest.continuum *= self.get_mean_flux(forest.log_lambda)

        return forest

    def read_raw_statistics(self):
        """Read the LSS delta variance and mean transmitted flux from files
        written by the raw analysis
        """
        # files are only for lya so far, this will need to be updated so that
        # regions other than Lya are available

        if self.raw_statistics_filename != "":
            filename = self.raw_statistics_filename
        else:
            filename = resource_filename(
                'picca', 'delta_extraction') + '/expected_fluxes/raw_stats/'
            if Forest.wave_solution == "log":
                filename += 'colore_v9_lya_log.fits.gz'
            elif Forest.wave_solution == "lin" and np.isclose(
                    10**Forest.log_lambda_grid[1] -
                    10**Forest.log_lambda_grid[0],
                    2.4,
                    rtol=0.1):
                filename += 'colore_v9_lya_lin_2.4.fits.gz'
            elif Forest.wave_solution == "lin" and np.isclose(
                    10**Forest.log_lambda_grid[1] -
                    10**Forest.log_lambda_grid[0],
                    3.2,
                    rtol=0.1):
                filename += 'colore_v9_lya_lin_3.2.fits.gz'
            else:
                raise ExpectedFluxError(
                    "Couldn't find compatible raw satistics file. Provide a "
                    "custom one using 'raw statistics file' field."
                )
        self.logger.info(
            f'Reading raw statistics var_lss and mean_flux from file: {filename}'
        )

        try:
            hdul = fitsio.FITS(filename)
        except IOError as error:
            raise ExpectedFluxError(
                f"raw statistics file {filename} couldn't be loaded"
            ) from error

        header = hdul[1].read_header()
        if Forest.wave_solution == "log":
            pixel_step = Forest.log_lambda_grid[1] - Forest.log_lambda_grid[0]
            log_lambda_min = Forest.log_lambda_grid[0]
            log_lambda_max = Forest.log_lambda_grid[-1] - pixel_step / 2
            log_lambda_rest_min = Forest.log_lambda_rest_frame_grid[
                0] - pixel_step / 2
            log_lambda_rest_max = Forest.log_lambda_rest_frame_grid[-1]
            if (header['LINEAR'] or not np.isclose(
                    header['L_MIN'], 10**log_lambda_min, rtol=1e-3) or
                    not np.isclose(
                        header['L_MAX'], 10**log_lambda_max, rtol=1e-3) or
                    not np.isclose(
                        header['LR_MIN'], 10**log_lambda_rest_min, rtol=1e-3) or
                    not np.isclose(
                        header['LR_MAX'], 10**log_lambda_rest_max, rtol=1e-3) or
                    not np.isclose(header['DEL_LL'], pixel_step, rtol=1e-3)):
                raise ExpectedFluxError(
                    "raw statistics file pixelization scheme does not match "
                    "input pixelization scheme. "
                    "\t\tL_MIN\tL_MAX\tLR_MIN\tLR_MAX\tDEL_LL"
                    f"raw\t{header['L_MIN']}\t{header['L_MAX']}\t"
                    f"{header['LR_MIN']}\t{header['LR_MAX']}\t{header['DEL_LL']}"
                    f"input\t{log_lambda_min}\t{log_lambda_max}\t"
                    f"{log_lambda_rest_min}\t{log_lambda_rest_max}"
                    "provide a custom file in 'raw statistics file' field "
                    "matching input pixelization scheme"
                )
            log_lambda = hdul[1]['LAMBDA'][:]
        elif Forest.wave_solution == "lin":
            pixel_step = 10**Forest.log_lambda_grid[
                1] - 10**Forest.log_lambda_grid[0]
            lambda_min = 10**Forest.log_lambda_grid[0]
            lambda_max = 10**Forest.log_lambda_grid[-1] - pixel_step / 2
            lambda_rest_min = 10**Forest.log_lambda_rest_frame_grid[
                0] - pixel_step / 2
            lambda_rest_max = 10**Forest.log_lambda_rest_frame_grid[-1]
            if (not header['LINEAR'] or
                    not np.isclose(header['L_MIN'], lambda_min, rtol=1e-3) or
                    not np.isclose(header['L_MAX'], lambda_max, rtol=1e-3) or
                    not np.isclose(header['LR_MIN'], lambda_rest_min, rtol=1e-3)
                    or
                    not np.isclose(header['LR_MAX'], lambda_rest_max, rtol=1e-3)
                    or not np.isclose(header['DEL_L'],
                                      10**Forest.log_lambda_grid[1] -
                                      10**Forest.log_lambda_grid[0],
                                      rtol=1e-3)):
                raise ExpectedFluxError(
                    "raw statistics file pixelization scheme does not match "
                    "input pixelization scheme. "
                    "\t\tL_MIN\tL_MAX\tLR_MIN\tLR_MAX\tDEL_L"
                    f"raw\t{header['L_MIN']}\t{header['L_MAX']}\t"
                    f"{header['LR_MIN']}\t{header['LR_MAX']}\t{header['DEL_L']}"
                    f"input\t{lambda_min}\t{lambda_max}\t{lambda_rest_min}\t"
                    f"{lambda_rest_max} provide a custom file in 'raw "
                    "statistics file' field matching input pixelization scheme"
                )
            log_lambda = np.log10(hdul[1]['LAMBDA'][:])

        flux_variance = hdul[1]['VAR'][:]
        mean_flux = hdul[1]['MEANFLUX'][:]
        hdul.close()

        var_lss = flux_variance / mean_flux**2

        self.get_var_lss = interp1d(log_lambda,
                                    var_lss,
                                    fill_value='extrapolate',
                                    kind='nearest')

        self.get_mean_flux = interp1d(log_lambda,
                                      mean_flux,
                                      fill_value='extrapolate',
                                      kind='nearest')

    def compute_var_lss(self, forests):
        """Compute var lss from delta variance by substracting
        the pipeline variance from it

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas."""
        var_lss = np.zeros_like(Forest.log_lambda_grid)
        counts = np.zeros_like(Forest.log_lambda_grid)

        for forest in forests:
            log_lambda_bins = find_bins(
                forest.log_lambda,
                Forest.log_lambda_grid,
                Forest.wave_solution)
            var_pipe = 1. / forest.ivar / forest.continuum**2
            deltas = forest.flux/forest.continuum - 1
            var_lss[log_lambda_bins] += deltas**2 - var_pipe
            counts[log_lambda_bins] += 1

        w = counts > 0
        var_lss[w] /= counts[w]
        self.get_var_lss = interp1d(Forest.log_lambda_grid[w],
                                    var_lss[w],
                                    fill_value='extrapolate',
                                    kind='nearest')


    def hdu_cont(self, results):
        """Add to the results file an HDU with the continuum information

        Arguments
        ---------
        results: fitsio.FITS
        The open fits file
        """
        results.write([
            Forest.log_lambda_rest_frame_grid,
            self.get_mean_cont(Forest.log_lambda_rest_frame_grid),
            self.get_mean_cont_weight(Forest.log_lambda_rest_frame_grid),
        ],
                      names=['loglam_rest', 'mean_cont', 'weight'],
                      extname='CONT')

    def hdu_var_func(self, results):
        """Add to the results file an HDU with the variance functions

        Arguments
        ---------
        results: fitsio.FITS
        The open fits file
        """
        results.write([
            Forest.log_lambda_grid,
            self.get_var_lss(Forest.log_lambda_grid),
        ],
                      names=[
                          'loglam', 'var_lss',
                      ],
                      extname='VAR_FUNC')

    def save_iteration_step(self):
        """Save the statistical properties of deltas"""
        iter_out_file = self.iter_out_prefix + ".fits.gz"

        with fitsio.FITS(self.out_dir + iter_out_file, 'rw',
                         clobber=True) as results:
            self.hdu_var_func(results)
            self.hdu_cont(results)
