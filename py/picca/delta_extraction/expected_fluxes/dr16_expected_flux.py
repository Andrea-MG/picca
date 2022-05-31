"""This module defines the class Dr16ExpectedFlux"""
import logging
import multiprocessing

import fitsio
import iminuit
import numpy as np
from scipy.interpolate import interp1d

from picca.delta_extraction.astronomical_objects.forest import Forest
from picca.delta_extraction.astronomical_objects.pk1d_forest import Pk1dForest
from picca.delta_extraction.errors import ExpectedFluxError, AstronomicalObjectError
from picca.delta_extraction.expected_flux import ExpectedFlux
from picca.delta_extraction.least_squares.least_squares_cont_model import LeastsSquaresContModel
from picca.delta_extraction.least_squares.least_squares_var_stats import (
    LeastsSquaresVarStats, FUDGE_REF)
from picca.delta_extraction.utils import find_bins

accepted_options = [
    "iter out prefix", "limit eta", "limit var lss", "num bins variance",
    "num iterations", "num processors", "order", "out dir",
    "use constant weight", "use ivar as weight"
]

defaults = {
    "iter out prefix": "delta_attributes",
    "limit eta": (0.5, 1.5),
    "limit var lss": (0., 0.3),
    "num bins variance": 20,
    "num iterations": 5,
    "order": 1,
    "use constant weight": False,
    "use ivar as weight": False,
}

FUDGE_FIT_START = FUDGE_REF
ETA_FIT_START = 1.
VAR_LSS_FIT_START = 0.1


class Dr16ExpectedFlux(ExpectedFlux):
    """Class to the expected flux as done in the DR16 SDSS analysys
    The mean expected flux is calculated iteratively as explained in
    du Mas des Bourboux et al. (2020)

    Methods
    -------
    (see ExpectedFlux in py/picca/delta_extraction/expected_flux.py)
    __init__
    _initialize_get_eta
    _initialize_get_fudge
    _initialize_get_var_lss
    _initialize_mean_continuum_arrays
    _initialize_variance_wavelength_array
    _initialize_variance_functions
    __parse_config
    compute_continuum
    compute_delta_stack
    compute_forest_variance
    compute_mean_cont
    compute_expected_flux
    compute_var_stats
    get_continuum_model
    get_continuum_weights
    hdu_cont
    hdu_stack_deltas
    hdu_var_func
    populate_los_ids
    save_iteration_step

    Attributes
    ----------
    (see ExpectedFlux in py/picca/delta_extraction/expected_flux.py)

    continuum_fit_parameters: dict
    A dictionary containing the continuum fit parameters for each line of sight.
    Keys are the identifier for the line of sight and values are tuples with
    the best-fit zero point and slope of the linear part of the fit.

    get_eta: scipy.interpolate.interp1d
    Interpolation function to compute mapping function eta. See equation 4 of
    du Mas des Bourboux et al. 2020 for details.

    get_fudge: scipy.interpolate.interp1d
    Interpolation function to compute mapping function fudge. See equation 4 of
    du Mas des Bourboux et al. 2020 for details.

    get_mean_cont: scipy.interpolate.interp1d
    Interpolation function to compute the unabsorbed mean quasar continua.

    get_mean_cont_weight: scipy.interpolate.interp1d
    Interpolation function to compute the weights associated with the unabsorbed
    mean quasar continua.

    get_num_pixels: scipy.interpolate.interp1d
    Number of pixels used to fit for eta, var_lss and fudge.

    get_stack_delta: scipy.interpolate.interp1d
    Interpolation function to compute the mean delta (from stacking all lines of
    sight).

    get_stack_delta_weights: scipy.interpolate.interp1d
    Weights associated with get_stack_delta

    get_valid_fit: scipy.interpolate.interp1d
    True if the fit for eta, var_lss and fudge is converged, false otherwise.
    Since the fit is performed independently for eah observed wavelength,
    this is also given as a function of the observed wavelength.

    get_var_lss: scipy.interpolate.interp1d
    Interpolation function to compute mapping functions var_lss. See equation 4 of
    du Mas des Bourboux et al. 2020 for details.

    iter_out_prefix: str
    Prefix of the iteration files. These files contain the statistical properties
    of deltas at a given iteration step. Intermediate files will add
    '_iteration{num}.fits.gz' to the prefix for intermediate steps and '.fits.gz'
    for the final results.

    limit_eta: tuple of floats
    Limits on the correction factor to the contribution of the pipeline estimate
    of the instrumental noise to the variance.

    limit_var_lss: tuple of floats
    Limits on the pixel variance due to Large Scale Structure

    log_lambda_var_func_grid: array of float
    Logarithm of the wavelengths where the variance functions and
    statistics are computed.

    logger: logging.Logger
    Logger object

    num_bins_variance: int
    Number of bins to be used to compute variance functions and statistics as
    a function of wavelength.

    num_iterations: int
    Number of iterations to determine the mean continuum shape, LSS variances, etc.

    order: int
    Order of the polynomial for the continuum fit.

    use_constant_weight: boolean
    If "True", set all the delta weights to one (implemented as eta = 0,
    sigma_lss = 1, fudge = 0).

    use_ivar_as_weight: boolean
    If "True", use ivar as weights (implemented as eta = 1, sigma_lss = fudge = 0).
    """

    def __init__(self, config):
        """Initialize class instance.

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class

        Raise
        -----
        ExpectedFluxError if Forest class variables are not set
        """
        self.logger = logging.getLogger(__name__)
        super().__init__(config)

        # load variables from config
        self.iter_out_prefix = None
        self.limit_eta = None
        self.limit_var_lss = None
        self.num_bins_variance = None
        self.num_iterations = None
        self.order = None
        self.use_constant_weight = None
        self.use_ivar_as_weight = None
        self.__parse_config(config)

        # check that Forest class variables are set
        # these are required in order to initialize the arrays
        try:
            Forest.class_variable_check()
        except AstronomicalObjectError as error:
            raise ExpectedFluxError(
                "Forest class variables need to be set "
                "before initializing variables here.") from error

        # initialize mean continuum
        self.get_mean_cont = None
        self.get_mean_cont_weight = None
        self._initialize_mean_continuum_arrays()

        # initialize wavelength array for variance functions
        self.log_lambda_var_func_grid = None
        self._initialize_variance_wavelength_array()

        # initialize variance functions
        self.get_eta = None
        self.get_fudge = None
        self.get_num_pixels = None
        self.get_valid_fit = None
        self.get_var_lss = None
        self.fit_variance_functions = []
        self._initialize_variance_functions()

        self.continuum_fit_parameters = None

        self.get_stack_delta = None
        self.get_stack_delta_weights = None

    def _initialize_get_eta(self):
        """Initialiaze function get_eta"""
        # if use_ivar_as_weight is set, we fix eta=1, var_lss=0 and fudge=0
        if self.use_ivar_as_weight:
            eta = np.ones(self.num_bins_variance)
        # if use_constant_weight is set, we fix eta=0, var_lss=1, and fudge=0
        elif self.use_constant_weight:
            eta = np.zeros(self.num_bins_variance)
        # normal initialization, starting values eta=1, var_lss=0.2 , and fudge=0
        else:
            eta = np.zeros(self.num_bins_variance)
            # this bit is what is actually freeing eta for the fit
            self.fit_variance_functions.append("eta")
        self.get_eta = interp1d(self.log_lambda_var_func_grid,
                                eta,
                                fill_value='extrapolate',
                                kind='nearest')

    def _initialize_get_fudge(self):
        """Initialiaze function get_fudge"""
        # if use_ivar_as_weight is set, we fix eta=1, var_lss=0 and fudge=0
        # if use_constant_weight is set, we fix eta=0, var_lss=1, and fudge=0
        # normal initialization, starting values eta=1, var_lss=0.2 , and fudge=0
        if not self.use_ivar_as_weight and not self.use_constant_weight:
            # this bit is what is actually freeing fudge for the fit
            self.fit_variance_functions.append("fudge")
        fudge = np.zeros(self.num_bins_variance)
        self.get_fudge = interp1d(self.log_lambda_var_func_grid,
                                  fudge,
                                  fill_value='extrapolate',
                                  kind='nearest')

    def _initialize_get_var_lss(self):
        """Initialiaze function get_var_lss"""
        # if use_ivar_as_weight is set, we fix eta=1, var_lss=0 and fudge=0
        if self.use_ivar_as_weight:
            var_lss = np.zeros(self.num_bins_variance)
        # if use_constant_weight is set, we fix eta=0, var_lss=1, and fudge=0
        elif self.use_constant_weight:
            var_lss = np.ones(self.num_bins_variance)
        # normal initialization, starting values eta=1, var_lss=0.2 , and fudge=0
        else:
            var_lss = np.zeros(self.num_bins_variance) + 0.2
            # this bit is what is actually freeing var_lss for the fit
            self.fit_variance_functions.append("var_lss")
        self.get_var_lss = interp1d(self.log_lambda_var_func_grid,
                                    var_lss,
                                    fill_value='extrapolate',
                                    kind='nearest')

    def _initialize_mean_continuum_arrays(self):
        """Initialize mean continuum arrays
        The initialized arrays are:
        - self.get_mean_cont
        - self.get_mean_cont_weight
        """
        # initialize the mean quasar continuum
        # TODO: maybe we can drop this and compute first the mean quasar
        # continuum on compute_expected_flux
        self.get_mean_cont = interp1d(Forest.log_lambda_rest_frame_grid,
                                      np.ones_like(
                                          Forest.log_lambda_rest_frame_grid),
                                      fill_value="extrapolate")
        self.get_mean_cont_weight = interp1d(
            Forest.log_lambda_rest_frame_grid,
            np.zeros_like(Forest.log_lambda_rest_frame_grid),
            fill_value="extrapolate")

    def _initialize_variance_wavelength_array(self):
        """Initialize the wavelength array where variance functions will be
        computed
        The initialized arrays are:
        - self.log_lambda_var_func_grid
        """
        # initialize the variance-related variables (see equation 4 of
        # du Mas des Bourboux et al. 2020 for details on these variables)
        if Forest.wave_solution == "log":
            self.log_lambda_var_func_grid = (
                Forest.log_lambda_grid[0] +
                (np.arange(self.num_bins_variance) + .5) *
                (Forest.log_lambda_grid[-1] - Forest.log_lambda_grid[0]) /
                self.num_bins_variance)
        # TODO: this is related with the todo in check the effect of finding
        # the nearest bin in log_lambda space versus lambda space infunction
        # find_bins in utils.py. Once we understand that we can remove
        # the dependence from Forest from here too.
        elif Forest.wave_solution == "lin":
            self.log_lambda_var_func_grid = np.log10(
                10**Forest.log_lambda_grid[0] +
                (np.arange(self.num_bins_variance) + .5) *
                (10**Forest.log_lambda_grid[-1] -
                 10**Forest.log_lambda_grid[0]) / self.num_bins_variance)

        # TODO: Replace the if/else block above by something like the commented
        # block below. We need to check the impact of doing this on the final
        # deltas first (eta, var_lss and fudge will be differently sampled).
        #start of commented block
        #resize = len(Forest.log_lambda_grid)/self.num_bins_variance
        #print(resize)
        #self.log_lambda_var_func_grid = Forest.log_lambda_grid[::int(resize)]
        #end of commented block

    def _initialize_variance_functions(self):
        """Initialize variance functions
        The initialized arrays are:
        - self.get_eta
        - self.get_fudge
        - self.get_num_pixels
        - self.get_valid_fit
        - self.get_var_lss
        """
        # if use_ivar_as_weight is set, eta, var_lss and fudge will be ignored
        # print a message to inform the user
        if self.use_ivar_as_weight:
            self.logger.info(("using ivar as weights, ignoring eta, "
                              "var_lss, fudge fits"))
            valid_fit = np.ones(self.num_bins_variance, dtype=bool)
        # if use_constant_weight is set then initialize eta, var_lss, and fudge
        # with values to have constant weights
        elif self.use_constant_weight:
            self.logger.info(("using constant weights, ignoring eta, "
                              "var_lss, fudge fits"))
            valid_fit = np.ones(self.num_bins_variance, dtype=bool)
        # normal initialization: eta, var_lss, and fudge are ignored in the
        # first iteration
        else:
            valid_fit = np.zeros(self.num_bins_variance, dtype=bool)
        num_pixels = np.zeros(self.num_bins_variance)

        self._initialize_get_eta()
        self._initialize_get_var_lss()
        self._initialize_get_fudge()
        self.get_num_pixels = interp1d(self.log_lambda_var_func_grid,
                                       num_pixels,
                                       fill_value="extrapolate",
                                       kind='nearest')
        self.get_valid_fit = interp1d(self.log_lambda_var_func_grid,
                                      valid_fit,
                                      fill_value="extrapolate",
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
        self.iter_out_prefix = config.get("iter out prefix")
        if self.iter_out_prefix is None:
            raise ExpectedFluxError(
                "Missing argument 'iter out prefix' required "
                "by Dr16ExpectedFlux")
        if "/" in self.iter_out_prefix:
            raise ExpectedFluxError(
                "Error constructing Dr16ExpectedFlux. "
                "'iter out prefix' should not incude folders. "
                f"Found: {self.iter_out_prefix}")

        limit_eta_string = config.get("limit eta")
        if limit_eta_string is None:
            raise ExpectedFluxError(
                "Missing argument 'limit eta' required by Dr16ExpectedFlux")
        limit_eta = limit_eta_string.split(",")
        if limit_eta[0].startswith("(") or limit_eta[0].startswith("["):
            eta_min = float(limit_eta[0][1:])
        else:
            eta_min = float(limit_eta[0])
        if limit_eta[1].endswith(")") or limit_eta[1].endswith("]"):
            eta_max = float(limit_eta[1][:-1])
        else:
            eta_max = float(limit_eta[1])
        self.limit_eta = (eta_min, eta_max)

        limit_var_lss_string = config.get("limit var lss")
        if limit_var_lss_string is None:
            raise ExpectedFluxError(
                "Missing argument 'limit var lss' required by Dr16ExpectedFlux")
        limit_var_lss = limit_var_lss_string.split(",")
        if limit_var_lss[0].startswith("(") or limit_var_lss[0].startswith("["):
            var_lss_min = float(limit_var_lss[0][1:])
        else:
            var_lss_min = float(limit_var_lss[0])
        if limit_var_lss[1].endswith(")") or limit_var_lss[1].endswith("]"):
            var_lss_max = float(limit_var_lss[1][:-1])
        else:
            var_lss_max = float(limit_var_lss[1])
        self.limit_var_lss = (var_lss_min, var_lss_max)

        self.num_bins_variance = config.getint("num bins variance")
        if self.num_bins_variance is None:
            raise ExpectedFluxError(
                "Missing argument 'num bins variance' required by Dr16ExpectedFlux"
            )

        self.num_iterations = config.getint("num iterations")
        if self.num_iterations is None:
            raise ExpectedFluxError(
                "Missing argument 'num iterations' required by Dr16ExpectedFlux"
            )

        self.order = config.getint("order")
        if self.order is None:
            raise ExpectedFluxError(
                "Missing argument 'order' required by Dr16ExpectedFlux")

        self.use_constant_weight = config.getboolean("use constant weight")
        if self.use_constant_weight is None:
            raise ExpectedFluxError(
                "Missing argument 'use constant weight' required by Dr16ExpectedFlux"
            )
        if self.use_constant_weight:
            self.logger.warning(
                "Deprecation Warning: option 'use constant weight' is now deprecated "
                "and will be removed in future versions. Consider using class "
                "Dr16FixedEtaVarlssFudgeExpectedFlux with options 'eta = 0', "
                "'var lss = 1' and 'fudge = 0'")
            # if use_ivar_as_weight is set, we fix eta=1, var_lss=0 and fudge=0
            # if use_constant_weight is set, we fix eta=0, var_lss=1, and fudge=0

        self.use_ivar_as_weight = config.getboolean("use ivar as weight")
        if self.use_ivar_as_weight is None:
            raise ExpectedFluxError(
                "Missing argument 'use ivar as weight' required by Dr16ExpectedFlux"
            )
        if self.use_ivar_as_weight:
            self.logger.warning(
                "Deprecation Warning: option 'use ivar as weight' is now deprecated "
                "and will be removed in future versions. Consider using class "
                "Dr16FixedEtaVarlssFudgeExpectedFlux with options 'eta = 1', "
                "'var lss = 0' and 'fudge = 0'")

    def compute_continuum(self, forest):
        """Compute the forest continuum.

        Fits a model based on the mean quasar continuum and linear function
        (see equation 2 of du Mas des Bourboux et al. 2020)
        Flags the forest with bad_cont if the computation fails.

        Arguments
        ---------
        forest: Forest
        A forest instance where the continuum will be computed

        Return
        ------
        forest: Forest
        The modified forest instance
        """
        self.continuum_fit_parameters = {}

        # get mean continuum
        mean_cont = self.get_mean_cont(forest.log_lambda -
                                       np.log10(1 + forest.z))

        # add transmission correction
        # (previously computed using method add_optical_depth)
        mean_cont *= forest.transmission_correction

        mean_cont_kwargs = {"mean_cont": mean_cont}
        # TODO: This can probably be replaced by forest.log_lambda[-1] and
        # forest.log_lambda[0]
        mean_cont_kwargs["log_lambda_max"] = (
            Forest.log_lambda_rest_frame_grid[-1] + np.log10(1 + forest.z))
        mean_cont_kwargs["log_lambda_min"] = (
            Forest.log_lambda_rest_frame_grid[0] + np.log10(1 + forest.z))

        leasts_squares = LeastsSquaresContModel(
            forest=forest,
            expected_flux=self,
            mean_cont_kwargs=mean_cont_kwargs,
        )

        zero_point = (forest.flux * forest.ivar).sum() / forest.ivar.sum()
        slope = 0.0

        minimizer = iminuit.Minuit(leasts_squares,
                                   zero_point=zero_point,
                                   slope=slope)
        minimizer.errors["zero_point"] = zero_point / 2.
        minimizer.errors["slope"] = zero_point / 2.
        minimizer.errordef = 1.
        minimizer.print_level = 0
        minimizer.fixed["slope"] = self.order == 0
        minimizer.migrad()

        forest.bad_continuum_reason = None
        temp_cont_model = self.get_continuum_model(
            forest, minimizer.values["zero_point"], minimizer.values["slope"],
            **mean_cont_kwargs)
        if not minimizer.valid:
            forest.bad_continuum_reason = "minuit didn't converge"
        if np.any(temp_cont_model < 0):
            forest.bad_continuum_reason = "negative continuum"

        if forest.bad_continuum_reason is None:
            forest.continuum = temp_cont_model
            self.continuum_fit_parameters[forest.los_id] = (
                minimizer.values["zero_point"], minimizer.values["slope"])
        ## if the continuum is negative or minuit didn't converge, then
        ## set it to None
        else:
            forest.continuum = None
            self.continuum_fit_parameters[forest.los_id] = (np.nan, np.nan)

        return forest

    def compute_delta_stack(self, forests, stack_from_deltas=False):
        """Compute a stack of the delta field as a function of wavelength

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.

        stack_from_deltas: bool - default: False
        Flag to determine whether to stack from deltas or compute them
        """
        stack_delta = np.zeros_like(Forest.log_lambda_grid)
        stack_weight = np.zeros_like(Forest.log_lambda_grid)

        for forest in forests:
            if stack_from_deltas:
                delta = forest.delta
                weights = forest.weights
            else:
                # ignore forest if continuum could not be computed
                if forest.continuum is None:
                    continue
                delta = forest.flux / forest.continuum
                variance = self.compute_forest_variance(forest,
                                                        forest.continuum)
                weights = 1. / variance

            bins = find_bins(forest.log_lambda, Forest.log_lambda_grid,
                             Forest.wave_solution)
            rebin = np.bincount(bins, weights=delta * weights)
            stack_delta[:len(rebin)] += rebin
            rebin = np.bincount(bins, weights=weights)
            stack_weight[:len(rebin)] += rebin

        w = stack_weight > 0
        stack_delta[w] /= stack_weight[w]

        self.get_stack_delta = interp1d(
            Forest.log_lambda_grid[stack_weight > 0.],
            stack_delta[stack_weight > 0.],
            kind="nearest",
            fill_value="extrapolate")
        self.get_stack_delta_weights = interp1d(
            Forest.log_lambda_grid[stack_weight > 0.],
            stack_weight[stack_weight > 0.],
            kind="nearest",
            fill_value=0.0,
            bounds_error=False)

    def compute_forest_variance(self, forest, continuum):
        """Compute the forest variance following Du Mas 2020

        Arguments
        ---------
        forest: Forest
        A forest instance where the variance will be computed

        var_pipe: float
        Pipeline variances that will be used to compute the full variance
        """
        var_pipe = 1. / forest.ivar / continuum**2
        var_lss = self.get_var_lss(forest.log_lambda)
        eta = self.get_eta(forest.log_lambda)
        fudge = self.get_fudge(forest.log_lambda)
        return eta * var_pipe + var_lss + fudge / var_pipe

    # TODO: We should check if we can directly compute the mean continuum
    # in particular this means:
    # 1. check that we can use forest.continuum instead of
    #    forest.flux/forest.continuum right before `mean_cont[:len(cont)] += cont`
    # 2. check that in that case we don't need to use the new_cont
    # 3. check that this is not propagated elsewhere through self.get_mean_cont
    # If this works then:
    # 1. update this function to be essentially the same as in TrueContinuum
    #    (except for the weights)
    # 2. overload `compute_continuum_weights` in TrueContinuum to compute the
    #    correct weights
    # 3. remove method compute_mean_cont from TrueContinuum
    # 4. restore min-similarity-lines in .pylintrc back to 5
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

        # first compute <F/C> in bins. C=Cont_old*spectrum_dependent_fitting_fct
        # (and Cont_old is constant for all spectra in a bin), thus we actually
        # compute
        #    1/Cont_old * <F/spectrum_dependent_fitting_function>
        for forest in forests:
            if forest.bad_continuum_reason is not None:
                continue
            bins = find_bins(forest.log_lambda - np.log10(1 + forest.z),
                             Forest.log_lambda_rest_frame_grid,
                             Forest.wave_solution)

            weights = 1.0 / self.compute_forest_variance(
                forest, forest.continuum)
            cont = np.bincount(bins,
                               weights=forest.flux / forest.continuum * weights)
            mean_cont[:len(cont)] += cont
            cont = np.bincount(bins, weights=weights)
            mean_cont_weight[:len(cont)] += cont

        w = mean_cont_weight > 0
        mean_cont[w] /= mean_cont_weight[w]
        mean_cont /= mean_cont.mean()
        log_lambda_cont = Forest.log_lambda_rest_frame_grid[w]

        # the new mean continuum is multiplied by the previous one to recover
        # <F/spectrum_dependent_fitting_function>
        new_cont = self.get_mean_cont(log_lambda_cont) * mean_cont[w]
        self.get_mean_cont = interp1d(log_lambda_cont,
                                      new_cont,
                                      fill_value="extrapolate")
        self.get_mean_cont_weight = interp1d(log_lambda_cont,
                                             mean_cont_weight[w],
                                             fill_value=0.0,
                                             bounds_error=False)

    def compute_expected_flux(self, forests):
        """Compute the mean expected flux of the forests.
        This includes the quasar continua and the mean transimission. It is
        computed iteratively following as explained in du Mas des Bourboux et
        al. (2020)

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.
        """
        context = multiprocessing.get_context('fork')
        for iteration in range(self.num_iterations):
            self.logger.progress(
                f"Continuum fitting: starting iteration {iteration} of {self.num_iterations}"
            )
            if self.num_processors > 1:
                with context.Pool(processes=self.num_processors) as pool:
                    forests = pool.map(self.compute_continuum, forests)
            else:
                forests = [self.compute_continuum(f) for f in forests]

            if iteration < self.num_iterations - 1:
                # Compute mean continuum (stack in rest-frame)
                self.compute_mean_cont(forests)

                # Compute observer-frame mean quantities (var_lss, eta, fudge)
                if not (self.use_ivar_as_weight or self.use_constant_weight):
                    self.compute_var_stats(forests)

            # compute the mean deltas
            self.compute_delta_stack(forests)

            # Save the iteration step
            if iteration == self.num_iterations - 1:
                self.save_iteration_step(-1)
            else:
                self.save_iteration_step(iteration)

            self.logger.progress(
                f"Continuum fitting: ending iteration {iteration} of "
                f"{self.num_iterations}")

        # now loop over forests to populate los_ids
        self.populate_los_ids(forests)

    def compute_var_stats(self, forests):
        """Compute variance functions and statistics

        This function computes the statistics required to fit the mapping functions
        eta, var_lss, and fudge. It also computes the functions themselves. See
        equation 4 of du Mas des Bourboux et al. 2020 for details.

        Arguments
        ---------
        forests: List of Forest
        A list of Forest from which to compute the deltas.

        Raise
        -----
        ExpectedFluxError if wavelength solution is not valid
        """
        # initialize arrays
        if "eta" in self.fit_variance_functions:
            eta = np.zeros(self.num_bins_variance) + ETA_FIT_START
        else:
            eta = self.get_eta(self.log_lambda_var_func_grid)
        if "var_lss" in self.fit_variance_functions:
            var_lss = np.zeros(self.num_bins_variance) + VAR_LSS_FIT_START
        else:
            var_lss = self.get_var_lss(self.log_lambda_var_func_grid)
        if "fudge" in self.fit_variance_functions:
            fudge = np.zeros(self.num_bins_variance) + FUDGE_FIT_START
        else:
            fudge = self.get_fudge(self.log_lambda_var_func_grid)
        num_pixels = np.zeros(self.num_bins_variance)
        valid_fit = np.zeros(self.num_bins_variance)
        chi2_in_bin = np.zeros(self.num_bins_variance)

        # initialize the fitter class
        leasts_squares = LeastsSquaresVarStats(
            self.num_bins_variance,
            forests,
            self.log_lambda_var_func_grid,
        )

        self.logger.progress(" Mean quantities in observer-frame")
        self.logger.progress(
            " loglam    eta      var_lss  fudge    chi2     num_pix valid_fit")
        for index in range(self.num_bins_variance):
            leasts_squares.set_fit_bins(index)

            minimizer = iminuit.Minuit(leasts_squares,
                                       name=("eta", "var_lss", "fudge"),
                                       eta=eta[index],
                                       var_lss=var_lss[index],
                                       fudge=fudge[index] / FUDGE_REF)
            minimizer.errors["eta"] = 0.05
            minimizer.limits["eta"] = self.limit_eta
            minimizer.errors["var_lss"] = 0.05
            minimizer.limits["var_lss"] = self.limit_var_lss
            minimizer.errors["fudge"] = 0.05
            minimizer.limits["fudge"] = (0, None)
            minimizer.errordef = 1.
            minimizer.print_level = 0
            minimizer.fixed["eta"] = "eta" not in self.fit_variance_functions
            minimizer.fixed[
                "var_lss"] = "var_lss" not in self.fit_variance_functions
            minimizer.fixed[
                "fudge"] = "fudge" not in self.fit_variance_functions
            minimizer.migrad()

            if minimizer.valid:
                minimizer.hesse()
                eta[index] = minimizer.values["eta"]
                var_lss[index] = minimizer.values["var_lss"]
                fudge[index] = minimizer.values["fudge"] * FUDGE_REF
                valid_fit[index] = True
            else:
                eta[index] = 1.
                var_lss[index] = 0.1
                fudge[index] = 1. * FUDGE_REF
                valid_fit[index] = False
            num_pixels[index] = leasts_squares.get_num_pixels()
            chi2_in_bin[index] = minimizer.fval

            self.logger.progress(
                f" {self.log_lambda_var_func_grid[index]:.3e} "
                f"{eta[index]:.2e} {var_lss[index]:.2e} {fudge[index]:.2e} "
                f"{chi2_in_bin[index]:.2e} {num_pixels[index]:.2e} {valid_fit[index]}"
            )

        w = num_pixels > 0

        self.get_eta = interp1d(self.log_lambda_var_func_grid[w],
                                eta[w],
                                fill_value="extrapolate",
                                kind="nearest")
        self.get_var_lss = interp1d(self.log_lambda_var_func_grid[w],
                                    var_lss[w],
                                    fill_value="extrapolate",
                                    kind="nearest")
        self.get_fudge = interp1d(self.log_lambda_var_func_grid[w],
                                  fudge[w],
                                  fill_value="extrapolate",
                                  kind="nearest")
        self.get_num_pixels = interp1d(self.log_lambda_var_func_grid[w],
                                       num_pixels[w],
                                       fill_value="extrapolate",
                                       kind="nearest")
        self.get_valid_fit = interp1d(self.log_lambda_var_func_grid[w],
                                      valid_fit[w],
                                      fill_value="extrapolate",
                                      kind="nearest")

    # pylint: disable=no-self-use
    # We expect this function to be changed by some child classes
    def get_continuum_model(self, forest, zero_point, slope, **kwargs):
        """Get the model for the continuum fit

        Arguments
        ---------
        forest: Forest
        The forest instance we want the model from

        zero_point: float
        Zero point of the linear function (flux mean). Referred to as $a_q$ in
        du Mas des Bourboux et al. 2020

        slope: float
        Slope of the linear function (evolution of the flux). Referred to as
        $b_q$ in du Mas des Bourboux et al. 2020

        Keyword Arguments
        -----------------
        mean_cont: array of floats
        Mean continuum. Required.

        log_lambda_max: float
        Maximum log_lambda for this forest.

        log_lambda_min: float
        Minimum log_lambda for this forest.

        Return
        ------
        cont_model: array of float
        The continuum model
        """
        # unpack kwargs
        if "mean_cont" not in kwargs:
            raise ExpectedFluxError("Function get_cont_model requires "
                                    "'mean_cont' in the **kwargs dictionary")
        mean_cont = kwargs.get("mean_cont")
        for key in ["log_lambda_max", "log_lambda_min"]:
            if key not in kwargs:
                raise ExpectedFluxError("Function get_cont_model requires "
                                        f"'{key}' in the **kwargs dictionary")
        log_lambda_max = kwargs.get("log_lambda_max")
        log_lambda_min = kwargs.get("log_lambda_min")
        # compute continuum
        line = (slope * (forest.log_lambda - log_lambda_min) /
                (log_lambda_max - log_lambda_min) + zero_point)

        return line * mean_cont

    # pylint: disable=unused-argument
    # kwargs are passed here in case this is necessary in child classes
    def get_continuum_weights(self, forest, cont_model, **kwargs):
        """Get the continuum model weights

        Arguments
        ---------
        forest: Forest
        The forest instance we want the model from

        cont_model: array of float
        The continuum model

        Return
        ------
        weights: array of float
        The continuum model weights
        """
        # force weights=1 when use-constant-weight
        if self.use_constant_weight:
            weights = np.ones_like(forest.flux)
        else:
            variance = self.compute_forest_variance(forest, cont_model)
            weights = 1.0 / cont_model**2 / variance

        return weights

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

    def hdu_stack_deltas(self, results):
        """Add to the results file an HDU with the delta stack

        Arguments
        ---------
        results: fitsio.FITS
        The open fits file
        """
        header = {}
        header["FITORDER"] = self.order

        results.write([
            Forest.log_lambda_grid,
            self.get_stack_delta(Forest.log_lambda_grid),
            self.get_stack_delta_weights(Forest.log_lambda_grid)
        ],
                      names=['loglam', 'stack', 'weight'],
                      header=header,
                      extname='STACK_DELTAS')

    def hdu_var_func(self, results):
        """Add to the results file an HDU with the variance functions

        Arguments
        ---------
        results: fitsio.FITS
        The open fits file
        """
        results.write([
            self.log_lambda_var_func_grid,
            self.get_eta(self.log_lambda_var_func_grid),
            self.get_var_lss(self.log_lambda_var_func_grid),
            self.get_fudge(self.log_lambda_var_func_grid),
            self.get_num_pixels(self.log_lambda_var_func_grid),
            self.get_valid_fit(self.log_lambda_var_func_grid)
        ],
                      names=[
                          'loglam', 'eta', 'var_lss', 'fudge', 'num_pixels',
                          'valid_fit'
                      ],
                      extname='VAR_FUNC')

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
            # get the variance functions and statistics
            stack_delta = self.get_stack_delta(forest.log_lambda)

            mean_expected_flux = forest.continuum * stack_delta
            weights = 1.0 / self.compute_forest_variance(
                forest, mean_expected_flux)

            forest_info = {
                "mean expected flux": mean_expected_flux,
                "weights": weights,
                "continuum": forest.continuum,
            }
            if isinstance(forest, Pk1dForest):
                eta = self.get_eta(forest.log_lambda)
                ivar = forest.ivar / (eta +
                                      (eta == 0)) * (mean_expected_flux**2)

                forest_info["ivar"] = ivar
            self.los_ids[forest.los_id] = forest_info

    def save_iteration_step(self, iteration):
        """Save the statistical properties of deltas at a given iteration
        step

        Arguments
        ---------
        iteration: int
        Iteration number. -1 for final iteration
        """
        if iteration == -1:
            iter_out_file = self.iter_out_prefix + ".fits.gz"
        else:
            iter_out_file = self.iter_out_prefix + f"_iteration{iteration+1}.fits.gz"

        with fitsio.FITS(self.out_dir + iter_out_file, 'rw',
                         clobber=True) as results:
            self.hdu_stack_deltas(results)
            self.hdu_var_func(results)
            self.hdu_cont(results)
