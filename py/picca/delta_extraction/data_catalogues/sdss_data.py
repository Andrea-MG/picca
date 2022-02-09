"""This module defines the class SdssData to read SDSS data"""
import os
import logging
import time

import numpy as np
import fitsio

from picca.delta_extraction.astronomical_objects.forest import Forest
from picca.delta_extraction.astronomical_objects.sdss_forest import SdssForest
from picca.delta_extraction.astronomical_objects.sdss_pk1d_forest import SdssPk1dForest
from picca.delta_extraction.data import Data, defaults, accepted_options
from picca.delta_extraction.errors import DataError
from picca.delta_extraction.quasar_catalogues.drq_catalogue import DrqCatalogue
from picca.delta_extraction.quasar_catalogues.drq_catalogue import defaults as defaults_drq
from picca.delta_extraction.quasar_catalogues.drq_catalogue import accepted_options as accepted_options_quasar_catalogue
from picca.delta_extraction.utils_pk1d import exp_diff, spectral_resolution

accepted_options = sorted(list(set(accepted_options + accepted_options_quasar_catalogue +[
    "input directory", "mode", "lambda max", "lambda max rest frame", "lambda min",
    "lambda min rest frame", "rebin"])))

defaults.update({
    "lambda max": 5500.0,
    "lambda max rest frame": 1200.0,
    "lambda min": 3600.0,
    "lambda min rest frame": 1040.0,
    "mode": "spplate",
    "rebin": 3,
})
defaults.update(defaults_drq)

class SdssData(Data):
    """Reads the spectra from SDSS and formats its data as a list of
    Forest instances.

    Methods
    -------
    filter_forests (from Data)
    __init__
    _parse_config
    read_from_spec
    read_from_spplate

    Attributes
    ----------
    analysis_type: str (from Data)
    Selected analysis type. Current options are "BAO 3D" or "PK 1D"

    forests: list of Forest (from Data)
    A list of Forest from which to compute the deltas.

    min_num_pix: int (from Data)
    Minimum number of pixels in a forest. Forests with less pixels will be dropped.

    in_dir: str
    Directory to spectra files.

    logger: logging.Logger
    Logger object

    mode: str
    Reading mode. Currently supported reading modes are "spplate" and "spec"
    """

    def __init__(self, config):
        """Initialize class instance

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class

        Raise
        -----
        DataError if the selected reading mode is not supported
        """
        self.logger = logging.getLogger(__name__)

        super().__init__(config)

        # load variables from config
        self.input_directory = None
        self.mode = None
        self._parse_config(config)

        # load DRQ Catalogue
        catalogue = DrqCatalogue(config).catalogue

        # read data
        if self.mode == "spplate":
            self.read_from_spplate(catalogue)
        elif self.mode == "spec":
            self.read_from_spec(catalogue)
        else:
            raise DataError(f"Error reading data in SdssData. Mode {self.mode} "
                            "is not supported.")

    def _parse_config(self, config):
        """Parse the configuration options

        Arguments
        ---------
        config: configparser.SectionProxy
        Parsed options to initialize class

        Raise
        -----
        DataError upon missing required variables
        """
        # setup SdssForest class variables
        Forest.wave_solution = "log"

        rebin = config.getint("rebin")
        if rebin is None:
            raise DataError("Missing argument 'rebin' required by SdssData")
        Forest.delta_log_lambda = rebin * 1e-4

        lambda_max = config.getfloat("lambda max")
        if lambda_max is None:
            raise DataError("Missing argument 'lambda max' required by SdssData")
        Forest.log_lambda_max = np.log10(lambda_max)
        lambda_max_rest_frame = config.getfloat("lambda max rest frame")
        if lambda_max_rest_frame is None:
            raise DataError("Missing argument 'lambda max rest frame' required by SdssData")
        Forest.log_lambda_max_rest_frame = np.log10(lambda_max_rest_frame)
        lambda_min = config.getfloat("lambda min")
        if lambda_min is None:
            raise DataError("Missing argument 'lambda min' required by SdssData")
        Forest.log_lambda_min = np.log10(lambda_min)
        lambda_min_rest_frame = config.getfloat("lambda min rest frame")
        if lambda_min_rest_frame is None:
            raise DataError("Missing argument 'lambda min rest frame' required by SdssData")
        Forest.log_lambda_min_rest_frame = np.log10(lambda_min_rest_frame)

        # instance variables
        self.input_directory = config.get("input directory")
        if self.input_directory is None:
            raise DataError(
                "Missing argument 'input directory' required by SdssData")

        self.mode = config.get("mode")
        if self.mode is None:
            raise DataError("Missing argument 'mode' required by SdssData")

    def read_from_spec(self, catalogue):
        """Read the spectra and formats its data as Forest instances.

        Arguments
        ---------
        catalogue: astropy.table.Table
        Table with the DRQ catalogue
        """
        self.logger.progress(f"Reading {len(catalogue)} objects")

        forests_by_thingid = {}
        #-- Loop over unique objects
        for row in catalogue:
            thingid = row['THING_ID']
            plate = row["PLATE"]
            mjd = row["MJD"]
            fiberid = row["FIBERID"]

            filename = (f"{self.input_directory}/{plate}/spec-{plate}-{mjd}-"
                        f"{fiberid:04d}.fits")
            try:
                hdul = fitsio.FITS(filename)
            except IOError:
                self.logger.warning(f"Error reading {filename}. Ignoring file")
                continue
            self.logger.progress("Read {}".format(filename))

            log_lambda = hdul[1]["loglam"][:]
            flux = hdul[1]["flux"][:]
            ivar = hdul[1]["ivar"][:] * (hdul[1]["and_mask"][:] == 0)

            if self.analysis_type == "BAO 3D":
                forest = SdssForest(
                    **{
                        "log_lambda": log_lambda,
                        "flux": flux,
                        "ivar": ivar,
                        "thingid": thingid,
                        "ra": row["RA"],
                        "dec": row["DEC"],
                        "z": row["Z"],
                        "plate": plate,
                        "mjd": mjd,
                        "fiberid": fiberid
                    })
            elif self.analysis_type == "PK 1D":
                # compute difference between exposure
                exposures_diff = exp_diff(hdul, log_lambda)
                # compute spectral resolution
                wdisp = hdul[1]["wdisp"][:]
                reso = spectral_resolution(wdisp, True, fiberid, log_lambda)

                forest = SdssPk1dForest(
                    **{
                        "log_lambda": log_lambda,
                        "flux": flux,
                        "ivar": ivar,
                        "thingid": thingid,
                        "ra": row["RA"],
                        "dec": row["DEC"],
                        "z": row["Z"],
                        "plate": plate,
                        "mjd": mjd,
                        "fiberid": fiberid,
                        "exposures_diff": exposures_diff,
                        "reso": reso
                    })
            else:
                raise DataError(f"analysis_type = {self.analysis_type}")

            if thingid in forests_by_thingid:
                forests_by_thingid[thingid].coadd(forest)
            else:
                forests_by_thingid[thingid] = forest

        self.forests = list(forests_by_thingid.values())

    def read_from_spplate(self, catalogue):
        """Read the spectra and formats its data as Forest instances.

        Arguments
        ---------
        catalogue: astropy.table.Table
        Table with the DRQ catalogue
        """
        grouped_catalogue = catalogue.group_by(["PLATE", "MJD"])
        num_objects = catalogue["THING_ID"].size
        self.logger.progress("reading {} plates".format(
            len(grouped_catalogue.groups)))

        forests_by_thingid = {}
        num_read_total = 0
        for (plate, mjd), group in zip(grouped_catalogue.groups.keys,
                                       grouped_catalogue.groups):
            spplate = f"{self.input_directory}/{plate}/spPlate-{plate:04d}-{mjd}.fits"

            try:
                hdul = fitsio.FITS(spplate)
                header = hdul[0].read_header()
            except IOError:
                self.logger.warning(f"Error reading {spplate}. Ignoring file")
                continue

            t0 = time.time()

            coeff0 = header["COEFF0"]
            coeff1 = header["COEFF1"]

            flux = hdul[0].read()
            ivar = hdul[1].read() * (hdul[2].read() == 0)
            log_lambda = coeff0 + coeff1 * np.arange(flux.shape[1])

            self.logger.warning(f"Read log_lambda. Size: {log_lambda.size}\n"
                                f"log_lambda: {log_lambda}")

            # Loop over all objects inside this spPlate file
            # and create the SdssForest objects
            for row in group:
                thingid = row["THING_ID"]
                fiberid = row["FIBERID"]
                array_index = fiberid - 1
                if self.analysis_type == "BAO 3D":
                    forest = SdssForest(
                        **{
                            "log_lambda": log_lambda,
                            "flux": flux[array_index],
                            "ivar": ivar[array_index],
                            "thingid": thingid,
                            "ra": row["RA"],
                            "dec": row["DEC"],
                            "z": row["Z"],
                            "plate": plate,
                            "mjd": mjd,
                            "fiberid": fiberid
                        })
                elif self.analysis_type == "PK 1D":
                    # compute difference between exposure
                    exposures_diff = exp_diff(hdul, log_lambda)
                    # compute spectral resolution
                    wdisp = hdul[1]["wdisp"][:]
                    reso = spectral_resolution(wdisp, True, fiberid, log_lambda)

                    forest = SdssPk1dForest(
                        **{
                            "log_lambda": log_lambda,
                            "flux": flux[array_index],
                            "ivar": ivar[array_index],
                            "thingid": thingid,
                            "ra": row["RA"],
                            "dec": row["DEC"],
                            "z": row["Z"],
                            "plate": plate,
                            "mjd": mjd,
                            "fiberid": fiberid,
                            "exposures_diff": exposures_diff,
                            "reso": reso
                        })
                if thingid in forests_by_thingid:
                    forests_by_thingid[thingid].coadd(forest)
                else:
                    forests_by_thingid[thingid] = forest
                self.logger.debug(
                    f"{thingid} read from file {spplate} and fiberid {fiberid}"
                )

                self.logger.warning(f"Constructed Forest with log_lambda. Size: {forest.log_lambda.size}\n"
                                    f"log_lambda: {forest.log_lambda}")

            num_read = len(group)
            num_read_total += num_read
            if num_read > 0.0:
                time_read = (time.time() - t0) / num_read
            else:
                time_read = np.nan
            self.logger.progress(
                f"read {num_read} from {os.path.basename(spplate)}"
                f" in {time_read:.3f} per spec. Progress: "
                f"{num_read_total} of {num_objects}")
            hdul.close()

        self.forests = list(forests_by_thingid.values())
