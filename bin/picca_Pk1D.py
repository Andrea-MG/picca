#!/usr/bin/env python
"""Compute the 1D power spectrum
"""
import sys
import argparse
import glob
from array import array
import numpy as np
import fitsio

from picca import constants
from picca.data import Delta
from picca.pk1d import (compute_correction_reso, compute_correction_reso_matrix, compute_pk_noise,
                        compute_pk_raw, fill_masked_pixels, rebin_diff_noise,
                        split_forest)
from picca.utils import userprint

def main(cmdargs):
    # pylint: disable-msg=too-many-locals,too-many-branches,too-many-statements
    """Compute the 1D power spectrum"""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='Compute the 1D power spectrum')

    parser.add_argument('--out-dir',
                        type=str,
                        default=None,
                        required=True,
                        help='Output directory')

    parser.add_argument(
        '--out-format',
        type=str,
        default='fits',
        required=False,
        help='Output format: ascii or fits')

    parser.add_argument('--in-dir',
                        type=str,
                        default=None,
                        required=True,
                        help='Directory to delta files')

    parser.add_argument(
        '--in-format',
        type=str,
        default='fits',
        required=False,
        help=' Input format used for input files: ascii or fits')

    parser.add_argument('--SNR-min',
                        type=float,
                        default=2.,
                        required=False,
                        help='Minimal mean SNR per pixel ')

    parser.add_argument('--reso-max',
                        type=float,
                        default=85.,
                        required=False,
                        help='Maximal resolution in km/s ')

    parser.add_argument('--lambda-obs-min',
                        type=float,
                        default=3600.,
                        required=False,
                        help='Lower limit on observed wavelength [Angstrom]')

    parser.add_argument('--nb-part',
                        type=int,
                        default=3,
                        required=False,
                        help='Number of parts in forest')

    parser.add_argument('--nb-pixel-min',
                        type=int,
                        default=75,
                        required=False,
                        help='Minimal number of pixels in a part of forest')

    parser.add_argument(
        '--nb-pixel-masked-max',
        type=int,
        default=40,
        required=False,
        help='Maximal number of masked pixels in a part of forest')

    parser.add_argument('--no-apply-filling',
                        action='store_true',
                        default=False,
                        required=False,
                        help='Dont fill masked pixels')

    parser.add_argument(
        '--noise-estimate',
        type=str,
        default='mean_diff',
        required=False,
        help=('Estimate of Pk_noise '
              'pipeline/diff/mean_diff/rebin_diff/mean_rebin_diff'))

    parser.add_argument('--forest-type',
                        type=str,
                        default='Lya',
                        required=False,
                        help='Forest used: Lya, SiIV, CIV')

    parser.add_argument(
        '--abs-igm',
        type=str,
        default='LYA',
        required=False,
        help=('Name of the absorption line in picca.constants defining the '
              'redshift of the forest pixels'))

    #additional options
    parser.add_argument(
        '--num-noise-exp',
        default = 10,
        type=int,
        required=False,
        help='number of pipeline noise realizations to generate per spectrum')
    
    parser.add_argument(
        '--disable-reso_matrix',
        default = False,
        action='store_true',
        required=False,
        help=('do not use the resolution matrix even '
              'if it exists and we are on linear binning'))


    #use resolution matrix automatically when doing linear binning and resolution matrix is available, else use Gaussian (which was the previous default)


    args = parser.parse_args(cmdargs)
    
    # Read deltas
    if args.in_format == 'fits':
        files = sorted(glob.glob(args.in_dir + "/*.fits.gz"))
    elif args.in_format == 'ascii':
        files = sorted(glob.glob(args.in_dir + "/*.txt"))

    num_data = 0

    # initialize randoms
    np.random.seed(4)

    # loop over input files
    for index, file in enumerate(files):
        if index % 1 == 0:
            userprint("\rread {} of {} {}".format(index, len(files), num_data),
                      end="")

        # read fits or ascii file
        if args.in_format == 'fits':
            hdul = fitsio.FITS(file)
            deltas = [
                Delta.from_fitsio(hdu, pk1d_type=True) for hdu in hdul[1:]
            ]
        elif args.in_format == 'ascii':
            ascii_file = open(file, 'r')
            deltas = [Delta.from_ascii(line) for line in ascii_file]

        #add the check for linear binning on first spectrum of first file only
        if index==0:
            delta = deltas[0]
            diff_lambda = np.diff(10**delta.log_lambda)
            diff_log_lambda = np.diff(delta.log_lambda)
            q25_lambda, q75_lambda = np.percentile(diff_lambda,[25,75])
            q25_log_lambda, q75_log_lambda = np.percentile(diff_lambda,[25,75])
            if (q75_lambda-q25_lambda)<1e-6:
                #we can assume linear binning for this case
                linear_binning=True
                delta_lambda = np.median(diff_lambda)
            elif (q75_log_lambda-q25_log_lambda)<1e-6:
                #we can assume log_linear binning for this case
                linear_binning=False
                delta_log_lambda = np.median(diff_log_lambda)
            else:
                raise ValueError("Could not figure out if linear or log wavelength binning was used")


        num_data += len(deltas)
        userprint("\n ndata =  ", num_data)
        results = None

        # loop over deltas
        for delta in deltas:

            # Selection over the SNR and the resolution
            if (delta.mean_snr <= args.SNR_min or
                    delta.mean_reso >= args.reso_max):
                continue

            # first pixel in forest
            selected_pixels = 10**delta.log_lambda > args.lambda_obs_min
            first_pixel_index = (np.argmax(selected_pixels)
                                 if np.any(selected_pixels) else len(selected_pixels))

            # minimum number of pixel in forest
            min_num_pixels = args.nb_pixel_min
            if (len(delta.log_lambda) - first_pixel_index) < min_num_pixels:
                continue

            # Split in n parts the forest
            max_num_parts = (len(delta.log_lambda) -
                             first_pixel_index) // min_num_pixels
            num_parts = min(args.nb_part, max_num_parts)
            (mean_z_array, log_lambda_array, delta_array, exposures_diff_array,
             ivar_array) = split_forest(num_parts, delta.delta_log_lambda,
                                        delta.log_lambda, delta.delta,
                                        delta.exposures_diff, delta.ivar,
                                        first_pixel_index)
            for index2 in range(num_parts):

                # rebin exposures_diff spectrum
                if (args.noise_estimate == 'rebin_diff' or
                        args.noise_estimate == 'mean_rebin_diff'):
                    exposures_diff_array[index2] = rebin_diff_noise(
                        delta.delta_log_lambda, log_lambda_array[index2],
                        exposures_diff_array[index2])

                # Fill masked pixels with 0.
                (log_lambda_new, delta_new, exposures_diff_new, ivar_new,
                 num_masked_pixels) = fill_masked_pixels(
                     delta.delta_log_lambda, log_lambda_array[index2],
                     delta_array[index2], exposures_diff_array[index2],
                     ivar_array[index2], args.no_apply_filling)
                if num_masked_pixels > args.nb_pixel_masked_max:
                    continue
                
                # Compute pk_raw
                k, pk_raw = compute_pk_raw(delta.delta_log_lambda, delta_new)

                # Compute pk_noise
                run_noise = False
                if args.noise_estimate == 'pipeline':
                    run_noise = True
                pk_noise, pk_diff = compute_pk_noise(delta.delta_log_lambda,
                                                     ivar_new,
                                                     exposures_diff_new,
                                                     run_noise)

                # Compute resolution correction
                delta_pixel = (delta.delta_log_lambda * np.log(10.) *
                               constants.speed_light / 1000.)
                correction_reso = compute_correction_reso(
                    delta_pixel, delta.mean_reso, k)

                # Compute 1D Pk
                if args.noise_estimate == 'pipeline':
                    pk = (pk_raw - pk_noise) / correction_reso
                elif (args.noise_estimate == 'diff' or
                      args.noise_estimate == 'rebin_diff'):
                    pk = (pk_raw - pk_diff) / correction_reso
                elif (args.noise_estimate == 'mean_diff' or
                      args.noise_estimate == 'mean_rebin_diff'):
                    selection = (k > 0) & (k < 0.02)
                    if args.noise_estimate == 'mean_rebin_diff':
                        selection = (k > 0.003) & (k < 0.02)
                    mean_pk_diff = (sum(pk_diff[selection]) /
                                    float(len(pk_diff[selection])))
                    pk = (pk_raw - mean_pk_diff) / correction_reso

                # save in fits format
                if args.out_format == 'fits':
                    header = [{
                        'name': 'RA',
                        'value': delta.ra,
                        'comment': "QSO's Right Ascension [degrees]"
                    }, {
                        'name': 'DEC',
                        'value': delta.dec,
                        'comment': "QSO's Declination [degrees]"
                    }, {
                        'name': 'Z',
                        'value': delta.z_qso,
                        'comment': "QSO's redshift"
                    }, {
                        'name': 'MEANZ',
                        'value': mean_z_array[index2],
                        'comment': "Absorbers mean redshift"
                    }, {
                        'name': 'MEANRESO',
                        'value': delta.mean_reso,
                        'comment': 'Mean resolution [km/s]'
                    }, {
                        'name': 'MEANSNR',
                        'value': delta.mean_snr,
                        'comment': 'Mean signal to noise ratio'
                    }, {
                        'name': 'NBMASKPIX',
                        'value': num_masked_pixels,
                        'comment': 'Number of masked pixels in the section'
                    }, {
                        'name': 'PLATE',
                        'value': delta.plate,
                        'comment': "Spectrum's plate id"
                    }, {
                        'name':
                            'MJD',
                        'value':
                            delta.mjd,
                        'comment': ('Modified Julian Date,date the spectrum '
                                    'was taken')
                    }, {
                        'name': 'FIBER',
                        'value': delta.fiberid,
                        'comment': "Spectrum's fiber number"
                    }]

                    cols = [k, pk_raw, pk_noise, pk_diff, correction_reso, pk]
                    names = [
                        'k', 'Pk_raw', 'Pk_noise', 'Pk_diff', 'cor_reso', 'Pk'
                    ]
                    comments = [
                        'Wavenumber', 'Raw power spectrum',
                        "Noise's power spectrum",
                        'Noise coadd difference power spectrum',
                        'Correction resolution function',
                        'Corrected power spectrum (resolution and noise)'
                    ]
                    units = [
                        '(km/s)^-1', 'km/s', 'km/s', 'km/s', 'km/s', 'km/s'
                    ]

                    try:
                        results.write(cols,
                                      names=names,
                                      header=header,
                                      comments=comments,
                                      units=units)
                    except AttributeError:
                        results = fitsio.FITS(
                            (args.out_dir + '/Pk1D-' + str(index) + '.fits.gz'),
                            'rw',
                            clobber=True)
                        results.write(cols,
                                      names=names,
                                      header=header,
                                      comment=comments,
                                      units=units)
        if (args.out_format == 'fits' and results is not None):
            results.close()

    userprint("all done ")


if __name__ == '__main__':
    cmdargs=sys.argv[1:]
    main(cmdargs)
